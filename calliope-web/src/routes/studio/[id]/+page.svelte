<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { beforeNavigate } from '$app/navigation';
	import ShotEditor from '$lib/studio/ShotEditor.svelte';
	import PlannerDialog from '$lib/studio/PlannerDialog.svelte';
	import SceneDialog from '$lib/studio/SceneDialog.svelte';
	import ConfigDialog from '$lib/studio/ConfigDialog.svelte';
	import JobList from '$lib/studio/JobList.svelte';
	import AudioDialog from '$lib/studio/AudioDialog.svelte';
	import ScriptDialog from '$lib/studio/ScriptDialog.svelte';
	import { studio, isActive, statusLabel, selectedTake, newRequestId, errorMessage, type Board, type Shot, type Scene } from '$lib/studio/api';
	let { data }: { data: { id: number } } = $props();
	let projectId = $derived(data.id);
	let board = $state<Board | null>(null);
	let loading = $state(true);
	let refreshPromise: Promise<void> | null = null;
	let lastProject = 0;
	let error = $state('');
	let connectionError = $state('');
	let notice = $state('');
	let busy = $state('');
	let selectedId = $state<number | null>(null);
	let editorDirty = $state(false);
	let plannerOpen = $state(false);
	let sceneOpen = $state(false);
	let sceneToEdit = $state<Scene | null>(null);
	let configOpen = $state(false);
	let audioOpen = $state(false);
	let scriptOpen = $state(false);
	let productionActive = $derived(!!board?.production && ['pending','running'].includes(board.production.status));
	let dryRun = $state(false);
	let compact = $state(false);
	let shots = $derived(board?.scenes.flatMap((scene) => scene.shots) ?? []);
	let selected = $derived(shots.find((shot) => shot.id === selectedId));
	let approvedCount = $derived(shots.filter((shot) => shot.approved && shot.selected_video_id).length);
	let exportReady = $derived(shots.length > 0 && approvedCount === shots.length);
	let pendingExport = $derived(board?.exports.some((item) => isActive(item.status)) ?? false);
	let completedExports = $derived([...(board?.exports ?? [])].filter((item) => item.url).sort((a, b) => b.id - a.id));
	let active = $derived(productionActive || (board ? board.jobs.some((job) => isActive(job.status)) || board.exports.some((item) => isActive(item.status)) || shots.some((shot) => shot.takes.some((take) => isActive(take.status))) : false));
	let duration = $derived(shots.reduce((sum, shot) => sum + shot.duration_sec, 0));
	beforeNavigate((navigation) => {
		if (navigation.from?.url.pathname === navigation.to?.url.pathname && navigation.from?.url.search === navigation.to?.url.search) return;
		if (!navigation.willUnload && editorDirty && !discardEdits()) navigation.cancel();
	});

	$effect(() => {
		const id = projectId;
		if (lastProject === id) return;
		lastProject = id;
		untrack(() => { board = null; selectedId = null; editorDirty = false; loading = true; error = ''; connectionError = ''; refresh().finally(() => loading = false); });
	});
	onMount(() => {
		const media = window.matchMedia('(max-width: 1100px)');
		const resize = () => compact = media.matches;
		resize(); media.addEventListener('change', resize);
		studio.config().then((config) => dryRun = config.dry_run).catch(() => {});
		const timer = setInterval(() => { if (active && !document.hidden) refresh(); }, 2500);
		const visible = () => { if (!document.hidden && active) refresh(); };
		document.addEventListener('visibilitychange', visible);
		return () => { clearInterval(timer); document.removeEventListener('visibilitychange', visible); media.removeEventListener('change', resize); };
	});
	async function refresh() {
		if (refreshPromise) { await refreshPromise; return refresh(); }
		const id = projectId;
		refreshPromise = (async () => {
			try {
				const value = await studio.board(id);
				if (id !== projectId) return;
				board = value;
				connectionError = '';
			} catch (err) { if (id === projectId) connectionError = errorMessage(err); }
		})();
		try { await refreshPromise; }
		finally { refreshPromise = null; }
	}
	function discardEdits() { return !editorDirty || confirm('Des changements du plan ne sont pas enregistrés. Les abandonner ?'); }
	function openShot(shot: Shot) { if (productionActive) { error = 'Mettez la production en pause pour modifier un plan.'; return; } if (selectedId === shot.id || !discardEdits()) return; editorDirty = false; selectedId = shot.id; }
	function closeShot() { if (!discardEdits()) return; selectedId = null; editorDirty = false; }
	async function action(name: string, task: () => Promise<void>) {
		if (busy) return;
		busy = name; error = ''; notice = '';
		try { await task(); await refresh(); }
		catch (err) { error = errorMessage(err); }
		finally { busy = ''; }
	}
	function addShot(scene: Scene) {
		if (!discardEdits()) return;
		action('shot', async () => { const shot = await studio.addShot(projectId, scene.id, { description: `Plan ${scene.shots.length + 1}`, prompt: '', duration_sec: 5 }); selectedId = shot.id; editorDirty = false; });
	}
	function moveScene(sceneId: number, delta: number) {
		if (!board) return;
		const ids = board.scenes.map((scene) => scene.id); const index = ids.indexOf(sceneId);
		if (index + delta < 0 || index + delta >= ids.length) return;
		[ids[index], ids[index + delta]] = [ids[index + delta], ids[index]];
		action('reorder', async () => { await studio.reorder(projectId, { scene_ids: ids }); });
	}
	function moveShot(scene: Scene, shotId: number, delta: number) {
		const ids = scene.shots.map((shot) => shot.id); const index = ids.indexOf(shotId);
		if (index + delta < 0 || index + delta >= ids.length) return;
		[ids[index], ids[index + delta]] = [ids[index + delta], ids[index]];
		action('reorder', async () => { await studio.reorder(projectId, { scene_id: scene.id, shot_ids: ids }); });
	}
	function deleteShot(shot: Shot) {
		if (!confirm(`Supprimer le plan "${shot.description}" du projet ?`)) return;
		action('delete', async () => { await studio.deleteShot(projectId, shot.id); if (selectedId === shot.id) { selectedId = null; editorDirty = false; } });
	}
	function editScene(scene: Scene | null) { sceneToEdit = scene; sceneOpen = true; }
	function shotStatus(shot: Shot) {
		if (shot.approved) return 'Approuvé';
		if (shot.selected_video_id) return 'À approuver';
		if (shot.takes.some((take) => take.kind === 'video' && take.url)) return 'Choisir une prise';
		if (shot.takes.some((take) => isActive(take.status))) return 'Rendu en cours';
		return 'À préparer';
	}
	function exportFilm() {
		if (editorDirty) { error = 'Enregistrez les changements du plan ouvert avant de lancer l\'export.'; return; }
		action('export', async () => { await studio.export(projectId, newRequestId()); notice = 'Assemblage lancé. Le MP4 sera disponible ci-dessous.'; });
	}
	function configSaved() { studio.config().then((config) => dryRun = config.dry_run).catch(() => {}); }
</script>

<svelte:head><title>{board?.project.title ?? 'Projet'} - Studio IA</title></svelte:head>
<header class="page-header between" inert={!!selected && compact}><div class="row"><a class="brand" href="/studio">Studio IA</a>{#if board?.episode}<a class="back-link" href={'/studio/series/' + board.episode.series_id}>{board.episode.series_title}</a>{:else}<a class="back-link" href="/studio/projects">Tous les projets</a>{/if}</div><div class="row"><button class="small" onclick={() => configOpen = true}>Services</button><a href="/settings">Options avancées</a></div></header>

{#if loading}<main class="loading"><p class="muted">Ouverture du projet...</p></main>
{:else if !board}<main class="loading"><p class="error" role="alert">{connectionError || error || 'Projet introuvable.'}</p><button onclick={() => refresh()}>Réessayer</button></main>
{:else}
	<div class:with-editor={!!selected} class="workspace">
		<nav class="scene-nav" aria-label="Scènes du projet" inert={!!selected && compact}><div class="project-label"><span class="badge">{board.project.aspect_ratio}</span><h2>{board.project.title}</h2><p class="hint">{shots.length} plans, environ {duration.toFixed(1).replace('.0', '')} s</p></div>
			<div class="scene-links">{#each board.scenes as scene, index (scene.id)}<a href={`#scene-${scene.id}`}><span>{index + 1}</span><div>{scene.heading}<small>{scene.shots.length} plans</small></div></a>{/each}</div>
			<button class="small" onclick={() => editScene(null)} disabled={productionActive || !!busy}>Ajouter une scène</button>
			<div class="nav-footer"><p class="hint">{approvedCount} / {shots.length} plans approuvés</p><a href="#export">Voir le montage final</a></div>
		</nav>
		<main class="board" inert={!!selected && compact}>
			{#if board.production}<section class="production-panel stack" aria-label="Production automatique"><div class="between"><div><p class="hint">Épisode {board.episode?.number} - Production automatique</p><h2>{board.production.message}</h2></div><div class="row">{#if productionActive}<button disabled={!!board.production.pause_requested || !!busy} onclick={() => action('pause',async()=>{await studio.pauseProduction(projectId);})}>{board.production.pause_requested ? 'Pause demandée' : 'Mettre en pause'}</button>{:else if board.production.status === 'paused' || board.production.status === 'failed'}<button class="primary" disabled={productionActive || !!busy} onclick={() => action('resume',async()=>{await studio.resumeProduction(projectId);})}>Reprendre la production</button>{/if}{#if board.episode}<a href={'/studio/series/' + board.episode.series_id}>Retour à la série</a>{/if}</div></div><progress max="1" value={board.production.progress}></progress>{#if board.production.error}<p class="error">{board.production.error}</p>{/if}{#if productionActive}<p class="hint">Vous pouvez fermer cette page. La production et les prises sont conservées. La pause prend effet après l'opération en cours.</p>{:else}<p class="hint">Vous pouvez modifier le scénario, les plans, les prises et le son, puis réexporter.</p>{/if}</section>{/if}
			<div class="row"><button disabled={productionActive || !shots.length} onclick={() => scriptOpen = true}>Modifier le scénario</button><button disabled={productionActive} onclick={() => audioOpen = true}>Musique et bruitages</button></div>
			{#if board.episode && board.production && ['paused', 'failed'].includes(board.production.status)}<div class="stack"><button disabled={!!busy || editorDirty || !shots.length} onclick={() => action('visual-contract', async () => { await studio.refreshVisualContract(projectId); notice = 'Contrat visuel préparé sans génération. Vérifiez les références et les zones avant de reprendre.'; })}>Préparer le contrat visuel depuis la série</button><p class="hint">Applique les identités et options actuelles à cet épisode, conserve ses prises et demande leur vérification. Les valeurs manquantes doivent être renseignées dans les plans.</p></div>{/if}
			<div class="between board-heading"><div class="stack title-stack"><h1>{board.project.title}</h1>{#if board.project.idea}<p class="muted project-idea">{board.project.idea}</p>{/if}</div><div class="row"><button class="small" disabled={productionActive || !!board.scenes.length || !!busy} onclick={() => plannerOpen = true}>Préparer les scènes</button><button class="small subtle" onclick={() => refresh()}>Actualiser</button></div></div>
			{#if dryRun}<p class="error">Simulation active: aucun média réel ne sera généré. Désactivez-la dans les services.</p>{/if}
			{#if error}<p class="error" role="alert">{error}</p>{/if}{#if notice}<p class="notice" role="status">{notice}</p>{/if}
			{#if connectionError}<p class="error" role="alert">{connectionError}</p>{/if}
			{#if !board.scenes.length && board.production}<div class="empty"><h2>L'IA prépare votre épisode.</h2><p class="muted">Le scénario, les plans et les dialogues apparaîtront ici.</p></div>
			{:else if !board.scenes.length}<div class="empty"><h2>Préparez votre premier découpage.</h2><p class="muted">Partez d'une idée avec l'IA, collez un script pour le découper localement ou ajoutez une scène.</p><div class="row"><button class="primary" onclick={() => plannerOpen = true}>Préparer les scènes</button><button onclick={() => editScene(null)}>Ajouter une scène</button></div></div>
			{:else if !productionActive}<p class="hint">Ouvrez un plan pour modifier ses images, ses prises ou son dialogue, puis réexportez.</p>{/if}
			<div class="scenes">{#each board.scenes as scene, sceneIndex (scene.id)}
				<section class="scene" id={`scene-${scene.id}`} aria-labelledby={`scene-title-${scene.id}`}>
					<div class="between scene-heading"><div class="row"><span class="scene-number">{sceneIndex + 1}</span><h2 id={`scene-title-${scene.id}`}>{scene.heading}</h2></div><div class="row"><button class="small subtle" disabled={productionActive} onclick={() => editScene(scene)}>Modifier</button><button class="small" aria-label={`Déplacer la scène ${sceneIndex + 1} vers le haut`} disabled={productionActive || !!busy || sceneIndex === 0} onclick={() => moveScene(scene.id, -1)}>Monter</button><button class="small" aria-label={`Déplacer la scène ${sceneIndex + 1} vers le bas`} disabled={productionActive || !!busy || sceneIndex === board!.scenes.length - 1} onclick={() => moveScene(scene.id, 1)}>Descendre</button></div></div>
					{#if scene.action}<p class="scene-action muted">{scene.action}</p>{/if}{#if scene.dialog}<details class="scene-dialog"><summary>Dialogue de la scène</summary><p>{scene.dialog}</p></details>{/if}
					<div class="shot-list">{#each scene.shots as shot, shotIndex (shot.id)}
						{@const video = selectedTake(shot, 'video')}{@const image = selectedTake(shot, 'image')}
						<article class:selected={selectedId === shot.id} class:approved={shot.approved} class="shot-card">
							<div class="shot-preview media" style={`aspect-ratio: ${board.project.aspect_ratio.replace(':', '/')}`}>
								{#if video?.url}<!-- svelte-ignore a11y_media_has_caption (Les prises importées ou générées n'ont pas de sous-titres.) --><video src={video.url} poster={video.poster_url ?? undefined} muted={shot.audio_mode === 'silent'} controls playsinline preload="metadata"></video>{:else if image?.url}<img src={image.url} alt={`Référence du plan ${sceneIndex + 1}.${shotIndex + 1}`} loading="lazy" />{:else}<span class="media-empty">{shot.takes.some((take) => take.kind === 'video' && take.url) ? 'Une prise est prête à choisir' : 'Aucune vidéo choisie'}</span>{/if}
							</div>
							<div class="shot-copy"><div class="row"><span class="shot-number">Plan {sceneIndex + 1}.{shotIndex + 1}</span><span class:approved={shot.approved} class:pending={!shot.approved && !!shot.selected_video_id} class="badge">{shotStatus(shot)}</span></div><h3>{shot.description || 'Plan sans titre'}</h3><p class="shot-prompt muted">{shot.prompt || 'Décrivez ce plan dans l\'éditeur.'}</p><p class="hint">{shot.duration_sec} s - {shot.audio_mode === 'silent' ? 'Sans son' : 'Son natif'} - {shot.takes.filter((take) => take.kind === 'video').length} prises vidéo{#if video} - Prise {video.id} choisie{/if}</p><button class:primary={selectedId !== shot.id} disabled={productionActive} onclick={() => openShot(shot)}>{selectedId === shot.id ? 'Plan ouvert' : 'Ouvrir le plan'}</button></div>
							<div class="shot-order"><button class="small subtle" aria-label={`Monter le plan ${sceneIndex + 1}.${shotIndex + 1}`} disabled={productionActive || !!busy || shotIndex === 0} onclick={() => moveShot(scene, shot.id, -1)}>Monter</button><button class="small subtle" aria-label={`Descendre le plan ${sceneIndex + 1}.${shotIndex + 1}`} disabled={productionActive || !!busy || shotIndex === scene.shots.length - 1} onclick={() => moveShot(scene, shot.id, 1)}>Descendre</button><button class="small subtle danger" disabled={productionActive || !!busy || scene.shots.length <= 1} onclick={() => deleteShot(shot)}>Supprimer</button></div>
						</article>
					{/each}</div>
					<button class="small add-shot" disabled={productionActive || !!busy} onclick={() => addShot(scene)}>Ajouter un plan à cette scène</button>
				</section>
			{/each}</div>
			<section id="export" class="export-section stack" aria-labelledby="export-title"><div class="between"><div class="stack title-stack"><h2 id="export-title">Montage final</h2><p class="hint">{approvedCount} / {shots.length} plans choisis et approuvés</p></div><button class="primary" disabled={productionActive || !exportReady || !!busy || pendingExport || editorDirty} onclick={exportFilm}>{busy === 'export' || pendingExport ? 'Assemblage en cours...' : 'Exporter le film MP4'}</button></div>
				{#if !exportReady}<p class="hint">Choisissez une vidéo et approuvez chaque plan pour exporter le film dans l'ordre des scènes.</p>{/if}
				{#each [...board.exports].filter((item) => !item.url && item.error).reverse() as item (item.id)}<p class="error">Export {item.id}: {item.error}</p>{/each}
				{#if completedExports.length}{@const latest = completedExports[0]}<div class="final-export stack"><div class="media final-preview" style={`aspect-ratio: ${board.project.aspect_ratio.replace(':', '/')}`}><!-- svelte-ignore a11y_media_has_caption (Le film ne dispose pas de sous-titres.) --><video src={latest.url!} controls playsinline preload="metadata"></video></div><div class="between"><div><p>Export du {new Date(latest.created_at).toLocaleString('fr-FR')}</p>{#if latest.stale}<p class="hint">Le projet a changé depuis cet export.</p>{/if}{#if latest.metadata?.duration_sec}<p class="hint">{latest.metadata.duration_sec.toFixed(1)} s{#if latest.metadata.width} - {latest.metadata.width} x {latest.metadata.height}{/if}</p>{/if}</div><a class="button primary" href={latest.url!} download={`${board.project.title}.mp4`}>Télécharger le MP4</a></div></div>
					{#if latest.metadata?.duration_warning}<p class="hint" role="status">{latest.metadata.duration_warning}</p>{/if}
					{#if completedExports.length > 1}<details><summary>Exports précédents ({completedExports.length - 1})</summary><div class="older-exports">{#each completedExports.slice(1) as item (item.id)}<div class="between"><span>{new Date(item.created_at).toLocaleString('fr-FR')}{#if item.stale} - Version précédente{/if}</span><a href={item.url!} download>Télécharger MP4</a></div>{/each}</div></details>{/if}
				{:else if !pendingExport}<p class="muted">Le film exporté apparaîtra ici, avec son lecteur et son téléchargement.</p>{/if}
			</section>
			<JobList jobs={board.jobs} {productionActive} onrefresh={refresh} />
		</main>
		{#if selected}{#key selected.id}<ShotEditor shot={selected} {projectId} onrefresh={refresh} onclose={closeShot} ondirty={(dirty) => editorDirty = dirty} />{/key}{/if}
	</div>
{/if}
{#if plannerOpen && board}<PlannerDialog project={board.project} onclose={() => plannerOpen = false} onrefresh={refresh} />{/if}
{#if sceneOpen}<SceneDialog {projectId} scene={sceneToEdit} onclose={() => sceneOpen = false} onrefresh={refresh} />{/if}
{#if configOpen}<ConfigDialog onclose={() => configOpen = false} onsaved={configSaved} />{/if}
{#if audioOpen && board}<AudioDialog {projectId} initial={board.audio} shotNames={shots.map(shot=>shot.description)} onclose={() => audioOpen = false} onrefresh={refresh} />{/if}
{#if scriptOpen && board}<ScriptDialog {board} onclose={() => scriptOpen = false} onrefresh={refresh} />{/if}

<style>
	.production-panel { border: 1px solid var(--line); border-left: 4px solid var(--green); padding: 20px; background: var(--white); }
	.production-panel progress { width: 100%; accent-color: var(--green); }
	.back-link { font-size: 13px; margin-left: 15px; }
	.loading { padding: 40px; display: grid; justify-items: start; gap: 20px; }
	.workspace { display: grid; grid-template-columns: 215px minmax(0, 1fr); align-items: start; }
	.workspace.with-editor { grid-template-columns: 200px minmax(0, 1fr) 440px; }
	.scene-nav { padding: 26px 20px; border-right: 1px solid var(--line); min-height: calc(100dvh - 80px); background: #edf0e7; position: sticky; top: 0; }
	.project-label { display: grid; justify-items: start; gap: 10px; }
	.project-label h2 { font-size: 17px; overflow-wrap: anywhere; }
	.scene-links { display: grid; margin: 24px -8px 18px; }
	.scene-links a { display: flex; gap: 12px; padding: 10px 8px; color: var(--ink); text-decoration: none; font-size: 13px; }
	.scene-links a:hover { background: #e0e7d9; border-radius: 4px; }
	.scene-links a > span { color: var(--muted); width: 16px; flex-shrink: 0; }
	.scene-links small { display: block; color: var(--muted); margin-top: 3px; }
	.nav-footer { display: grid; gap: 12px; margin-top: 28px; font-size: 12px; border-top: 1px solid var(--line); padding-top: 20px; }
	.board { padding: 28px 30px 60px; display: grid; gap: 24px; min-width: 0; }
	.board-heading { align-items: flex-start; flex-wrap: wrap; }
	.title-stack { gap: 7px; }
	.project-idea { max-width: 75ch; font-size: 14px; }
	.scenes { display: grid; gap: 36px; }
	.scene { scroll-margin-top: 16px; }
	.scene-heading { padding-bottom: 14px; border-bottom: 1px solid #b9c8b7; flex-wrap: wrap; }
	.scene-number { display: grid; place-items: center; width: 29px; height: 29px; background: var(--green); color: white; border-radius: 3px; font-weight: 650; font-size: 14px; }
	.scene-heading h2 { font-size: 19px; }
	.scene-action { margin-top: 13px; font-size: 14px; max-width: 78ch; }
	.scene-dialog { font-size: 13px; margin-top: 10px; }
	.scene-dialog p { padding-top: 8px; white-space: pre-wrap; }
	.shot-list { display: grid; gap: 12px; margin-top: 16px; }
	.shot-card { display: grid; grid-template-columns: minmax(170px, 30%) minmax(0, 1fr) 75px; background: var(--white); border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
	.shot-card.selected { outline: 2px solid var(--green); outline-offset: 1px; }
	.shot-card.approved { border-left: 4px solid var(--green); }
	.shot-preview { width: 100%; max-height: 230px; align-self: center; min-height: 130px; }
	.shot-copy { padding: 16px 18px; display: grid; justify-items: start; gap: 9px; }
	.shot-number { color: var(--muted); font-size: 12px; }
	.shot-prompt { font-size: 13px; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
	.shot-order { display: flex; flex-direction: column; justify-content: center; align-items: center; border-left: 1px solid var(--line); gap: 3px; padding: 6px; }
	.shot-order button { font-size: 11px; padding: 3px; }
	.add-shot { margin-top: 12px; }
	.export-section { margin-top: 12px; padding-top: 26px; border-top: 2px solid var(--green); scroll-margin-top: 20px; }
	.export-section > .between { flex-wrap: wrap; }
	.final-preview { border-radius: 5px; max-height: 600px; }
	.final-export { font-size: 14px; }
	.older-exports { display: grid; gap: 12px; padding-top: 14px; font-size: 13px; }
	@media (max-width: 1450px) { .workspace.with-editor { grid-template-columns: 170px minmax(0, 1fr) 410px; } .with-editor .shot-card { grid-template-columns: 1fr; } .with-editor .shot-preview { max-height: 280px; } .with-editor .shot-order { border-left: none; border-top: 1px solid var(--line); flex-direction: row; justify-content: flex-end; } }
	@media (max-width: 1100px) { .workspace.with-editor { grid-template-columns: 195px minmax(0, 1fr); } .with-editor .shot-card { grid-template-columns: minmax(170px, 30%) minmax(0, 1fr) 75px; } .with-editor .shot-order { flex-direction: column; border-top: none; border-left: 1px solid var(--line); justify-content: center; } }
	@media (max-width: 800px) { .workspace, .workspace.with-editor { grid-template-columns: 1fr; } .scene-nav { position: static; min-height: auto; padding: 16px; border-right: none; border-bottom: 1px solid var(--line); } .project-label, .nav-footer { display: none; } .scene-links { display: flex; overflow-x: auto; margin: 0 0 12px; } .scene-links a { min-width: 130px; } .board { padding: 22px 16px 40px; } .shot-card, .with-editor .shot-card { grid-template-columns: minmax(130px, 30%) minmax(0, 1fr); } .shot-order, .with-editor .shot-order { grid-column: 1/-1; flex-direction: row; border-left: none; border-top: 1px solid var(--line); justify-content: flex-end; } }
	@media (max-width: 540px) { .shot-card, .with-editor .shot-card { grid-template-columns: 1fr; } .shot-preview { max-height: 300px; } .back-link { margin-left: 0; } }
</style>
