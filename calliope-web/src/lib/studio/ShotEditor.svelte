<script lang="ts">
	import { onMount } from 'svelte';
	import TakeGallery from './TakeGallery.svelte';
	import { studio, newRequestId, errorMessage, selectedTake, type Shot, type ShotDraft, type Take, type MediaKind } from './api';
	let { shot, projectId, onrefresh, onclose, ondirty }: { shot: Shot; projectId: number; onrefresh: () => Promise<void>; onclose: () => void; ondirty: (dirty: boolean) => void } = $props();
	let initializedId = 0;
	let draft = $state<ShotDraft>({ description: '', prompt: '', image_prompt: '', dialogue: '', duration_sec: 5, audio_mode: 'native', continue_from_previous: false });
	let saved = $state('');
	let busy = $state('');
	let error = $state('');
	let message = $state('');
	let nTakes = $state(1);
	let seed = $state<number | undefined>();
	let editor: HTMLElement;
	let compact = $state(false);
	let dirty = $derived(JSON.stringify(draft) !== saved);
	let chosenVideo = $derived(selectedTake(shot, 'video'));
	let chosenImage = $derived(selectedTake(shot, 'image'));
	let valid = $derived(!!draft.description.trim() && draft.duration_sec >= 5 && draft.duration_sec <= 15);
	onMount(() => {
		const previousFocus = document.activeElement;
		const media = window.matchMedia('(max-width: 1100px)');
		const resize = () => compact = media.matches;
		resize(); media.addEventListener('change', resize); editor.focus();
		return () => { media.removeEventListener('change', resize); if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus(); };
	});

	$effect(() => {
		if (initializedId === shot.id) return;
		initializedId = shot.id;
		draft = { description: shot.description ?? '', prompt: shot.prompt ?? '', image_prompt: shot.image_prompt ?? '', dialogue: shot.dialogue ?? '', duration_sec: shot.duration_sec, audio_mode: shot.audio_mode, continue_from_previous: shot.continue_from_previous ?? false };
		saved = JSON.stringify(draft);
		error = ''; message = '';
	});
	$effect(() => ondirty(dirty));

	async function persist() {
		if (!dirty) return;
		if (!valid) throw new Error('Renseignez le titre du plan et une durée de 5 à 15 secondes.');
		const snapshot = { ...draft };
		await studio.saveShot(projectId, shot.id, snapshot);
		saved = JSON.stringify(snapshot);
	}
	async function run(name: string, action: () => Promise<void>) {
		if (busy) return;
		busy = name; error = ''; message = '';
		try { await action(); await onrefresh(); }
		catch (err) { error = errorMessage(err); }
		finally { busy = ''; }
	}
	function save(event: SubmitEvent) { event.preventDefault(); run('save', async () => { await persist(); message = 'Le plan est enregistré.'; }); }
	function generate(kind: MediaKind) {
		run(kind, async () => {
			await persist();
			await studio.generate(shot.id, { kind, request_id: newRequestId(), n_takes: nTakes, ...(seed !== undefined && Number.isFinite(seed) ? { seed } : {}) });
			message = kind === 'video' ? 'Génération lancée. La vidéo choisie reste en place; choisissez une nouvelle prise quand elle est prête.' : 'Génération des images lancée. Choisissez ensuite votre référence.';
		});
	}
	function select(take: Take) { run('select', async () => { await persist(); await studio.select(take.id); message = take.kind === 'video' ? 'Vidéo choisie. Regardez-la, puis approuvez le plan.' : 'Référence choisie. L\'approbation du plan doit être renouvelée.'; }); }
	async function upload(event: Event, kind: MediaKind) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		await run('upload', async () => { await persist(); await studio.upload(shot.id, kind, file); message = 'Média importé. Choisissez-le dans les prises ci-dessous.'; });
		input.value = '';
	}
	function approve() { run('approve', async () => { await studio.approve(shot.id, !shot.approved); message = shot.approved ? 'Approbation retirée.' : 'Plan approuvé pour le montage.'; }); }
	function beforeUnload(event: BeforeUnloadEvent) { if (dirty) { event.preventDefault(); event.returnValue = ''; } }
	function keyboard(event: KeyboardEvent) {
		if (document.querySelector('dialog[open]')) return;
		if (event.key === 'Escape') { event.preventDefault(); onclose(); }
		if (!compact || event.key !== 'Tab') return;
		const controls = [...editor.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], summary')].filter((element) => element.getClientRects().length > 0);
		const first = controls[0]; const last = controls[controls.length - 1];
		if (event.shiftKey && (document.activeElement === first || document.activeElement === editor)) { event.preventDefault(); last?.focus(); }
		else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
	}
</script>

<svelte:window onbeforeunload={beforeUnload} onkeydown={keyboard} />
<aside bind:this={editor} class="shot-editor" aria-labelledby="editor-title" tabindex="-1" role={compact ? 'dialog' : 'complementary'} aria-modal={compact ? true : undefined}>
	<header class="editor-header between"><div><p class="hint">Plan #{shot.id}</p><h2 id="editor-title">Éditer le plan</h2></div><button class="small subtle" onclick={onclose}>Fermer</button></header>
	<div class="editor-body stack">
		{#if error}<p class="error" role="alert">{error}</p>{/if}{#if message}<p class="notice" role="status">{message}</p>{/if}
		<form onsubmit={save} class="stack">
			<label>Titre du plan<input required bind:value={draft.description} /></label>
			<label>Prompt vidéo et mouvement<textarea bind:value={draft.prompt} rows="5" placeholder="Sujet, action, cadrage, mouvement de caméra et ambiance."></textarea></label>
			<label>Dialogue<textarea bind:value={draft.dialogue} rows="2" placeholder="Réplique ou parole à entendre dans ce plan."></textarea></label>
			<div class="columns"><label>Durée demandée (secondes)<input type="number" min="5" max="15" step="0.1" required bind:value={draft.duration_sec} /></label><label>Son<select bind:value={draft.audio_mode}><option value="native">Son généré avec la vidéo</option><option value="silent">Sans son</option></select></label></div>
			<p class="hint">Le moteur produit des plans de 5 à 15 secondes. La durée réelle de chaque prise est indiquée dans ses informations.</p>
			<label class="check"><input type="checkbox" bind:checked={draft.continue_from_previous} />Reprendre la dernière image du plan précédent</label><p class="hint">Utilise la vidéo choisie dans le plan précédent. Le mouvement et le son restent propres à cette prise.</p>
			<div class="row"><button disabled={!dirty || !valid || !!busy} class="primary">{busy === 'save' ? 'Enregistrement...' : 'Enregistrer les changements'}</button>{#if dirty}<span class="hint">Modifications non enregistrées</span>{/if}</div>
			{#if shot.approved && dirty}<p class="hint">Ces changements retireront l'approbation. Vos prises restent conservées.</p>{/if}
		</form>
		<section class="reference stack"><h3>Référence visuelle</h3>
			{#if chosenImage?.url}<div class="reference-preview media"><img src={chosenImage.url} alt="Référence actuellement choisie pour le plan" /></div><p class="hint">Image choisie: prise {chosenImage.id}</p>{:else}<p class="hint">Sans référence, la vidéo part de votre prompt.</p>{/if}
			<label>Prompt de l'image<textarea bind:value={draft.image_prompt} rows="3" placeholder="Décrivez l'image de départ souhaitée."></textarea></label>
			<label>Importer une image<input type="file" accept="image/*" disabled={!!busy} onchange={(event) => upload(event, 'image')} /></label>
		</section>
		<section class="generation stack"><h3>Générer de nouvelles prises</h3>
			<div class="columns"><label>Nombre de prises<select bind:value={nTakes}>{#each [1, 2, 3, 4] as count}<option value={count}>{count}</option>{/each}</select></label><label>Graine (facultatif)<input type="number" min="0" step="1" bind:value={seed} placeholder="Aléatoire" /></label></div>
			<div class="row"><button disabled={!!busy || !valid || !draft.image_prompt.trim()} onclick={() => generate('image')}>{busy === 'image' ? 'Lancement...' : 'Générer des images'}</button><button class="primary" disabled={!!busy || !valid || !draft.prompt.trim()} onclick={() => generate('video')}>{busy === 'video' ? 'Lancement...' : shot.takes.some((take) => take.kind === 'video') ? 'Régénérer ce plan' : 'Générer la vidéo'}</button></div>
			<p class="hint">Les changements sont enregistrés avant la génération. Toutes les prises sont conservées, aucune vidéo n'est choisie automatiquement.</p>
			<label>Ou importer une vidéo<input type="file" accept="video/*" disabled={!!busy} onchange={(event) => upload(event, 'video')} /></label>
		</section>
		<TakeGallery takes={shot.takes} kind="image" selectedId={shot.selected_image_id} busy={!!busy} onselect={select} />
		<TakeGallery takes={shot.takes} kind="video" selectedId={shot.selected_video_id} busy={!!busy} onselect={select} />
		<section class="approval stack"><h3>Choix pour le montage</h3>{#if chosenVideo?.url}<p>Vidéo choisie: prise {chosenVideo.id}</p><button class:primary={!shot.approved} disabled={!!busy || dirty} onclick={approve}>{shot.approved ? 'Retirer l\'approbation' : 'Approuver ce plan'}</button>{#if dirty}<p class="hint">Enregistrez vos changements avant d'approuver.</p>{/if}{:else}<p class="hint">Choisissez une vidéo dans les prises avant d'approuver le plan.</p>{/if}</section>
	</div>
</aside>

<style>
	.shot-editor { background: var(--white); border-left: 1px solid var(--line); height: calc(100dvh - 80px); overflow-y: auto; position: sticky; top: 0; min-width: 0; }
	.editor-header { padding: 16px 22px; border-bottom: 1px solid var(--line); position: sticky; top: 0; background: var(--white); z-index: 2; }
	.editor-body { padding: 22px; gap: 28px; }
	.reference, .generation, .approval { border-top: 1px solid var(--line); padding-top: 22px; }
	.reference-preview { max-height: 220px; aspect-ratio: 16/9; border-radius: 4px; }
	@media (max-width: 1100px) { .shot-editor { position: fixed; top: 0; bottom: 0; right: 0; height: 100dvh; width: min(540px, 100%); z-index: 20; box-shadow: -20px 0 70px #142b2722; } }
</style>
