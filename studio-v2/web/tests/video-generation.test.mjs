import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/lib/api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { api } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

function fixture(t, { fail = '', estimate = 0.2, blocked = '' } = {}) {
    const saved = { id: 'p', shots: [
        { id: 'manual', rendered_video_ref: 'import.mp4' },
        { id: 's1', rendered_video_ref: null },
        { id: 's2', rendered_video_ref: null, continue_from_previous: true },
        { id: 's3', rendered_video_ref: null },
    ] };
    const calls = [];
    let active = 0;
    const original = globalThis.fetch;
    t.after(() => { globalThis.fetch = original; });
    globalThis.fetch = async (url, options) => {
        assert.equal(++active, 1, 'calls must stay sequential');
        await new Promise((resolve) => setTimeout(resolve, 2));
        active--;
        assert.equal(options.method, 'POST');
        if (url === '/api/projects/p/prepare-render?recompile_prompts=false') {
            return Response.json({ project: structuredClone(saved), shots: saved.shots.map((shot, index) => ({
                shot_id: shot.id,
                ready_for_gpu: shot.id !== blocked && (!shot.continue_from_previous || Boolean(saved.shots[index - 1].rendered_video_ref)),
                blocking_reason: shot.id === blocked ? 'Image non validee' : null,
                cost: { estimated_cost_eur: estimate },
            })) });
        }
        const match = url.match(/^\/api\/projects\/p\/shots\/(s[123])\/render$/);
        assert.ok(match, `unexpected call: ${url}`);
        const id = match[1];
        calls.push(id);
        assert.deepEqual(JSON.parse(options.body), { authorized_by_human: true });
        if (id === 's2') assert.ok(saved.shots[1].rendered_video_ref, 'previous clip must finish before continuation');
        if (id === fail) return Response.json({ detail: 'Modal indisponible' }, { status: 502 });
        saved.shots.find((shot) => shot.id === id).rendered_video_ref = `${id}.mp4`;
        return Response.json({ project: structuredClone(saved), take: {}, qc: {} });
    };
    return { saved, calls, recover: () => { fail = ''; } };
}

test('one batch authorisation renders every missing video in order and preserves existing clips', async (t) => {
    const { saved, calls } = fixture(t);
    const progress = [];
    const stages = [];
    const result = await api.renderMissingShots(structuredClone(saved), {
        authorizedByHuman: true,
        onUpdate: (project, id, error) => {
            assert.equal(error, '');
            progress.push(project.shots.filter((shot) => shot.rendered_video_ref).length);
        },
        onStart: (id) => stages.push(id),
    });
    assert.deepEqual(calls, ['s1', 's2', 's3']);
    assert.deepEqual(stages, calls);
    assert.deepEqual(progress, [2, 3, 4]);
    assert.equal(result.shots[0].rendered_video_ref, 'import.mp4');
    assert.ok(result.shots.every((shot) => shot.rendered_video_ref));
    assert.ok(result.shots.every((shot) => !shot.human_validated), 'batch must not approve videos for the user');
});

test('a failed video stops the batch without retries and a new batch skips completed clips', async (t) => {
    const { saved, calls, recover } = fixture(t, { fail: 's2' });
    const errors = [];
    const first = await api.renderMissingShots(structuredClone(saved), {
        authorizedByHuman: true, onUpdate: (project, id, error) => { if (error) errors.push([id, error]); },
    });
    assert.deepEqual(calls, ['s1', 's2']);
    assert.deepEqual(errors, [['s2', 'Modal indisponible']]);
    assert.equal(first.shots[1].rendered_video_ref, 's1.mp4');
    assert.equal(first.shots[3].rendered_video_ref, null);
    recover();
    await api.renderMissingShots(first, { authorizedByHuman: true, onUpdate: () => {} });
    assert.deepEqual(calls, ['s1', 's2', 's2', 's3']);
});

test('missing consent never initiates a request', async (t) => {
    const original = globalThis.fetch;
    t.after(() => { globalThis.fetch = original; });
    globalThis.fetch = async () => { assert.fail('No request allowed'); };
    const project = { id: 'p', shots: [{ id: 's1', rendered_video_ref: null }] };
    await assert.rejects(api.renderMissingShots(project, { authorizedByHuman: false, onUpdate: () => {} }), /autorisation/i);
});

for (const [name, options, expected] of [
    ['unvalidated image', { blocked: 's1' }, /Image non validee/],
]) {
    test(`${name} stops before any GPU request`, async (t) => {
        const { saved, calls } = fixture(t, options);
        const errors = [];
        await api.renderMissingShots(structuredClone(saved), {
            authorizedByHuman: true, onUpdate: (project, id, error) => { if (error) errors.push([id, error]); },
        });
        assert.deepEqual(calls, []);
        assert.equal(errors[0][0], 's1');
        assert.match(errors[0][1], expected);
    });
}

for (const estimate of [null, 200]) {
    test(`authorised batch runs without a budget even when estimate is ${estimate}`, async (t) => {
        const { saved, calls } = fixture(t, { estimate });
        const errors = [];
        await api.renderMissingShots(structuredClone(saved), {
            authorizedByHuman: true, onUpdate: (project, id, error) => { if (error) errors.push(error); },
        });
        assert.deepEqual(errors, []);
        assert.deepEqual(calls, ['s1', 's2', 's3']);
    });
}

test('stop finishes the current clip and starts no further clip', async (t) => {
    const { saved, calls } = fixture(t);
    let stop = false;
    const result = await api.renderMissingShots(structuredClone(saved), {
        authorizedByHuman: true, onUpdate: () => { stop = true; }, shouldStop: () => stop,
    });
    assert.deepEqual(calls, ['s1']);
    assert.equal(result.shots[1].rendered_video_ref, 's1.mp4');
    assert.equal(result.shots[2].rendered_video_ref, null);
});

test('a clip already saved on the server is not rendered a second time from stale page state', async (t) => {
    const { saved, calls } = fixture(t);
    const stale = structuredClone(saved);
    saved.shots[1].rendered_video_ref = 'already-finished.mp4';
    await api.renderMissingShots(stale, { authorizedByHuman: true, onUpdate: () => {} });
    assert.deepEqual(calls, ['s2', 's3']);
});

test('stop requested during preparation prevents starting the next paid render', async (t) => {
    const { saved, calls } = fixture(t);
    const fakeFetch = globalThis.fetch;
    let stop = false;
    globalThis.fetch = async (...args) => {
        const response = await fakeFetch(...args);
        stop = true;
        return response;
    };
    await api.renderMissingShots(structuredClone(saved), {
        authorizedByHuman: true, onUpdate: () => {}, shouldStop: () => stop,
    });
    assert.deepEqual(calls, []);
});

test('a missing video file can be rendered again despite a saved path', async (t) => {
    const { saved, calls } = fixture(t);
    saved.shots[1].rendered_video_ref = 'missing.mp4';
    const fakeFetch = globalThis.fetch;
    globalThis.fetch = async (...args) => {
        const response = await fakeFetch(...args);
        if (args[0].includes('prepare-render')) {
            const prepared = await response.json();
            prepared.shots.forEach((report, index) => {
                report.video_available = Boolean(saved.shots[index].rendered_video_ref)
                    && saved.shots[index].rendered_video_ref !== 'missing.mp4';
            });
            return Response.json(prepared);
        }
        return response;
    };
    await api.renderMissingShots(structuredClone(saved), { authorizedByHuman: true, onUpdate: () => {} });
    assert.deepEqual(calls, ['s1', 's2', 's3']);
});
