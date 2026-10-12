<script lang="ts">
	import CompareDialog from './CompareDialog.svelte';
    import RenderProgress from './RenderProgress.svelte';
	import { statusLabel, isActive, type Take, type MediaKind } from './api';
	let { takes, kind, selectedId, busy, muted = false, onselect }: { takes: Take[]; kind: MediaKind; selectedId: number | null; busy: boolean; muted?: boolean; onselect: (take: Take) => void } = $props();
	let compared = $state<number[]>([]);
	let compareOpen = $state(false);
	let items = $derived(takes.filter((take) => take.kind === kind));
	let compareTakes = $derived(items.filter((take) => compared.includes(take.id) && take.url));
	function toggle(id: number) { compared = compared.includes(id) ? compared.filter((value) => value !== id) : [...compared, id]; }
</script>

<div class="gallery">
	<div class="between"><h3>{kind === 'image' ? 'Images de référence' : 'Prises vidéo'} <span class="muted">({items.length})</span></h3><button class="small" disabled={compareTakes.length < 2} onclick={() => compareOpen = true}>Comparer ({compareTakes.length})</button></div>
	{#if !items.length}<p class="hint">{kind === 'image' ? 'Importez ou générez une image, ou créez une vidéo directement à partir du prompt.' : 'Lancez une génération ou importez une vidéo. Vous choisirez ensuite la prise à monter.'}</p>
	{:else}<div class="takes">{#each items as take (take.id)}
		<div class:selected={selectedId === take.id} class="take">
			<div class="take-preview media">
				{#if take.url && take.kind === 'image'}<img src={take.url} alt={`Image de référence, prise ${take.id}`} loading="lazy" />
				{:else if take.url}<!-- svelte-ignore a11y_media_has_caption (Les prises importées ou générées n'ont pas de sous-titres.) --><video src={take.url} {muted} controls playsinline preload="metadata"></video>
				{:else}<span class="media-empty">{statusLabel(take.status)}</span>{/if}
			</div>
			<div class="take-copy stack">
				<div class="between"><span class="take-name">Prise {take.id}</span><span class:approved={selectedId === take.id} class:pending={isActive(take.status)} class:failed={!!take.error} class="badge">{selectedId === take.id ? 'Choisie' : statusLabel(take.status)}</span></div>
				{#if take.error}<p class="take-error">{take.error}</p>{/if}
                <RenderProgress state={take.render_state} callId={take.modal_call_id} />
                {#if take.metadata.dialogue_qc}
                    {@const qc = take.metadata.dialogue_qc as { status: string; reason: string }}
                    <p class="hint">Contrôle automatique du texte : {qc.status === 'ok' ? 'réussi' : 'à vérifier'}{#if qc.reason} : {qc.reason}{/if}</p>
                    <p class="hint">Voix, prononciation et attribution à vérifier en regardant la prise.</p>
                {/if}
				{#if take.url}<div class="row"><button class="small" disabled={busy || selectedId === take.id} onclick={() => onselect(take)}>{selectedId === take.id ? 'Prise choisie' : kind === 'image' ? 'Utiliser cette image' : 'Choisir cette vidéo'}</button><label class="check hint"><input type="checkbox" checked={compared.includes(take.id)} onchange={() => toggle(take.id)} />Comparer</label></div>{/if}
				<details><summary>Informations</summary><div class="take-info"><p>{take.prompt || 'Média importé'}</p>{#if take.seed !== null}<p>Graine: {take.seed}</p>{/if}{#if take.source_take_id}<p>Image source: prise {take.source_take_id}</p>{/if}{#if take.metadata?.duration_sec}<p>Durée réelle: {String(take.metadata.duration_sec)} s</p>{/if}<p>{new Date(take.created_at).toLocaleString('fr-FR')}</p>{#if take.url}<a href={take.url} download>Télécharger la prise</a>{/if}</div></details>
			</div>
		</div>
	{/each}</div>{/if}
</div>
{#if compareOpen}<CompareDialog takes={compareTakes} {muted} onclose={() => compareOpen = false} />{/if}

<style>
	.gallery { display: grid; gap: 12px; }
	.takes { display: grid; gap: 12px; }
	.take { border: 1px solid var(--line); border-radius: 5px; overflow: hidden; background: var(--white); }
	.take.selected { border: 2px solid var(--green); }
	.take-preview { aspect-ratio: 16/9; }
	.take-copy { padding: 12px; gap: 8px; }
	.take-name { font-size: 13px; font-weight: 650; }
	.take-error { color: #873422; font-size: 13px; overflow-wrap: anywhere; }
	details { font-size: 12px; color: var(--muted); }
	.take-info { display: grid; gap: 6px; margin-top: 8px; white-space: pre-wrap; }
</style>
