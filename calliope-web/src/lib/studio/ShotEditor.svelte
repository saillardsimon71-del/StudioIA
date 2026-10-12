<script lang="ts">
	import { onMount } from 'svelte';
	import TakeGallery from './TakeGallery.svelte';
	import VisualReview from './VisualReview.svelte';
	import { studio, newRequestId, errorMessage, selectedTake, type Shot, type ShotDraft, type Take, type MediaKind, type PromptPreview } from './api';
	let { shot, projectId, onrefresh, onclose, ondirty }: { shot: Shot; projectId: number; onrefresh: () => Promise<void>; onclose: () => void; ondirty: (dirty: boolean) => void } = $props();
	let initializedId = 0;
	let draft = $state<ShotDraft>({ description: '', prompt: '', image_prompt: '', dialogue: '', duration_sec: 5, audio_mode: 'native', continue_from_previous: false });
	let saved = $state('');
	let busy = $state('');
	let error = $state('');
	let message = $state('');
	let preview = $state<PromptPreview | null>(null);
	let previewError = $state('');
	let previewLoading = $state(false);
	let nTakes = $state(1);
	let seed = $state<number | undefined>();
	let editor: HTMLElement;
	let compact = $state(false);
	let dirty = $derived(JSON.stringify(draft) !== saved);
	let chosenVideo = $derived(selectedTake(shot, 'video'));
	let chosenImage = $derived(selectedTake(shot, 'image'));
	let valid = $derived(!!draft.description.trim() && draft.duration_sec >= 1 && draft.duration_sec <= 15);
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
		draft = { description: shot.description ?? '', prompt: shot.prompt ?? '', image_prompt: shot.image_prompt ?? '', dialogue: shot.dialogue ?? '', dialogue_turns: $state.snapshot(shot.dialogue_turns ?? []), soundscape: shot.soundscape ?? '', screen_text: $state.snapshot(shot.screen_text ?? []), duration_sec: shot.duration_sec, audio_mode: shot.audio_mode, continue_from_previous: shot.continue_from_previous ?? false,
		...(shot.prompt_contract_version === 2 ? {
			action: shot.action ?? '', reaction: shot.reaction ?? '', framing: shot.framing ?? '',
			camera: shot.camera ?? '', blocking: shot.blocking ?? '', cut_reason: shot.cut_reason ?? '',
			prop_keys: $state.snapshot(shot.prop_keys ?? []), start_state: $state.snapshot(shot.start_state ?? {}),
			end_state: $state.snapshot(shot.end_state ?? {})
		} : {}) };
		saved = JSON.stringify(draft);
		preview = null; previewError = '';
		error = ''; message = '';
	});
	$effect(() => ondirty(dirty));

	async function persist() {
		if (!dirty) return;
		if (!valid) throw new Error('Renseignez le titre du plan et une durée utile de 1 à 15 secondes.');
		const snapshot = { ...draft };
		await studio.saveShot(projectId, shot.id, snapshot);
		saved = JSON.stringify(snapshot);
		preview = null;
	}
	async function loadPreview() {
		if (dirty || previewLoading) return;
		const id = shot.id;
		previewLoading = true; previewError = '';
		try { const value = await studio.promptPreview(projectId, id); if (shot.id === id) preview = value; }
		catch (err) { if (shot.id === id) previewError = errorMessage(err); }
		finally { previewLoading = false; }
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
	function addLabel() { draft.screen_text = [...(draft.screen_text ?? []), { key: '', text: '', value: null, entity_key: '', x: 0.5, y: 0.1, anchor: 'character', anchor_box: [0.1, 0.1, 0.3, 0.5], event: 'none' }]; }
	function review(take: Take, frameIndex: number, targetShotId?: number) { run('review', async () => { await persist(); await studio.reviewVisuals(take.id, true, frameIndex, targetShotId); message = "Identité et zones approuvées sur cette prise exacte."; }); }
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
			{#if shot.prompt_contract_version === 2}
				<details><summary>Actions et mise en scène</summary><div class="stack">
					<label>Action avant la parole<textarea bind:value={draft.action} rows="2"></textarea></label>
					<label>Action finale après les répliques<textarea bind:value={draft.reaction} rows="2"></textarea></label>
					<label>Cadrage<textarea bind:value={draft.framing} rows="2"></textarea></label>
					<label>Caméra<textarea bind:value={draft.camera} rows="2"></textarea></label>
					<label>Placements et regards<textarea bind:value={draft.blocking} rows="2"></textarea></label>
					<label>Raison de la coupe<textarea bind:value={draft.cut_reason} rows="2"></textarea></label>
				</div></details>
			{/if}
			{#if draft.dialogue_turns?.length}
				{#each draft.dialogue_turns as turn, index}
					<label>Réplique {index + 1} - {turn.speaker}<textarea bind:value={turn.text} rows="2"></textarea></label>
				{/each}
			{:else}
				<label>Dialogue<textarea bind:value={draft.dialogue} rows="2" placeholder="Réplique ou parole à entendre dans ce plan."></textarea></label>
			{/if}
			<label>Ambiance du plan<textarea bind:value={draft.soundscape} rows="2" placeholder="Sons de lieu et d'action, sans paroles ni musique."></textarea></label>
			<button type="button" disabled={!!busy} onclick={addLabel}>Ajouter une valeur ou un texte exact</button>
			{#each draft.screen_text ?? [] as label, labelIndex}
				<label>Clé stable du texte<input bind:value={label.key} /></label>
				<label>Personnage associé<select bind:value={label.entity_key}><option value="">Texte indépendant</option>{#each shot.character_keys ?? [] as key}<option value={key}>{key}</option>{/each}</select></label>
				{#if label.value !== undefined && label.value !== null}
					<label>Valeur exacte - {label.key}<input type="number" step="1" bind:value={label.value} /></label>
				{:else}
					<label>Valeur entière exacte<input type="number" step="1" bind:value={label.value} /></label>
					<label>Texte exact - {label.key}<input bind:value={label.text} /></label>
				{/if}
				<details><summary>Position du texte</summary><div class="columns">
					<label>Horizontal<input type="number" min="0" max="1" step="0.01" bind:value={label.x} /></label>
					<label>Vertical<input type="number" min="0" max="1" step="0.01" bind:value={label.y} /></label>
				</div><label>Ancrage<select bind:value={label.anchor}>
					<option value="screen">Position fixe dans l'image</option>
					<option value="character" disabled={!label.anchor_box}>Suivre le personnage</option>
				</select></label>
				{#if label.anchor === 'character' && label.anchor_box}
					<div class="columns">
						<label>Zone de suivi - gauche<input type="number" min="0" max="1" step="0.01" bind:value={label.anchor_box[0]} /></label>
						<label>Haut<input type="number" min="0" max="1" step="0.01" bind:value={label.anchor_box[1]} /></label>
						<label>Largeur<input type="number" min="0.01" max="1" step="0.01" bind:value={label.anchor_box[2]} /></label>
						<label>Hauteur<input type="number" min="0.01" max="1" step="0.01" bind:value={label.anchor_box[3]} /></label>
					</div>
				{/if}
				<p class="hint">0 correspond au bord gauche ou haut, 1 au bord droit ou bas. La zone de suivi doit entourer uniquement le personnage concerné.</p></details>
				<button type="button" onclick={() => draft.screen_text = draft.screen_text?.filter((_, index) => index !== labelIndex)}>Retirer ce texte</button>
			{/each}
			<div class="columns"><label>Durée utile (secondes)<input type="number" min="1" max="15" step="0.1" required bind:value={draft.duration_sec} /></label><label>Son<select bind:value={draft.audio_mode}><option value="native">Son généré avec la vidéo</option><option value="silent">Sans son</option></select></label></div>
			<p class="hint">Le moteur rend un clip plus long que nécessaire ; le montage ne garde que la durée utile de la réplique ou de l'action, sans rallonger la prise.</p>
			{#if shot.trim_start_sec !== null || shot.trim_end_sec !== null}<p class="hint">Partie conservée au montage : {(shot.trim_start_sec ?? 0).toFixed(2)} s → {(shot.trim_end_sec ?? 0).toFixed(2)} s de la prise choisie.</p>{/if}
			<label class="check"><input type="checkbox" bind:checked={draft.continue_from_previous} />Reprendre la dernière image du plan précédent</label><p class="hint">Utilise la vidéo choisie dans le plan précédent. Le mouvement et le son restent propres à cette prise.</p>
			<div class="row"><button disabled={!dirty || !valid || !!busy} class="primary">{busy === 'save' ? 'Enregistrement...' : 'Enregistrer les changements'}</button>{#if dirty}<span class="hint">Modifications non enregistrées</span>{/if}</div>
			{#if shot.approved && dirty}<p class="hint">Ces changements retireront l'approbation. Vos prises restent conservées.</p>{/if}
		</form>
		<details ontoggle={(event) => { if ((event.currentTarget as HTMLDetailsElement).open) loadPreview(); }}>
			<summary>Voir les demandes image et vidéo</summary>
			<p class="hint">Aperçu des changements enregistrés. Aucune génération n'est lancée.</p>
			{#if dirty}<p class="hint">Enregistrez les changements pour actualiser cet aperçu.</p>{/if}
			<button disabled={dirty || previewLoading} onclick={loadPreview}>{previewLoading ? 'Chargement...' : 'Actualiser l\'aperçu'}</button>
			{#if previewError}<p class="error">{previewError}</p>{/if}
			{#if preview}
				<p class="hint">{preview.source_status === 'pending_source' ? 'Image de départ encore à choisir : demande vidéo provisoire.' : preview.source_status === 'selected_previous_video' ? 'Source : dernière image conservée du plan narratif précédent.' : 'Source : image choisie.'}</p>
				<label>Demande image<textarea readonly value={preview.image_prompt} rows="6"></textarea></label>
				<label>Demande vidéo<textarea readonly value={preview.video_prompt} rows="8"></textarea></label>
			{/if}
		</details>
		<section class="reference stack"><h3>Référence visuelle</h3>
			{#if draft.continue_from_previous && shot.continuation_source?.url}<h4>Dernière frame conservée du plan précédent</h4><VisualReview take={shot.continuation_source} labels={draft.screen_text} initialFrame={shot.continuation_frame_index ?? 0} disabled={!!busy} onreview={(frameIndex) => review(shot.continuation_source!, frameIndex, shot.id)} />{/if}
			{#if chosenImage?.url}<div class="reference-preview media"><img src={chosenImage.url} alt="Référence actuellement choisie pour le plan" /></div><p class="hint">Image choisie: prise {chosenImage.id}</p>{:else}<p class="hint">Sans référence, la vidéo part de votre prompt.</p>{/if}
			{#if chosenImage?.url}<VisualReview take={chosenImage} labels={draft.screen_text} disabled={!!busy} onreview={(frameIndex) => review(chosenImage!, frameIndex)} />{/if}
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
		<TakeGallery takes={shot.takes} kind="video" selectedId={shot.selected_video_id} busy={!!busy} muted={draft.audio_mode === 'silent'} onselect={select} />
		{#if chosenVideo?.url}<section class="stack"><h3>Identité et ancrages de la vidéo choisie</h3><VisualReview take={chosenVideo} labels={draft.screen_text} disabled={!!busy} onreview={(frameIndex) => review(chosenVideo!, frameIndex)} /></section>{/if}
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
