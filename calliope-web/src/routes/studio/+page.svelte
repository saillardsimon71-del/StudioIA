<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import ConfigDialog from '$lib/studio/ConfigDialog.svelte';
	import { studio, errorMessage, type StudioProject, type AspectRatio, type Preflight } from '$lib/studio/api';
	let projects = $state<StudioProject[]>([]);
	let loading = $state(true);
	let busy = $state('');
	let error = $state('');
	let title = $state('');
	let idea = $state('');
	let aspect = $state<AspectRatio>('16:9');
	let configOpen = $state(false);
	let preflight = $state<Preflight | null>(null);
	let diagnosticError = $state('');
	let testing = $state(false);
	let dryRun = $state(false);
	const checks = [{ key: 'modal', label: 'Génération vidéo' }, { key: 'ffmpeg', label: 'Assemblage MP4' }, { key: 'llm', label: 'Écriture IA (facultatif)' }, { key: 'image', label: 'Images IA (facultatif)' }] as const;

	onMount(() => {
		load();
		studio.config().then((value) => dryRun = value.dry_run).catch(() => {});
	});
	async function load() {
		loading = true;
		error = '';
		try { projects = (await studio.projects()).projects; }
		catch (err) { error = errorMessage(err); }
		finally { loading = false; }
	}
	async function create(event: SubmitEvent) {
		event.preventDefault();
		if (busy) return;
		busy = 'create'; error = '';
		try { const project = await studio.create({ title: title.trim(), idea: idea.trim(), aspect_ratio: aspect }); await goto(`/studio/${project.id}`); }
		catch (err) { error = errorMessage(err); }
		finally { busy = ''; }
	}
	async function example() {
		if (busy) return;
		busy = 'example'; error = '';
		try { const project = await studio.example(); await goto(`/studio/${project.id}`); }
		catch (err) { error = errorMessage(err); }
		finally { busy = ''; }
	}
	async function testServices() {
		if (testing) return;
		testing = true; diagnosticError = '';
		try { preflight = await studio.preflight(); }
		catch (err) { diagnosticError = errorMessage(err); }
		finally { testing = false; }
	}
	function configSaved() { preflight = null; studio.config().then((value) => dryRun = value.dry_run).catch((err) => diagnosticError = errorMessage(err)); }
	function date(value: string) { return new Date(value).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' }); }
</script>

<svelte:head><title>Studio IA - Projets</title><meta name="description" content="Un atelier vidéo de votre idée au montage final." /></svelte:head>

<header class="page-header between"><a class="brand" href="/studio">Studio IA</a><div class="row"><button onclick={() => configOpen = true}>Configurer les services</button><a href="/settings">Options avancées</a></div></header>
<main class="home">
	<div class="intro"><h1>Vos projets vidéo</h1><p class="muted">Préparez les scènes, choisissez vos prises et assemblez le film.</p></div>
	{#if dryRun}<p class="error" role="status">La simulation est active. Aucun média ne sera généré. Désactivez-la dans les services pour produire un film.</p>{/if}
	{#if error}<div class="error" role="alert">{error}{#if !loading && !projects.length}<button class="small retry" onclick={load}>Réessayer</button>{/if}</div>{/if}
	<div class="home-grid">
		<section class="project-section" aria-labelledby="projects-title">
			<div class="between section-heading"><h2 id="projects-title">Projets</h2><button class="small subtle" onclick={load} disabled={loading}>Actualiser</button></div>
			{#if loading}<p class="muted">Chargement des projets...</p>
			{:else if !projects.length}<div class="empty"><h3>Le premier projet commence ici.</h3><p class="muted">Créez un projet avec votre idée ou ouvrez un exemple pour découvrir le découpage.</p><button onclick={example} disabled={!!busy}>{busy === 'example' ? 'Création...' : 'Ouvrir un exemple'}</button></div>
			{:else}<div class="project-list">{#each projects as project (project.id)}
				<a class="project-link" href={`/studio/${project.id}`}>
					<div class="project-cover media" style={`aspect-ratio: ${project.aspect_ratio.replace(':', '/')}`}>
						{#if project.cover_url}<img src={project.cover_url} alt="" loading="lazy" />{:else}<span>{project.aspect_ratio}</span>{/if}
					</div>
					<div class="project-copy"><h3>{project.title}</h3><p class="muted idea">{project.idea || 'Projet en préparation'}</p><p class="project-details">{project.scene_count ?? 0} scènes, {project.shot_count ?? 0} plans{#if project.shot_count}, {project.approved_count ?? 0} approuvés{/if}</p></div>
					<span class="project-date">{date(project.updated_at)}</span>
				</a>
			{/each}</div><button class="small example" onclick={example} disabled={!!busy}>{busy === 'example' ? 'Création...' : 'Créer un projet exemple'}</button>{/if}
		</section>
		<aside class="new-project" aria-labelledby="new-title">
			<form onsubmit={create} class="stack"><h2 id="new-title">Nouveau projet</h2><label>Titre<input required maxlength="200" bind:value={title} placeholder="Le nom de votre film" /></label><label>Idée ou intention<textarea bind:value={idea} placeholder="Le sujet, les personnages, l'ambiance..." rows="4"></textarea></label><label>Format<select bind:value={aspect}><option value="16:9">16:9 - Paysage</option><option value="9:16">9:16 - Portrait</option><option value="1:1">1:1 - Carré</option></select></label><button class="primary" disabled={!!busy || !title.trim()}>{busy === 'create' ? 'Création...' : 'Créer le projet'}</button><p class="hint">Vous pourrez ensuite coller un script ou préparer les scènes avec l'IA.</p></form>
			<div class="services stack"><div class="between"><h3>Services</h3><button class="small" onclick={testServices} disabled={testing}>{testing ? 'Vérification...' : 'Vérifier'}</button></div>
				{#if diagnosticError}<p class="error" role="alert">{diagnosticError}</p>{/if}
				{#if preflight}{#each checks as check}<div class="service"><div class="between"><span>{check.label}</span><span class:approved={preflight[check.key].ok} class:pending={!preflight[check.key].ok} class="badge">{preflight[check.key].ok ? 'Prêt' : 'À configurer'}</span></div><p class="hint">{preflight[check.key].message}</p></div>{/each}<p class="hint">Moteur: {preflight.engine}</p>{:else}<p class="hint">Vérifiez la connexion vidéo et l'assemblage avant de lancer vos prises. L'écriture et les images IA sont facultatives.</p>{/if}
			</div>
		</aside>
	</div>
</main>
{#if configOpen}<ConfigDialog onclose={() => configOpen = false} onsaved={configSaved} />{/if}

<style>
	.home { max-width: 1360px; margin: auto; padding: 38px 32px 60px; display: grid; gap: 28px; }
	.intro { display: grid; gap: 9px; }
	.home-grid { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 40px; align-items: start; }
	.section-heading { margin-bottom: 18px; }
	.project-list { display: grid; border-top: 1px solid var(--line); }
	.project-link { display: grid; grid-template-columns: 128px minmax(0, 1fr) auto; align-items: center; gap: 20px; padding: 18px 0; border-bottom: 1px solid var(--line); text-decoration: none; color: var(--ink); }
	.project-link:hover h3 { text-decoration: underline; }
	.project-cover { height: 80px; border-radius: 3px; width: 128px; color: #a5b9ab; font-size: 13px; }
	.project-copy { display: grid; gap: 5px; }
	.idea { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; overflow: hidden; font-size: 14px; }
	.project-details, .project-date { color: var(--muted); font-size: 12px; }
	.new-project { border: 1px solid var(--line); border-top: 4px solid var(--green); background: var(--white); padding: 24px; border-radius: 5px; }
	.services { margin-top: 28px; padding-top: 22px; border-top: 1px solid var(--line); }
	.service { display: grid; gap: 5px; font-size: 13px; }
	.example { margin-top: 18px; }
	.retry { margin-left: 14px; }
	@media (max-width: 900px) { .home-grid { grid-template-columns: 1fr; gap: 28px; } .new-project { max-width: 600px; width: 100%; } }
	@media (max-width: 650px) { .home { padding: 24px 16px; } .project-link { grid-template-columns: 90px minmax(0, 1fr); gap: 13px; } .project-cover { width: 90px; height: 64px; } .project-date { display: none; } }
</style>
