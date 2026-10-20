import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const bundle = await build({ entryPoints: ['src/lib/studio/source-video-api.ts'],
  bundle: true, format: 'esm', platform: 'node', write: false });
const file = join(mkdtempSync(join(tmpdir(), 'studio-source-')), 'client.mjs');
writeFileSync(file, bundle.outputFiles[0].text);
const { sourceVideo } = await import(pathToFileURL(file).href);
let captured;
globalThis.fetch = async (url, options) => {
  captured = { url, ...options };
  return Response.json({ id: 'source', approved: false });
};
await sourceVideo.upload(30, new File(['mp4'], 'source.mp4'), 'en');
assert.equal(captured.url, '/api/studio/series/30/video-sources');
assert.equal(captured.headers, undefined);
assert.equal(captured.body.get('file').name, 'source.mp4');
assert.equal(captured.body.get('source_language'), 'en');
await sourceVideo.review(30, 'source', 'hash', [{ index: 0, transcript: 'Hello' }]);
assert.deepEqual(JSON.parse(captured.body), {
  expected_hash: 'hash', shots: [{ index: 0, transcript: 'Hello' }],
});
await sourceVideo.approve(30, 'source', 'new-hash');
assert.equal(JSON.parse(captured.body).expected_hash, 'new-hash');
await sourceVideo.cuts(30, 'source', 'new-hash', [2, 4.5]);
assert.deepEqual(JSON.parse(captured.body), { expected_hash: 'new-hash', cuts: [2, 4.5] });
globalThis.fetch = async () => Response.json({ detail: 'Analyse périmée' }, { status: 409 });
await assert.rejects(() => sourceVideo.approve(30, 'source', 'old'), /périmée/);
console.log('Source video client checks passed: upload, review, explicit approval and stale state.');
