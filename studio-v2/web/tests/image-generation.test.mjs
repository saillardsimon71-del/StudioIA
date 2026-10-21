import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/lib/api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { api } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('images run sequentially, continue after failure and retry only the missing plan', async (t) => {
    let saved = { id: 'p', shots: [
        { id: 'manual', image_ref: 'import.png' },
        { id: 's1', image_ref: null },
        { id: 's2', image_ref: null },
        { id: 's3', image_ref: null },
    ] };
    let failing = true;
    let inFlight = 0;
    const calls = [];
    const originalFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = originalFetch; });
    globalThis.fetch = async (url, options) => {
        assert.equal(options.method, 'POST');
        const match = url.match(/^\/api\/projects\/p\/shots\/(s[123])\/generate-image$/);
        assert.ok(match, `unexpected API call: ${url}`);
        assert.equal(++inFlight, 1, 'generations must not overlap');
        await new Promise((resolve) => setTimeout(resolve, 5));
        inFlight--;
        calls.push(match[1]);
        if (match[1] === 's2' && failing) {
            return Response.json({ detail: 'Meta AI indisponible' }, { status: 422 });
        }
        saved.shots.find((shot) => shot.id === match[1]).image_ref = `${match[1]}.png`;
        return Response.json(structuredClone(saved));
    };
    const progress = [];
    saved = await api.generateShotImages(structuredClone(saved), (project, id, error) => {
        progress.push({ id, error, count: project.shots.filter((shot) => shot.image_ref).length });
    });
    assert.deepEqual(calls, ['s1', 's2', 's3']);
    assert.deepEqual(progress.map(({ count }) => count), [2, 2, 3]);
    assert.equal(progress[1].error, 'Meta AI indisponible');
    assert.equal(saved.shots[0].image_ref, 'import.png');
    failing = false;
    saved = await api.generateShotImages(saved, () => {}, ['s2']);
    assert.deepEqual(calls, ['s1', 's2', 's3', 's2']);
    assert.ok(saved.shots.every((shot) => shot.image_ref));
    await api.generateShotImages(saved, () => {});
    assert.equal(calls.length, 4, 'completed plans must not be generated again');
});

test('automatic pipeline generates missing characters and decors before plans, then resumes failures', async (t) => {
    let saved = { id: 'p', characters: [{ id: 'manual', image_ref: 'import.png' }, { id: 'hero', image_ref: null }],
        decors: [{ id: 'cantine', asset_id: null }], shots: [{ id: 's1', image_ref: null }] };
    const calls = [];
    let failDecor = true;
    let inFlight = 0;
    const originalFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = originalFetch; });
    globalThis.fetch = async (url) => {
        assert.equal(++inFlight, 1);
        await new Promise((resolve) => setTimeout(resolve, 5));
        inFlight--;
        calls.push(url);
        if (url.includes('/characters/hero/')) saved.characters[1].image_ref = 'hero.png';
        else if (url.includes('/decors/cantine/')) {
            if (failDecor) return Response.json({ detail: 'Decor indisponible' }, { status: 422 });
            saved.decors[0].asset_id = 'asset-cantine';
        } else if (url.includes('/shots/s1/')) saved.shots[0].image_ref = 's1.png';
        else assert.fail(`Unexpected call ${url}`);
        return Response.json(structuredClone(saved));
    };
    const errors = [];
    const stages = [];
    saved = await api.generateProjectImages(structuredClone(saved), (updated, key, error) => {
        if (error) errors.push([key, error]);
    }, (label) => stages.push(label));
    assert.deepEqual(calls.map((url) => url.split('/').slice(4, 6).join('/')), ['characters/hero', 'decors/cantine', 'shots/s1']);
    assert.deepEqual(errors, [['decor:cantine', 'Decor indisponible']]);
    assert.ok(stages.some((label) => label.includes('hero')));
    assert.equal(saved.characters[0].image_ref, 'import.png');
    failDecor = false;
    saved = await api.generateProjectImages(saved, () => {}, () => {});
    assert.equal(calls.length, 4);
    assert.equal(saved.decors[0].asset_id, 'asset-cantine');
});

test('a Meta timeout stops the entire image batch before other references or frames', async (t) => {
    const project = { id: 'p', characters: [{ id: 'hero', image_ref: null }], decors: [{ id: 'cantine', asset_id: null }], shots: [{ id: 's1', image_ref: null }] };
    const calls = [];
    const original = globalThis.fetch;
    t.after(() => { globalThis.fetch = original; });
    globalThis.fetch = async (url) => { calls.push(url); return Response.json({ detail: 'Delai Meta AI depasse' }, { status: 504 }); };
    const errors = [];
    await api.generateProjectImages(project, (updated, key, error) => errors.push([key, error]), () => {});
    assert.equal(calls.length, 1);
    assert.deepEqual(errors, [['character:hero', 'Delai Meta AI depasse']]);
});

test('image stop finishes the current reference and does not start the next one', async (t) => {
    const project = { id: 'p', characters: [{ id: 'hero', image_ref: null }], decors: [{ id: 'cantine', asset_id: null }], shots: [{ id: 's1', image_ref: null }] };
    const original = globalThis.fetch;
    t.after(() => { globalThis.fetch = original; });
    const calls = [];
    globalThis.fetch = async (url) => {
        calls.push(url);
        return Response.json({ ...project, characters: [{ id: 'hero', image_ref: 'hero.png' }] });
    };
    let stop = false;
    const saved = await api.generateProjectImages(project, () => { stop = true; }, () => {}, () => stop);
    assert.equal(calls.length, 1);
    assert.equal(saved.characters[0].image_ref, 'hero.png');
});
