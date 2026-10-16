import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const bundle = await build({ entryPoints: ['src/lib/studio/director-api.ts'],
  bundle: true, format: 'esm', platform: 'node', write: false });
const file = join(mkdtempSync(join(tmpdir(), 'studio-director-')), 'client.mjs');
writeFileSync(file, bundle.outputFiles[0].text);
const { director } = await import(pathToFileURL(file).href);
let captured;
globalThis.fetch = async (url, options) => {
  captured = { url, ...options };
  return Response.json({ enabled: true });
};
await director.revise(7, 'snapshot', [41, 43], 'Change only these shots');
assert.equal(captured.url, '/api/studio/projects/7/director/revise');
assert.deepEqual(JSON.parse(captured.body), { expected_hash: 'snapshot', shot_ids: [41, 43], instruction: 'Change only these shots' });
await director.images(7, 'snapshot', [new File(['image'], 'ep-0007-shot-000041-abc.png', { type: 'image/png' })]);
assert.equal(captured.headers, undefined, 'Browser must set the multipart boundary');
assert.equal(captured.body.get('expected_hash'), 'snapshot');
assert.equal(captured.body.getAll('files')[0].name, 'ep-0007-shot-000041-abc.png');
await director.review(7, 9, 'snapshot', true, { visual_ok: true, continuity_ok: true, speakers_ok: true, audio_ok: true });
assert.deepEqual(JSON.parse(captured.body), { expected_hash: 'snapshot', approved: true,
  visual_ok: true, continuity_ok: true, speakers_ok: true, audio_ok: true });
globalThis.fetch = async () => Response.json({ detail: 'Storyboard obsolete' }, { status: 409 });
await assert.rejects(() => director.approve(7, 'old-snapshot'), /Storyboard obsolete/);
globalThis.fetch = async () => { throw new Error('offline'); };
await assert.rejects(() => director.preflight(7), /injoignable/);
console.log('Director client checks passed: targeted revision, batch mapping, explicit QC, stale approval and offline errors.');
