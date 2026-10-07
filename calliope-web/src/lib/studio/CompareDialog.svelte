<script lang="ts">
	import { onMount } from 'svelte';
	import type { Take } from './api';
	let { takes, onclose }: { takes: Take[]; onclose: () => void } = $props();
	let dialog: HTMLDialogElement;
	onMount(() => dialog.showModal());
</script>

<dialog bind:this={dialog} onclose={onclose} aria-labelledby="compare-title">
	<div class="dialog-header between"><h2 id="compare-title">Comparer les prises</h2><button class="subtle small" onclick={onclose}>Fermer</button></div>
	<div class="comparison dialog-body">{#each takes as take (take.id)}<figure>
		<div class="media preview">{#if take.kind === 'image' && take.url}<img src={take.url} alt={`Prise image ${take.id}`} />{:else if take.url}<!-- svelte-ignore a11y_media_has_caption (Les prises importées ou générées n'ont pas de sous-titres.) --><video src={take.url} controls playsinline preload="metadata"></video>{/if}</div>
		<details><summary>Prompt de cette prise</summary><p>{take.prompt || 'Média importé'}</p></details>
		<figcaption>Prise {take.id}{#if take.seed !== null} - Graine {take.seed}{/if}</figcaption>
	</figure>{/each}</div>
	<div class="dialog-actions row"><button onclick={onclose}>Revenir au choix</button></div>
</dialog>

<style>
	dialog { width: min(1180px, calc(100vw - 32px)); }
	.comparison { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; }
	figure { margin: 0; display: grid; gap: 10px; align-content: start; }
	.preview { aspect-ratio: 16/9; border-radius: 4px; }
	figcaption { font-size: 14px; font-weight: 600; }
	details { font-size: 13px; }
	details p { margin-top: 8px; white-space: pre-wrap; }
</style>
