import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { compileModule, parse } from 'svelte/compiler';
import { build } from 'esbuild';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const source = readFileSync(join(root, 'src/lib/studio/ShotEditor.svelte'), 'utf8');
const ast = parse(source, { modern: true });
const initialization = ast.instance.content.body
	.filter((node) => node.expression?.callee?.name === '$effect')
	.flatMap((node) => node.expression.arguments[0].body.body ?? [])
	.find((node) => node.expression?.left?.name === 'draft').expression.right;

// Execute the editor's actual initializer with the same deep proxy as the board.
const compiled = compileModule(`
	export function initialize(input, reactive) {
		const state = $state(input);
		const shot = reactive ? state : input;
		return ${source.slice(initialization.start, initialization.end)};
	}
`, { filename: 'shot-draft.svelte.js', generate: 'client', dev: false });
const bundle = await build({
	stdin: { contents: compiled.js.code, resolveDir: root, sourcefile: 'shot-draft.js' },
	bundle: true, format: 'esm', platform: 'node', write: false,
});
const file = join(mkdtempSync(join(tmpdir(), 'shot-draft-')), 'initializer.mjs');
writeFileSync(file, bundle.outputFiles[0].text);
const { initialize } = await import(pathToFileURL(file).href);

const fixture = {
	description: 'Deux personnages se répondent',
	prompt: 'Two characters face each other.',
	image_prompt: 'Two characters in a medium shot.',
	dialogue: '',
	dialogue_turns: [{ speaker: 'A', text: 'Bonjour.' }, { speaker: 'B', text: 'Salut.' }],
	soundscape: 'Street ambience.',
	screen_text: [{ key: 'status', text: '', value: 500, entity_key: 'B', anchor_box: [0.6, 0.2, 0.2, 0.5] }],
	duration_sec: 3.4,
	audio_mode: 'native',
	continue_from_previous: false,
};

for (const reactive of [false, true]) {
	const shot = structuredClone(fixture);
	const draft = initialize(shot, reactive);
	assert.deepEqual(draft, fixture);
	draft.dialogue_turns[0].text = 'Edited dialogue.';
	draft.screen_text[0].anchor_box[0] = 0.1;
	assert.deepEqual(shot, fixture, 'Editing the draft must not change the saved shot.');
}

const legacy = { description: 'Plan sans tableaux', duration_sec: 2.1, audio_mode: 'silent' };
const draft = initialize(legacy, true);
assert.equal(draft.description, legacy.description);
assert.equal(draft.duration_sec, legacy.duration_sec);
assert.deepEqual(draft.dialogue_turns, []);
assert.deepEqual(draft.screen_text, []);
console.log('Shot draft checks passed: plain and reactive shots, independent edits, legacy fields.');
