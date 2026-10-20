<script lang="ts">
	import { onMount } from 'svelte';
	import { api, mediaUrl } from '$lib/api';
	import type { LibraryAsset, Project, ShotPrepReport } from '$lib/types';

	type Step = 'IDEA' | 'SCENARIO' | 'ASSETS' | 'RENDER' | 'EXPORT';
	const STEPS: { id: Step; num: number; label: string }[] = [
		{ id: 'IDEA', num: 1, label: 'IDÉE' },
		{ id: 'SCENARIO', num: 2, label: 'SCÉNARIO' },
		{ id: 'ASSETS', num: 3, label: 'ASSETS' },
		{ id: 'RENDER', num: 4, label: 'RENDER' },
		{ id: 'EXPORT', num: 5, label: 'EXPORT' }
	];

	let activeStep = $state<Step>('IDEA');
	let health = $state<Record<string, any> | null>(null);
	let modalStatus = $state<{ ok: boolean; message: string } | null>(null);
	let settings = $state<Record<string, any> | null>(null);
	let showSettings = $state(false);
	let showCosts = $state(false);
	let ledger = $state<Record<string, any> | null>(null);

	let projectList = $state<Array<Record<string, any>>>([]);
	let project = $state<Project | null>(null);
	let assets = $state<LibraryAsset[]>([]);
	let prepReports = $state<Record<string, ShotPrepReport>>({});
	let takes = $state<Array<Record<string, any>>>([]);
	let exportsList = $state<Array<Record<string, any>>>([]);

	let busy = $state(false);
	let errorBanner = $state('');
	let infoBanner = $state('');

	// Nouveau projet
	let newTitle = $state('');
	let newIdea = $state('');
	let newDuration = $state(15);

	// Formulaire bibliothèque
	let libKind = $state<'character' | 'decor' | 'frame'>('character');
	let libName = $state('');
	let libDesc = $state('');
	let dragOverKey = $state('');

	// Autorisation explicite par plan (jamais activée par défaut)
	let authorizedByShot = $state<Record<string, boolean>>({});
	let ceilingByShot = $state<Record<string, number>>({});
	let reviewNotesByShot = $state<Record<string, string>>({});

	// Paramètres éditables
	let apiKeyInput = $state('');

	async function refreshGlobal() {
		const [h, m, s, plist, alist, cost] = await Promise.all([
			api.health(),
			api.modalStatus(),
			api.getSettings(),
			api.listProjects(),
			api.listAssets(),
			api.costLedger()
		]);
		health = h;
		modalStatus = m;
		settings = s;
		projectList = plist;
		assets = alist;
		ledger = cost;
	}

	async function selectProject(id: string) {
		errorBanner = '';
		const [p, t, ex] = await Promise.all([
			api.getProject(id),
			api.listTakes(id),
			api.listExports(id)
		]);
		project = p;
		takes = t;
		exportsList = ex;
		authorizedByShot = {};
		const caps: Record<string, number> = {};
		for (const sh of p.shots) {
			caps[sh.id] = settings?.max_budget_eur_per_take ?? 1.0;
		}
		ceilingByShot = caps;
		await runPrepare(false);
	}

	onMount(async () => {
		try {
			await refreshGlobal();
			if (projectList.length > 0) {
				await selectProject(projectList[0].id);
			} else {
				const seeded = await api.createPhase3Template();
				await refreshGlobal();
				await selectProject(seeded.id);
			}
		} catch (e: any) {
			errorBanner = e?.message || String(e);
		}
	});

	async function handleCreateProject() {
		if (!newIdea.trim()) {
			errorBanner = "Saisissez une idée avant de créer le projet.";
			return;
		}
		busy = true;
		errorBanner = '';
		try {
			const created = await api.createProject(newTitle, newIdea, newDuration);
			newTitle = '';
			newIdea = '';
			await refreshGlobal();
			await selectProject(created.id);
			infoBanner = `Projet « ${created.title} » créé dans v2.db.`;
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
		}
	}

	async function handleLoadPhase3Template() {
		busy = true;
		errorBanner = '';
		try {
			const p3 = await api.createPhase3Template();
			await refreshGlobal();
			await selectProject(p3.id);
			activeStep = 'ASSETS';
			infoBanner = 'Projet Phase 3 (Fruit anthropomorphe 5s) prêt : importez une image ChatGPT puis vérifiez l’estimation.';
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
		}
	}

	async function handleSaveProject() {
		if (!project) return;
		busy = true;
		errorBanner = '';
		try {
			project = await api.saveProject(project);
			await refreshGlobal();
			await runPrepare(false);
			infoBanner = 'Modifications enregistrées et validées (Pydantic V2).';
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
		}
	}

	async function handleGenerateScenario() {
		if (!project) return;
		busy = true;
		errorBanner = '';
		infoBanner = '';
		try {
			const res = await api.generateScenario(project.id, project.idea, project.target_duration_s);
			project = res.project;
			await refreshGlobal();
			await runPrepare(false);
			activeStep = 'SCENARIO';
			const warnTxt =
				res.llm_meta?.warnings?.length > 0 ? ` (${res.llm_meta.warnings.join(' | ')})` : '';
			infoBanner = `Scénario généré en 1 appel DeepSeek (${res.llm_meta.prompt_tokens}+${res.llm_meta.completion_tokens} tokens).${warnTxt}`;
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
		}
	}

	function addCharacter() {
		if (!project) return;
		const idx = project.characters.length + 1;
		project.characters = [
			...project.characters,
			{
				id: `perso_${idx}`,
				name: `Personnage ${idx}`,
				description: 'Expressive stylized character in casual clothes, clear facial features',
				image_ref: null,
				voice_hint: 'natural'
			}
		];
	}

	function removeCharacter(idx: number) {
		if (!project) return;
		project.characters = project.characters.filter((_, i) => i !== idx);
	}

	function addShot() {
		if (!project) return;
		const idx = project.shots.length + 1;
		const defaultSpeaker = project.characters[0]?.id || 'perso_1';
		project.shots = [
			...project.shots,
			{
				id: `shot_${idx}`,
				duration_s: 5.0,
				action: 'Medium vertical shot, character reacts naturally toward camera.',
				dialogue: [{ speaker_id: defaultSpeaker, text: 'Nouvelle réplique en français.', start_sec: 0.3 }],
				initial_state: 'Character stands calm at opening frame.',
				final_state: 'Character finishes speaking with an expressive look.',
				image_ref: null,
				continue_from_previous: idx > 1,
				ltx_prompt: '',
				seed: 42 + idx,
				image_validated: false,
				display_overlay: null,
				rendered_video_ref: null,
				human_validated: false
			}
		];
	}

	function removeShot(idx: number) {
		if (!project) return;
		project.shots = project.shots.filter((_, i) => i !== idx);
	}

	function toggleOverlay(idx: number) {
		if (!project) return;
		const shot = project.shots[idx];
		if (shot.display_overlay) {
			shot.display_overlay = null;
		} else {
			shot.display_overlay = {
				initial_text: '000',
				x: 0.5,
				y: 0.14,
				font_size: 0.05,
				color: '00FF88',
				box_bg: '101418',
				changes: [{ at_sec: Math.max(0.5, Number((shot.duration_s * 0.65).toFixed(1))), text: '001' }]
			};
		}
	}

	async function handleLibraryDrop(files: FileList | null) {
		if (!files || files.length === 0) return;
		busy = true;
		errorBanner = '';
		try {
			for (const file of Array.from(files)) {
				await api.uploadLibraryAsset(file, libKind, libName || file.name, libDesc);
			}
			libName = '';
			libDesc = '';
			assets = await api.listAssets();
			infoBanner = 'Image(s) importée(s) dans la bibliothèque persistante.';
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
			dragOverKey = '';
		}
	}

	async function handleShotImageFile(shotId: string, files: FileList | null) {
		if (!project || !files || files.length === 0) return;
		busy = true;
		errorBanner = '';
		try {
			project = await api.uploadShotImage(project.id, shotId, files[0]);
			assets = await api.listAssets();
			await runPrepare(false);
			infoBanner = `Image importée sur ${shotId}. Vérifiez-la visuellement puis cliquez « Valider pour GPU ».`;
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
			dragOverKey = '';
		}
	}

	async function handleCharImageFile(charId: string, files: FileList | null) {
		if (!project || !files || files.length === 0) return;
		busy = true;
		errorBanner = '';
		try {
			project = await api.uploadCharacterImage(project.id, charId, files[0]);
			assets = await api.listAssets();
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
			dragOverKey = '';
		}
	}

	async function runPrepare(recompile: boolean) {
		if (!project) return;
		try {
			const res = await api.prepareRender(project.id, recompile);
			project = res.project;
			const map: Record<string, ShotPrepReport> = {};
			for (const r of res.shots) {
				map[r.shot_id] = r;
			}
			prepReports = map;
		} catch (e: any) {
			errorBanner = e.message;
		}
	}

	async function handleRenderSingleShot(shotId: string) {
		if (!project) return;
		busy = true;
		errorBanner = '';
		infoBanner = '';
		try {
			await api.saveProject(project);
			const res = await api.renderShot(
				project.id,
				shotId,
				Boolean(authorizedByShot[shotId]),
				ceilingByShot[shotId] ?? settings?.max_budget_eur_per_take ?? 1.0
			);
			project = res.project;
			authorizedByShot[shotId] = false; // Réinitialise l'autorisation unitaire après usage
			takes = await api.listTakes(project.id);
			ledger = await api.costLedger();
			await runPrepare(false);
			infoBanner = `Prise ${shotId} terminée. Visionnez la vidéo et contrôlez le rapport OCR/Whisper.`;
		} catch (e: any) {
			errorBanner = e.message;
			takes = await api.listTakes(project.id);
			ledger = await api.costLedger();
		} finally {
			busy = false;
		}
	}

	async function handleUploadManualTake(shotId: string, files: FileList | null) {
		if (!project || !files || files.length === 0) return;
		busy = true;
		errorBanner = '';
		try {
			const res = await api.uploadManualTake(project.id, shotId, files[0]);
			project = res.project;
			takes = await api.listTakes(project.id);
			await runPrepare(false);
			infoBanner = `MP4 local attaché à ${shotId} (0,00 € GPU) et analysé par le QC.`;
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
		}
	}

	async function handleExportMp4() {
		if (!project) return;
		busy = true;
		errorBanner = '';
		infoBanner = '';
		try {
			await api.saveProject(project);
			const record = await api.exportProject(project.id);
			exportsList = await api.listExports(project.id);
			infoBanner = `MP4 vertical exporté (${record.width}x${record.height}, ${record.duration_s.toFixed(2)}s).`;
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
		}
	}

	async function saveSettingsForm() {
		if (!settings) return;
		busy = true;
		errorBanner = '';
		try {
			const patch: Record<string, any> = {
				deepseek_base_url: settings.deepseek_base_url,
				deepseek_model: settings.deepseek_model,
				modal_app_name: settings.modal_app_name,
				modal_function_name: settings.modal_function_name,
				gpu_label: settings.gpu_label,
				gpu_usd_per_sec:
					settings.gpu_usd_per_sec === '' || settings.gpu_usd_per_sec === null
						? null
						: Number(settings.gpu_usd_per_sec),
				cpu_cores: Number(settings.cpu_cores),
				memory_gib: Number(settings.memory_gib),
				usd_to_eur:
					settings.usd_to_eur === '' || settings.usd_to_eur === null
						? null
						: Number(settings.usd_to_eur),
				max_budget_eur_per_take: Number(settings.max_budget_eur_per_take),
				whisper_model_or_path: settings.whisper_model_or_path,
				ocr_enabled: Boolean(settings.ocr_enabled)
			};
			if (apiKeyInput.trim()) {
				patch.deepseek_api_key = apiKeyInput.trim();
			}
			settings = await api.updateSettings(patch);
			apiKeyInput = '';
			await refreshGlobal();
			await runPrepare(false);
			showSettings = false;
			infoBanner = 'Paramètres Studio IA V2 enregistrés dans studio-v2/config.json.';
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
		}
	}

	function wordCount(text: string): number {
		const trimmed = (text || '').trim();
		return trimmed ? trimmed.split(/\s+/).length : 0;
	}
</script>

<div style="max-width: 1260px; margin: 0 auto; padding: 1rem 1.25rem 3rem;">
	<!-- Barre supérieure -->
	<header
		class="card"
		style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 0.75rem; margin-bottom: 1rem;"
	>
		<div style="display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
			<strong style="font-size: 1.15rem; letter-spacing: 0.02em;">STUDIO IA V2</strong>
			<span class="badge ok">Isolé de V1 · Port {health?.port ?? 8270}</span>
			{#if health?.tools}
				<span class="badge {health.tools.ffmpeg ? 'ok' : 'err'}">FFmpeg</span>
				<span class="badge {health.tools.tesseract_ocr ? 'ok' : 'warn'}">OCR</span>
				<span class="badge {health.tools.whisper_cpu ? 'ok' : 'warn'}">Whisper</span>
				<span class="badge {health.tools.deepseek_key_configured ? 'ok' : 'warn'}">DeepSeek</span>
				<span class="badge {modalStatus?.ok ? 'ok' : 'warn'}">Modal GPU</span>
			{/if}
		</div>

		<div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
			{#if projectList.length > 0 && project}
				<select
					style="width: auto; min-width: 230px;"
					value={project.id}
					onchange={(e) => selectProject((e.target as HTMLSelectElement).value)}
				>
					{#each projectList as item}
						<option value={item.id}>{item.title} ({item.shot_count} plan(s))</option>
					{/each}
				</select>
			{/if}
			<button onclick={handleLoadPhase3Template} disabled={busy}>Modèle Phase 3 (Fruit 5s)</button>
			<button onclick={() => (showCosts = !showCosts)}>
				Coûts ({ledger?.total_actual_gpu_eur?.toFixed(3) ?? '0.000'} €)
			</button>
			<button onclick={() => (showSettings = !showSettings)}>Paramètres</button>
		</div>
	</header>

	{#if errorBanner}
		<div
			class="card"
			style="border-color: var(--err); background: #2c0b10; margin-bottom: 1rem; white-space: pre-wrap;"
		>
			<div style="display: flex; justify-content: space-between; gap: 1rem;">
				<div><strong>Diagnostic :</strong> {errorBanner}</div>
				<button onclick={() => (errorBanner = '')}>Fermer</button>
			</div>
		</div>
	{/if}

	{#if infoBanner}
		<div
			class="card"
			style="border-color: var(--accent); background: #0c2336; margin-bottom: 1rem;"
		>
			<div style="display: flex; justify-content: space-between; gap: 1rem;">
				<div>{infoBanner}</div>
				<button onclick={() => (infoBanner = '')}>OK</button>
			</div>
		</div>
	{/if}

	<!-- Panneau Paramètres -->
	{#if showSettings && settings}
		<section class="card" style="margin-bottom: 1rem;">
			<h3 style="margin-top: 0;">Paramètres isolés (`studio-v2/config.json`)</h3>
			<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 0.85rem;">
				<div>
					<label>Clé API DeepSeek ({settings.deepseek_api_key_configured ? 'configurée' : 'absente'})
						<input type="password" placeholder="sk-..." bind:value={apiKeyInput} />
					</label>
				</div>
				<div>
					<label>Modèle DeepSeek
						<input bind:value={settings.deepseek_model} />
					</label>
				</div>
				<div>
					<label>App Modal / Fonction
						<div style="display: flex; gap: 0.4rem;">
							<input bind:value={settings.modal_app_name} />
							<input bind:value={settings.modal_function_name} />
						</div>
					</label>
				</div>
				<div>
					<label>Préréglage tarif réel Modal
						<select
							onchange={(e) => {
								const key = (e.target as HTMLSelectElement).value;
								if (key === 'NONE') {
									settings!.gpu_usd_per_sec = null;
									settings!.gpu_label = 'Tarif non configuré';
								} else if (settings?.presets?.[key]) {
									const p = settings.presets[key];
									settings.gpu_label = p.label;
									settings.gpu_usd_per_sec = p.gpu_usd_per_sec;
									settings.cpu_cores = p.cpu_cores;
									settings.memory_gib = p.memory_gib;
								}
							}}
						>
							<option value="">-- Choisir un profil tarifaire fournisseur --</option>
							<option value="L40S_PROD">Modal L40S + 8 CPU + 64 GiB ($1.95/h GPU)</option>
							<option value="RTX_PRO_6000">Modal RTX PRO 6000 + 2 CPU + 32 GiB ($3.06/h GPU)</option>
							<option value="NONE">Non renseigné (retourne None — aucun prix inventé)</option>
						</select>
					</label>
				</div>
				<div>
					<label>Tarif GPU ($/seconde, vide = None)
						<input type="number" step="0.000001" bind:value={settings.gpu_usd_per_sec} />
					</label>
				</div>
				<div>
					<label>Taux USD → EUR
						<input type="number" step="0.01" bind:value={settings.usd_to_eur} />
					</label>
				</div>
				<div>
					<label>Plafond par prise (€)
						<input type="number" step="0.05" bind:value={settings.max_budget_eur_per_take} />
					</label>
				</div>
				<div>
					<label>Whisper CPU (modèle ou chemin `.pt`)
						<input bind:value={settings.whisper_model_or_path} />
					</label>
				</div>
			</div>
			<div style="margin-top: 0.9rem; display: flex; gap: 0.6rem;">
				<button class="primary" onclick={saveSettingsForm} disabled={busy}>Enregistrer Paramètres</button>
				<button onclick={() => (showSettings = false)}>Annuler</button>
			</div>
		</section>
	{/if}

	<!-- Panneau Registre des Coûts -->
	{#if showCosts && ledger}
		<section class="card" style="margin-bottom: 1rem;">
			<h3 style="margin-top: 0;">Journal des coûts réels (`v2.db` & `COSTS.md`)</h3>
			<p style="color: var(--muted); margin-top: 0;">
				Total GPU Modal mesuré : <strong>{ledger.total_actual_gpu_eur} €</strong> ({ledger.completed_takes} réussie(s),
				{ledger.failed_takes} échouée(s)) · Total LLM DeepSeek estimé :
				<strong>{ledger.total_estimated_llm_eur} €</strong> ({ledger.llm_calls} appel(s)).
			</p>
			{#if ledger.takes?.length > 0}
				<div style="overflow-x: auto;">
					<table style="width: 100%; border-collapse: collapse; font-size: 0.85rem;">
						<thead>
							<tr style="text-align: left; border-bottom: 1px solid var(--border);">
								<th style="padding: 0.35rem;">Date</th>
								<th>Projet / Plan</th>
								<th>Statut</th>
								<th>Estimé</th>
								<th>Coût réel</th>
								<th>Calcul</th>
							</tr>
						</thead>
						<tbody>
							{#each ledger.takes as t}
								<tr style="border-bottom: 1px solid #1f2735;">
									<td style="padding: 0.35rem;">{t.created_at}</td>
									<td>{t.project_id} / {t.shot_id}</td>
									<td>{t.status}</td>
									<td>{t.estimated_cost_eur ?? 'None'} €</td>
									<td>{t.actual_cost_eur ?? 'N/A'} €</td>
									<td>{t.observed_compute_s ?? '-'} s</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	{/if}

	<!-- Navigation des 5 étapes -->
	<nav
		style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 0.5rem; margin-bottom: 1.15rem;"
	>
		{#each STEPS as step}
			<button
				class={activeStep === step.id ? 'primary' : ''}
				onclick={() => (activeStep = step.id)}
				style="padding: 0.65rem 0.5rem; text-align: center;"
			>
				{step.num}. {step.label}
			</button>
		{/each}
	</nav>

	{#if project}
		<!-- ═══════════════════════ ÉTAPE 1 : IDEA ═══════════════════════ -->
		{#if activeStep === 'IDEA'}
			<div style="display: grid; grid-template-columns: 1.5fr 1fr; gap: 1rem;">
				<section class="card">
					<h2 style="margin-top: 0;">1. Idée du film (`{project.id}`)</h2>
					<div style="display: grid; gap: 0.75rem;">
						<label>Titre du projet
							<input bind:value={project.title} />
						</label>
						<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
							<label>Format
								<select bind:value={project.aspect_ratio}>
									<option value="9:16">9:16 Vertical (Shorts / Reels / TikTok)</option>
									<option value="16:9">16:9 Horizontal</option>
									<option value="1:1">1:1 Carré</option>
								</select>
							</label>
							<label>Durée cible (secondes)
								<input type="number" min="3" max="90" step="1" bind:value={project.target_duration_s} />
							</label>
						</div>
						<label>Idée libre (hook, personnages, comédie/suspense, cliffhanger)
							<textarea rows="5" bind:value={project.idea}></textarea>
						</label>
						<div style="display: flex; flex-wrap: wrap; gap: 0.6rem;">
							<button class="primary" onclick={handleGenerateScenario} disabled={busy}>
								Générer le scénario avec DeepSeek (1 seul appel)
							</button>
							<button onclick={handleSaveProject} disabled={busy}>Enregistrer l'idée</button>
							<button onclick={() => (activeStep = 'SCENARIO')}>Éditer le scénario →</button>
						</div>
						<small style="color: var(--muted);">
							Garantie V2 : aucun second agent critique, aucune réécriture cachée. Si DeepSeek renvoie un JSON invalide, le problème exact s'affiche ci-dessus.
						</small>
					</div>
				</section>

				<section class="card">
					<h3 style="margin-top: 0;">Créer un autre projet</h3>
					<div style="display: grid; gap: 0.65rem;">
						<label>Titre (optionnel)
							<input placeholder="Ex. Fruit City — Épisode 1" bind:value={newTitle} />
						</label>
						<label>Durée visée (s)
							<input type="number" min="5" max="60" bind:value={newDuration} />
						</label>
						<label>Idée libre
							<textarea
								rows="4"
								placeholder="Deux fruits anthropomorphes découvrent un compteur mystérieux sur la table..."
								bind:value={newIdea}
							></textarea>
						</label>
						<button onclick={handleCreateProject} disabled={busy}>+ Créer le projet</button>
					</div>
				</section>
			</div>
		{/if}

		<!-- ═══════════════════════ ÉTAPE 2 : SCENARIO ═══════════════════════ -->
		{#if activeStep === 'SCENARIO'}
			<section class="card" style="margin-bottom: 1rem;">
				<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem;">
					<h2 style="margin: 0;">2. Personnages (`Character`)</h2>
					<button onclick={addCharacter}>+ Ajouter un personnage</button>
				</div>
				<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 0.75rem;">
					{#each project.characters as char, cIdx}
						<div style="border: 1px solid var(--border); border-radius: 8px; padding: 0.75rem; background: var(--panel-alt);">
							<div style="display: flex; gap: 0.5rem; margin-bottom: 0.5rem;">
								<input placeholder="id" bind:value={char.id} style="width: 42%;" />
								<input placeholder="Nom" bind:value={char.name} />
								<button class="danger" onclick={() => removeCharacter(cIdx)}>✕</button>
							</div>
							<textarea rows="2" placeholder="Description visuelle concise (anglais recommandé pour LTX)" bind:value={char.description}></textarea>
							<input
								style="margin-top: 0.4rem;"
								placeholder="Timbre vocal (ex. raspy low, bright youthful)"
								bind:value={char.voice_hint}
							/>
						</div>
					{/each}
				</div>
			</section>

			<section class="card">
				<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.9rem;">
					<h2 style="margin: 0;">
						Découpage en plans (`Shot` — {project.shots.reduce((a, s) => a + Number(s.duration_s || 0), 0).toFixed(1)} s au total)
					</h2>
					<div style="display: flex; gap: 0.5rem;">
						<button onclick={addShot}>+ Ajouter un plan</button>
						<button class="primary" onclick={handleSaveProject} disabled={busy}>
							Enregistrer le scénario
						</button>
						<button onclick={() => (activeStep = 'ASSETS')}>Étape 3 : Assets →</button>
					</div>
				</div>

				<div style="display: grid; gap: 0.9rem;">
					{#each project.shots as shot, sIdx}
						<div style="border: 1px solid var(--border); border-radius: 9px; padding: 0.9rem; background: var(--panel-alt);">
							<div style="display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center; margin-bottom: 0.6rem;">
								<strong>Plan #{sIdx + 1}</strong>
								<input style="width: 130px;" bind:value={shot.id} />
								<label style="display: flex; align-items: center; gap: 0.35rem; width: auto;">
									Durée (s)
									<input type="number" step="0.5" min="1" max="15" style="width: 80px;" bind:value={shot.duration_s} />
								</label>
								<label style="display: flex; align-items: center; gap: 0.35rem; width: auto;">
									<input
										type="checkbox"
										style="width: auto;"
										disabled={sIdx === 0}
										bind:checked={shot.continue_from_previous}
									/>
									Continuer depuis la fin du plan précédent
								</label>
								<button onclick={() => toggleOverlay(sIdx)}>
									{shot.display_overlay ? 'Retirer zone chiffres fixes' : '+ Zone chiffres/texte fixe'}
								</button>
								<button class="danger" onclick={() => removeShot(sIdx)}>Supprimer</button>
							</div>

							<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.6rem; margin-bottom: 0.6rem;">
								<label>État initial (`initial_state` à t=0)
									<input bind:value={shot.initial_state} />
								</label>
								<label>État final (`final_state`)
									<input bind:value={shot.final_state} />
								</label>
							</div>

							<label>Action physique filmée (`action`)
								<textarea rows="2" bind:value={shot.action}></textarea>
							</label>

							<!-- Dialogues français -->
							<div style="margin-top: 0.65rem;">
								<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">
									<span style="font-size: 0.86rem; color: var(--muted);">Dialogues français (`DialogueLine`)</span>
									<button
										style="padding: 0.25rem 0.6rem; font-size: 0.82rem;"
										onclick={() =>
											(shot.dialogue = [
												...shot.dialogue,
												{
													speaker_id: project!.characters[0]?.id || '',
													text: '',
													start_sec: null
												}
											])}
									>
										+ Réplique
									</button>
								</div>
								{#each shot.dialogue as line, lIdx}
									<div style="display: flex; gap: 0.45rem; margin-bottom: 0.35rem;">
										<select style="width: 190px;" bind:value={line.speaker_id}>
											{#each project.characters as c}
												<option value={c.id}>{c.name} ({c.id})</option>
											{/each}
										</select>
										<input placeholder="Réplique parlée en français..." bind:value={line.text} />
										<input
											type="number"
											step="0.1"
											placeholder="t(s)"
											style="width: 85px;"
											bind:value={line.start_sec}
										/>
										<button
											class="danger"
											onclick={() => (shot.dialogue = shot.dialogue.filter((_, i) => i !== lIdx))}
										>
											✕
										</button>
									</div>
								{/each}
							</div>

							<!-- Affichage numérique fixe au montage -->
							{#if shot.display_overlay}
								<div style="margin-top: 0.65rem; padding: 0.65rem; border-radius: 7px; background: #0f1520; border: 1px dashed var(--accent);">
									<div style="font-size: 0.84rem; margin-bottom: 0.45rem; color: var(--accent);">
										<strong>Zone chiffres/texte exacte (incrustée au montage FFmpeg — écran vierge envoyé à LTX)</strong>
									</div>
									<div style="display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;">
										<label style="width: 130px;">Valeur t=0
											<input bind:value={shot.display_overlay.initial_text} />
										</label>
										<label style="width: 100px;">Pos X (0–1)
											<input type="number" step="0.05" min="0" max="1" bind:value={shot.display_overlay.x} />
										</label>
										<label style="width: 100px;">Pos Y (0–1)
											<input type="number" step="0.05" min="0" max="1" bind:value={shot.display_overlay.y} />
										</label>
										<button
											onclick={() =>
												shot.display_overlay &&
												(shot.display_overlay.changes = [
													...shot.display_overlay.changes,
													{ at_sec: 2.5, text: '001' }
												])}
										>
											+ Changement horodaté
										</button>
									</div>
									{#each shot.display_overlay.changes as chg, chIdx}
										<div style="display: flex; gap: 0.45rem; margin-top: 0.4rem; max-width: 420px;">
											<input type="number" step="0.1" bind:value={chg.at_sec} placeholder="À (s)" />
											<input bind:value={chg.text} placeholder="Nouvelle valeur" />
											<button
												class="danger"
												onclick={() =>
													shot.display_overlay &&
													(shot.display_overlay.changes = shot.display_overlay.changes.filter(
														(_, i) => i !== chIdx
													))}
											>
												✕
											</button>
										</div>
									{/each}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			</section>
		{/if}

		<!-- ═══════════════════════ ÉTAPE 3 : ASSETS ═══════════════════════ -->
		{#if activeStep === 'ASSETS'}
			<div style="display: grid; grid-template-columns: 1fr 1.6fr; gap: 1rem;">
				<!-- Bibliothèque persistante -->
				<section class="card">
					<h2 style="margin-top: 0;">Bibliothèque visuelle persistante</h2>
					<p style="color: var(--muted); font-size: 0.88rem; margin-top: 0;">
						Importez vos personnages et décors générés dans ChatGPT par glisser-déposer.
					</p>
					<div style="display: grid; gap: 0.5rem; margin-bottom: 0.75rem;">
						<select bind:value={libKind}>
							<option value="character">Personnage récurrent</option>
							<option value="decor">Décor récurrent</option>
							<option value="frame">Image initiale de plan</option>
						</select>
						<input placeholder="Nom du visuel (ex. Inspecteur Zeste face)" bind:value={libName} />
						<div
							role="region"
							aria-label="Zone de dépôt bibliothèque"
							ondragover={(e) => {
								e.preventDefault();
								dragOverKey = 'lib';
							}}
							ondragleave={() => (dragOverKey = '')}
							ondrop={(e) => {
								e.preventDefault();
								handleLibraryDrop(e.dataTransfer?.files ?? null);
							}}
							style="border: 2px dashed {dragOverKey === 'lib' ? 'var(--accent)' : 'var(--border)'}; border-radius: 9px; padding: 1.1rem; text-align: center; background: #0e131b;"
						>
							Glissez-déposez des PNG/JPG ici ou
							<label style="color: var(--accent); text-decoration: underline; cursor: pointer;">
								choisissez un fichier
								<input
									type="file"
									accept="image/*"
									multiple
									style="display: none;"
									onchange={(e) => handleLibraryDrop((e.target as HTMLInputElement).files)}
								/>
							</label>
						</div>
					</div>

					<div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.6rem; max-height: 520px; overflow-y: auto;">
						{#each assets as asset}
							<div style="border: 1px solid var(--border); border-radius: 8px; padding: 0.45rem; background: var(--panel-alt);">
								<img
									src={mediaUrl(asset.file_path)}
									alt={asset.name}
									style="width: 100%; height: 130px; object-fit: cover; border-radius: 5px;"
								/>
								<div style="font-size: 0.8rem; font-weight: 600; margin-top: 0.3rem;">{asset.name}</div>
								<div style="font-size: 0.74rem; color: var(--muted);">
									{asset.kind} · {asset.width}×{asset.height}
								</div>
							</div>
						{/each}
					</div>
				</section>

				<!-- Références personnages & validation humaine des plans -->
				<section class="card">
					<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem;">
						<h2 style="margin: 0;">3. Images du projet & Validation avant GPU</h2>
						<button class="primary" onclick={() => (activeStep = 'RENDER')}>Étape 4 : Render →</button>
					</div>

					<h4 style="margin-bottom: 0.5rem;">Références personnages</h4>
					<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.65rem; margin-bottom: 1.2rem;">
						{#each project.characters as char}
							<div style="border: 1px solid var(--border); border-radius: 8px; padding: 0.65rem; background: var(--panel-alt);">
								<strong>{char.name}</strong>
								{#if char.image_ref}
									<img
										src={mediaUrl(char.image_ref)}
										alt={char.name}
										style="width: 100%; height: 150px; object-fit: cover; border-radius: 6px; margin: 0.45rem 0;"
									/>
								{/if}
								<input
									type="file"
									accept="image/*"
									onchange={(e) => handleCharImageFile(char.id, (e.target as HTMLInputElement).files)}
								/>
							</div>
						{/each}
					</div>

					<h4 style="margin-bottom: 0.5rem;">Images initiales des plans (validation humaine requise)</h4>
					<div style="display: grid; gap: 0.8rem;">
						{#each project.shots as shot}
							<div
								role="region"
								aria-label="Image du plan {shot.id}"
								ondragover={(e) => {
									e.preventDefault();
									dragOverKey = shot.id;
								}}
								ondragleave={() => (dragOverKey = '')}
								ondrop={(e) => {
									e.preventDefault();
									handleShotImageFile(shot.id, e.dataTransfer?.files ?? null);
								}}
								style="border: 2px dashed {dragOverKey === shot.id ? 'var(--accent)' : 'var(--border)'}; border-radius: 9px; padding: 0.85rem; background: var(--panel-alt); display: grid; grid-template-columns: 160px 1fr; gap: 0.9rem; align-items: center;"
							>
								<div>
									{#if shot.continue_from_previous}
										<div style="padding: 1.2rem 0.6rem; text-align: center; background: #0e131b; border-radius: 7px; font-size: 0.82rem; color: var(--accent);">
											Raccord automatique<br />(`last_frame` FFmpeg du plan précédent)
										</div>
									{:else if shot.image_ref}
										<img
											src={mediaUrl(shot.image_ref)}
											alt={shot.id}
											style="width: 160px; height: 260px; object-fit: cover; border-radius: 7px;"
										/>
									{:else}
										<div style="height: 180px; display: flex; align-items: center; justify-content: center; background: #0e131b; border-radius: 7px; font-size: 0.8rem; color: var(--muted); text-align: center; padding: 0.5rem;">
											Glissez l'image ChatGPT 9:16 ici
										</div>
									{/if}
								</div>

								<div>
									<div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem;">
										<strong>{shot.id}</strong> ({shot.duration_s}s)
										{#if shot.continue_from_previous}
											<span class="badge ok">Continuité auto</span>
										{:else if shot.image_validated}
											<span class="badge ok">Image validée par l'humain</span>
										{:else}
											<span class="badge warn">En attente de validation visuelle</span>
										{/if}
									</div>
									<p style="margin: 0.25rem 0 0.6rem; font-size: 0.86rem; color: var(--muted);">
										<strong>État t=0 attendu :</strong> {shot.initial_state || shot.action}
									</p>
									{#if !shot.continue_from_previous}
										<div style="display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;">
											<label style="width: auto; cursor: pointer; border: 1px solid var(--border); padding: 0.4rem 0.75rem; border-radius: 7px; background: #0f131b;">
												Importer PNG/JPG
												<input
													type="file"
													accept="image/*"
													style="display: none;"
													onchange={(e) =>
														handleShotImageFile(shot.id, (e.target as HTMLInputElement).files)}
												/>
											</label>
											{#if assets.length > 0}
												<select
													style="width: auto; max-width: 220px;"
													onchange={async (e) => {
														const aid = (e.target as HTMLSelectElement).value;
														if (aid && project) {
															project = await api.assignAssetToShot(project.id, shot.id, aid);
															await runPrepare(false);
														}
													}}
												>
													<option value="">Choisir dans la bibliothèque...</option>
													{#each assets as a}
														<option value={a.id}>{a.name}</option>
													{/each}
												</select>
											{/if}
											{#if shot.image_ref}
												<button
													class={shot.image_validated ? '' : 'primary'}
													onclick={async () => {
														if (!project) return;
														project = await api.validateShotImage(
															project.id,
															shot.id,
															!shot.image_validated
														);
														await runPrepare(false);
													}}
												>
													{shot.image_validated ? 'Annuler validation' : '✓ Valider pour GPU'}
												</button>
											{/if}
										</div>
									{/if}
								</div>
							</div>
						{/each}
					</div>
				</section>
			</div>
		{/if}

		<!-- ═══════════════════════ ÉTAPE 4 : RENDER ═══════════════════════ -->
		{#if activeStep === 'RENDER'}
			<section class="card">
				<div style="display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 0.6rem; margin-bottom: 1rem;">
					<div>
						<h2 style="margin: 0;">4. Préparation LTX-2.5, Coûts Réels & Rendu Unitaire</h2>
						<small style="color: var(--muted);">
							Limite stricte : 150 mots par prompt · Une seule prise à la fois · Zéro retry GPU automatique · Autorisation explicite obligatoire.
						</small>
					</div>
					<div style="display: flex; gap: 0.5rem;">
						<button onclick={() => runPrepare(true)} disabled={busy}>
							Recompiler les prompts depuis le scénario (0 €)
						</button>
						<button class="primary" onclick={() => (activeStep = 'EXPORT')}>
							Étape 5 : Export MP4 →
						</button>
					</div>
				</div>

				<div style="display: grid; gap: 1rem;">
					{#each project.shots as shot}
						{@const rep = prepReports[shot.id]}
						{@const wc = wordCount(shot.ltx_prompt)}
						<div style="border: 1px solid var(--border); border-radius: 10px; padding: 1rem; background: var(--panel-alt);">
							<div style="display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0.5rem; margin-bottom: 0.6rem;">
								<div style="display: flex; align-items: center; gap: 0.55rem; flex-wrap: wrap;">
									<strong style="font-size: 1.05rem;">{shot.id}</strong>
									<span class="badge {wc > 150 || wc === 0 ? 'err' : 'ok'}">
										Prompt : {wc} / 150 mots
									</span>
									{#if rep?.ready_for_gpu}
										<span class="badge ok">Prêt pour GPU ({rep.cost.frames} frames)</span>
									{:else}
										<span class="badge warn">Bloqué avant GPU</span>
									{/if}
									{#if shot.rendered_video_ref}
										<span class="badge {shot.human_validated ? 'ok' : 'warn'}">
											{shot.human_validated ? 'Prise validée' : 'Prise à visionner'}
										</span>
									{/if}
								</div>
							</div>

							{#if rep?.blocking_reason}
								<div style="padding: 0.55rem 0.75rem; border-radius: 7px; background: #361909; border: 1px solid #92400e; color: #fde68a; font-size: 0.86rem; margin-bottom: 0.65rem;">
									<strong>Prérequis avant GPU :</strong> {rep.blocking_reason}
								</div>
							{/if}

							<label style="display: block; margin-bottom: 0.7rem;">
								Prompt LTX-2.5 envoyé au moteur (éditable) :
								<textarea rows="3" bind:value={shot.ltx_prompt}></textarea>
							</label>

							<!-- Estimation de coût fournisseur + Autorisation explicite -->
							{#if rep?.cost}
								<div style="padding: 0.75rem; border-radius: 8px; background: #0f141d; border: 1px solid var(--border); margin-bottom: 0.75rem;">
									<div style="display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0.5rem;">
										<div>
											<strong>Estimation fournisseur ({rep.cost.gpu_label}) :</strong>
											{#if rep.cost.estimated_cost_eur !== null}
												<span style="color: var(--accent); font-weight: 700; margin-left: 0.35rem;">
													~{rep.cost.estimated_cost_eur.toFixed(4)} €
												</span>
												<span style="color: var(--muted); font-size: 0.85rem;">
													(chaud min : {rep.cost.warm_min_eur?.toFixed(4)} € · pire cas : {rep.cost.worst_case_eur?.toFixed(4)} €)
												</span>
											{:else}
												<span class="badge warn">None — configurez le tarif GPU réel dans Paramètres</span>
											{/if}
										</div>
										<div style="font-size: 0.82rem; color: var(--muted);">
											{rep.cost.calibration_source}
										</div>
									</div>
									<ul style="margin: 0.45rem 0 0.65rem 1.1rem; padding: 0; font-size: 0.81rem; color: var(--muted);">
										{#each rep.cost.uncertainties as u}
											<li>{u}</li>
										{/each}
									</ul>

									<div style="display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; padding-top: 0.5rem; border-top: 1px solid #1e2736;">
										<label style="display: flex; align-items: center; gap: 0.4rem; width: auto; cursor: pointer;">
											<input
												type="checkbox"
												style="width: auto;"
												bind:checked={authorizedByShot[shot.id]}
											/>
											<span>
												J'autorise explicitement le paiement de <strong>1 prise GPU</strong> pour `{shot.id}`
											</span>
										</label>

										<label style="display: flex; align-items: center; gap: 0.35rem; width: auto;">
											Plafond (€)
											<input
												type="number"
												step="0.05"
												style="width: 90px;"
												bind:value={ceilingByShot[shot.id]}
											/>
										</label>

										<button
											class="primary"
											disabled={busy || !authorizedByShot[shot.id] || wc > 150}
											onclick={() => handleRenderSingleShot(shot.id)}
										>
											{shot.rendered_video_ref
												? `Refaire uniquement ${shot.id} (GPU)`
												: `Générer ${shot.id} sur Modal`}
										</button>

										<label style="width: auto; cursor: pointer; border: 1px solid var(--border); padding: 0.45rem 0.8rem; border-radius: 8px;">
											Importer un MP4 local (0 €)
											<input
												type="file"
												accept="video/mp4,video/*"
												style="display: none;"
												onchange={(e) =>
													handleUploadManualTake(shot.id, (e.target as HTMLInputElement).files)}
											/>
										</label>
									</div>
								</div>
							{/if}

							<!-- Visionnage et validation humaine de la prise rendue -->
							{#if shot.rendered_video_ref}
								{@const latestTake = takes.find((t) => t.shot_id === shot.id)}
								<div style="display: grid; grid-template-columns: 220px 1fr; gap: 1rem; align-items: start; margin-top: 0.6rem;">
									<video
										controls
										src={mediaUrl(shot.rendered_video_ref)}
										style="width: 220px; border-radius: 8px; background: #000;"
									>
										<track kind="captions" />
									</video>
									<div>
										<h4 style="margin: 0 0 0.4rem;">Contrôle qualité & Validation humaine</h4>
										{#if latestTake?.qc}
											<div style="font-size: 0.84rem; display: grid; gap: 0.25rem; margin-bottom: 0.6rem;">
												<div>
													<strong>Média :</strong>
													{latestTake.qc.media?.width}×{latestTake.qc.media?.height},
													{latestTake.qc.media?.duration_s}s, {latestTake.qc.media?.fps} fps,
													audio={latestTake.qc.media?.has_audio ? 'oui' : 'non'}
												</div>
												<div>
													<strong>OCR :</strong> {latestTake.qc.ocr?.status} — {latestTake.qc.ocr?.reason}
												</div>
												<div>
													<strong>Whisper :</strong> {latestTake.qc.speech?.status}
													{#if latestTake.qc.transcript?.text}
														« {latestTake.qc.transcript.text} » (début parole :
														{latestTake.qc.transcript.first_word_start_sec ?? '?'}s)
													{:else}
														— {latestTake.qc.speech?.reason}
													{/if}
												</div>
												{#if latestTake.actual_cost_eur !== null}
													<div>
														<strong>Coût enregistré :</strong> {latestTake.actual_cost_eur} €
														({latestTake.observed_compute_s ?? 0}s GPU)
													</div>
												{/if}
											</div>
										{/if}
										<div style="display: flex; gap: 0.5rem; align-items: center;">
											<input
												placeholder="Notes de visionnage humain..."
												bind:value={reviewNotesByShot[shot.id]}
											/>
											<button
												class="primary"
												onclick={async () => {
													if (!project) return;
													const r = await api.reviewTake(
														project.id,
														shot.id,
														true,
														reviewNotesByShot[shot.id] || 'Validé au visionnage'
													);
													project = r.project;
													takes = await api.listTakes(project.id);
												}}
											>
												✓ Valider la prise
											</button>
										</div>
									</div>
								</div>
							{/if}
						</div>
					{/each}
				</div>
			</section>
		{/if}

		<!-- ═══════════════════════ ÉTAPE 5 : EXPORT ═══════════════════════ -->
		{#if activeStep === 'EXPORT'}
			<section class="card">
				<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.6rem; margin-bottom: 1rem;">
					<div>
						<h2 style="margin: 0;">5. Assemblage FFmpeg MP4 Vertical (1080×1920, 24 fps, AAC 48 kHz)</h2>
						<small style="color: var(--muted);">
							Assemble les prises dans l'ordre du scénario, incruste les zones de chiffres fixes (`display_overlay`) et vérifie le fichier livré.
						</small>
					</div>
					<button
						class="primary"
						disabled={busy || project.shots.some((s) => !s.rendered_video_ref)}
						onclick={handleExportMp4}
					>
						Assembler & Vérifier le MP4 final
					</button>
				</div>

				{#if project.shots.some((s) => !s.rendered_video_ref)}
					<p style="color: var(--warn);">
						Plans en attente d'une prise vidéo avant export :
						<strong>
							{project.shots
								.filter((s) => !s.rendered_video_ref)
								.map((s) => s.id)
								.join(', ')}
						</strong>
					</p>
				{/if}

				{#if exportsList.length > 0}
					<div style="display: grid; gap: 1rem; margin-top: 1rem;">
						{#each exportsList as exp}
							<div style="border: 1px solid var(--border); border-radius: 10px; padding: 1rem; background: var(--panel-alt); display: grid; grid-template-columns: 250px 1fr; gap: 1.1rem;">
								<video
									controls
									src={mediaUrl(exp.output_path)}
									style="width: 250px; border-radius: 8px; background: #000;"
								>
									<track kind="captions" />
								</video>
								<div>
									<h3 style="margin-top: 0;">
										Export `{exp.id}` — {exp.width}×{exp.height} · {exp.duration_s.toFixed(2)}s
									</h3>
									<p style="font-size: 0.85rem; word-break: break-all; color: var(--muted);">
										Fichier : <code>{exp.output_path}</code>
									</p>
									{#if exp.qc}
										<div style="display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.6rem;">
											<span class="badge {exp.qc.ok ? 'ok' : 'err'}">
												Livraison {exp.qc.ok ? 'CONFORME (48 kHz stéréo)' : 'ERREUR'}
											</span>
											<span class="badge {exp.qc.ocr?.status === 'not_detected' ? 'ok' : 'warn'}">
												OCR : {exp.qc.ocr?.status}
											</span>
											<span class="badge {exp.qc.speech?.status === 'ok' ? 'ok' : 'warn'}">
												Whisper : {exp.qc.speech?.status}
											</span>
										</div>
										{#if exp.qc.warnings?.length > 0}
											<ul style="font-size: 0.84rem; color: #fde68a;">
												{#each exp.qc.warnings as w}
													<li>{w}</li>
												{/each}
											</ul>
										{/if}
									{/if}
									<a
										href={mediaUrl(exp.output_path)}
										download="{project.id}.mp4"
										style="display: inline-block; margin-top: 0.5rem; padding: 0.5rem 0.95rem; border-radius: 8px; background: var(--accent-strong); color: #fff; text-decoration: none; font-weight: 600;"
									>
										Télécharger le MP4 vertical
									</a>
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</section>
		{/if}
	{/if}
</div>
