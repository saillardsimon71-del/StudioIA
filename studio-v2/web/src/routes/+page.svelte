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
	let metaImagePrompt = $state('');
	let imageErrors = $state<Record<string, string>>({});
	let missingImages = $state<string[]>([]);
	let imagePromptDrafts = $state<Record<string, string>>({});
	let imageStage = $state('');
	let imageBatchRunning = $state(false);
	let stopImageBatch = $state(false);
	let showScenarioTransfer = $state(false);
	let scenarioText = $state('');
	let dragOverKey = $state('');

	// Autorisation explicite par plan (jamais activée par défaut)
	let authorizedByShot = $state<Record<string, boolean>>({});
	let reviewNotesByShot = $state<Record<string, string>>({});
	let batchAuthorized = $state(false);
	let videoBatchRunning = $state(false);
	let stopVideoBatch = $state(false);
	let currentVideoShot = $state('');
	let videoErrors = $state<Record<string, string>>({});
	let pendingVideoShots = $derived(project?.shots.filter((shot) => !(prepReports[shot.id]?.video_available ?? Boolean(shot.rendered_video_ref))) ?? []);

	// Paramètres éditables
	let apiKeyInput = $state('');
	let metaAiKeyInput = $state('');

	async function refreshGlobal() {
		const [h, s, plist, alist, cost] = await Promise.all([
			api.health(),
			api.getSettings(),
			api.listProjects(),
			api.listAssets(),
			api.costLedger()
		]);
		health = h;
		api.modalStatus().then((status) => { modalStatus = status; }).catch((error) => {
			modalStatus = { ok: false, message: error.message };
		});
		settings = s;
		projectList = plist;
		assets = alist;
		ledger = cost;
	}

	async function selectProject(id: string) {
		errorBanner = '';
		imageErrors = {};
		missingImages = [];
		prepReports = {};
		imagePromptDrafts = {};
		videoErrors = {};
		batchAuthorized = false;
		showScenarioTransfer = false;
		scenarioText = '';
		const [p, t, ex] = await Promise.all([
			api.getProject(id),
			api.listTakes(id),
			api.listExports(id)
		]);
		project = p;
		takes = t;
		exportsList = ex;
		authorizedByShot = {};
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
			imageErrors = {};
			imagePromptDrafts = {};
			videoErrors = {};
			batchAuthorized = false;
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

	async function copyScenario() {
		if (!project || busy) return;
		scenarioText = JSON.stringify(project, null, 2);
		try {
			await navigator.clipboard.writeText(scenarioText);
			infoBanner = 'Scénario complet copié : idée, personnages, décors, plans, dialogues et références.';
		} catch {
			showScenarioTransfer = true;
			infoBanner = 'Sélectionnez le texte ci-dessous et copiez-le avec Ctrl+C.';
		}
	}

	async function replaceScenario() {
		if (!project || busy) return;
		busy = true;
		errorBanner = '';
		infoBanner = '';
		try {
			project = await api.replaceScenario(project.id, scenarioText);
			imageErrors = {};
			imagePromptDrafts = {};
			videoErrors = {};
			batchAuthorized = false;
			authorizedByShot = {};
			showScenarioTransfer = false;
			scenarioText = '';
			await refreshGlobal();
			await runPrepare(false);
			infoBanner = 'Scénario remplacé et enregistré. Vérifiez les images des plans modifiés avant le rendu.';
		} catch (e: any) {
			errorBanner = e instanceof SyntaxError ? 'JSON invalide. Collez le scénario complet copié, puis corrigé.' : e.message;
		} finally {
			busy = false;
		}
	}

	function addDecor() {
		if (!project) return;
		let idx = project.decors.length + 1;
		while (project.decors.some((decor) => decor.id === `decor_${idx}`)) idx++;
		project.decors = [...project.decors, {
			id: `decor_${idx}`, name: `Décor ${idx}`, description: 'Lieu à décrire : mobilier, couleurs, lumière et style visuel.', asset_id: null
		}];
	}

	function removeDecor(id: string) {
		if (!project) return;
		project.decors = project.decors.filter((decor) => decor.id !== id);
		for (const shot of project.shots) if (shot.decor_id === id) shot.decor_id = null;
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
				image_prompt: '',
				image_ref: null,
				continue_from_previous: idx > 1,
				decor_id: project.decors[0]?.id ?? null,
				decor_asset_id: null,
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
		if (busy || !files || files.length === 0) return;
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

	async function generateLibraryImage() {
		if (libKind === 'decor' ? !libName.trim() : !metaImagePrompt.trim()) {
			errorBanner = libKind === 'decor' ? 'Saisissez le nom du décor.' : 'Saisissez un prompt pour générer une image.';
			return;
		}
		busy = true;
		errorBanner = '';
		try {
			if (libKind === 'decor') {
				await api.generateDecor(libName.trim(), libDesc.trim());
			} else {
				await api.generateLibraryAsset(metaImagePrompt.trim(), libKind, libName || 'Image Meta AI', libDesc);
			}
			assets = await api.listAssets();
			infoBanner = 'Image générée par Meta AI et ajoutée à la bibliothèque. Vérifiez-la avant utilisation.';
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
		}
	}

	async function editImagePrompt(shotId: string) {
		if (!project || busy) return;
		if (imagePromptDrafts[shotId] !== undefined) {
			const drafts = { ...imagePromptDrafts };
			delete drafts[shotId];
			imagePromptDrafts = drafts;
			return;
		}
		busy = true;
		errorBanner = '';
		try {
			const preview = await api.getShotImagePrompt(project.id, shotId);
			imagePromptDrafts = { ...imagePromptDrafts, [shotId]: preview.prompt };
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
		}
	}

	async function saveImagePrompt(shotId: string) {
		if (!project || busy) return;
		busy = true;
		errorBanner = '';
		try {
			project = await api.saveShotImagePrompt(project.id, shotId, imagePromptDrafts[shotId]);
			infoBanner = `Prompt image de ${shotId} enregistré. L'action vidéo et les dialogues sont conservés.`;
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
		}
	}

	async function generateShotImages(shotId?: string) {
		if (!project || busy) return;
		busy = true;
		imageBatchRunning = !shotId;
		stopImageBatch = false;
		errorBanner = '';
		infoBanner = '';
		try {
			project = await api.saveProject(project);
			if (!shotId) {
				activeStep = 'ASSETS';
			}
			await runPrepare(false);
			for (const shot of project.shots) {
				const draft = imagePromptDrafts[shot.id];
				if ((!shot.image_ref || missingImages.includes(shot.id)) && (!shotId || shot.id === shotId) && draft !== undefined && draft.trim() !== shot.image_prompt) {
					project = await api.saveShotImagePrompt(project.id, shot.id, draft);
				}
			}
			const onUpdate = (updated: Project, id: string, error: string) => {
				project = updated;
				imageErrors = { ...imageErrors, [id]: error };
				if (error) errorBanner = error;
			};
			project = shotId
				? await api.generateShotImages(project, onUpdate, [shotId], undefined, missingImages)
				: await api.generateProjectImages(project, onUpdate, (stage) => { imageStage = stage; }, () => stopImageBatch, missingImages);
			assets = await api.listAssets();
			await runPrepare(false);
			if (stopImageBatch) infoBanner = 'Lot arrete. Les images deja enregistrees sont conservees.';
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
			imageStage = '';
			imageBatchRunning = false;
		}
	}

	async function generateReferenceImage(kind: 'character' | 'decor', id: string) {
		if (!project || busy) return;
		busy = true;
		errorBanner = '';
		try {
			project = await api.saveProject(project);
			project = await api.generateReferenceImage(project.id, kind, id);
			imageErrors = { ...imageErrors, [`${kind}:${id}`]: '' };
			assets = await api.listAssets();
		} catch (e: any) {
			imageErrors = { ...imageErrors, [`${kind}:${id}`]: e.message };
		} finally {
			busy = false;
		}
	}

	async function handleDecorImageFile(decorId: string, files: FileList | null) {
		if (!project || busy || !files?.length) return;
		busy = true;
		errorBanner = '';
		try {
			const decor = project.decors.find((item) => item.id === decorId)!;
			const asset = await api.uploadLibraryAsset(files[0], 'decor', decor.name, decor.description);
			project = await api.assignAssetToDecor(project.id, decorId, asset.id);
			imageErrors = { ...imageErrors, [`decor:${decorId}`]: '' };
			assets = await api.listAssets();
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			busy = false;
		}
	}

	async function handleShotImageFile(shotId: string, files: FileList | null) {
		if (!project || busy || !files || files.length === 0) return;
		busy = true;
		errorBanner = '';
		try {
			project = await api.uploadShotImage(project.id, shotId, files[0]);
			imageErrors = { ...imageErrors, [shotId]: '' };
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
		if (!project || busy || !files || files.length === 0) return;
		busy = true;
		errorBanner = '';
		try {
			project = await api.uploadCharacterImage(project.id, charId, files[0]);
			imageErrors = { ...imageErrors, [`character:${charId}`]: '' };
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
			missingImages = res.missing_images;
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
		if (!project || busy) return;
		busy = true;
		errorBanner = '';
		infoBanner = '';
		try {
			await api.saveProject(project);
			const res = await api.renderShot(
				project.id,
				shotId,
				Boolean(authorizedByShot[shotId])
			);
			project = res.project;
			videoErrors = { ...videoErrors, [shotId]: '' };
			authorizedByShot[shotId] = false; // Réinitialise l'autorisation unitaire après usage
			takes = await api.listTakes(project.id);
			ledger = await api.costLedger();
			await runPrepare(false);
			infoBanner = `Prise ${shotId} terminée. Visionnez la vidéo et contrôlez le rapport OCR/Whisper.`;
		} catch (e: any) {
			errorBanner = e.message;
			videoErrors = { ...videoErrors, [shotId]: e.message };
			takes = await api.listTakes(project.id);
			ledger = await api.costLedger();
		} finally {
			busy = false;
		}
	}

	async function handleRenderAllShots() {
		if (!project || busy || !batchAuthorized || !pendingVideoShots.length) return;
		batchAuthorized = false;
		authorizedByShot = {};
		busy = true;
		videoBatchRunning = true;
		stopVideoBatch = false;
		currentVideoShot = '';
		errorBanner = '';
		infoBanner = '';
		let failed = false;
		try {
			project = await api.saveProject(project);
			project = await api.renderMissingShots(project, {
				authorizedByHuman: true,
				onStart: (id) => {
					currentVideoShot = id;
					if (prepReports[id]) prepReports[id] = { ...prepReports[id], ready_for_gpu: true, blocking_reason: null };
				},
				shouldStop: () => stopVideoBatch,
				onUpdate: (updated, id, error) => {
					project = updated;
					videoErrors = { ...videoErrors, [id]: error };
					if (error) {
						failed = true;
						errorBanner = `Rendu arrêté au plan ${id} : ${error}`;
					}
				}
			});
			const [saved, latestTakes, latestLedger] = await Promise.all([
				api.getProject(project.id), api.listTakes(project.id), api.costLedger()
			]);
			project = saved;
			takes = latestTakes;
			ledger = latestLedger;
			await runPrepare(false);
			if (!failed) {
				infoBanner = stopVideoBatch
					? 'Lot arrêté. Les vidéos terminées sont conservées ; vous pouvez reprendre les plans manquants.'
					: 'Toutes les vidéos sont générées. Visionnez les prises avant le montage final.';
			}
		} catch (e: any) {
			errorBanner = e.message;
		} finally {
			videoBatchRunning = false;
			currentVideoShot = '';
			busy = false;
		}
	}

	async function handleUploadManualTake(shotId: string, files: FileList | null) {
		if (!project || busy || !files || files.length === 0) return;
		busy = true;
		errorBanner = '';
		try {
			const res = await api.uploadManualTake(project.id, shotId, files[0]);
			project = res.project;
			videoErrors = { ...videoErrors, [shotId]: '' };
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
				whisper_model_or_path: settings.whisper_model_or_path,
				ocr_enabled: Boolean(settings.ocr_enabled)
			};
			patch.metaai_api_url = settings.metaai_api_url;
			if (apiKeyInput.trim()) {
				patch.deepseek_api_key = apiKeyInput.trim();
			}
			if (metaAiKeyInput.trim()) {
				patch.metaai_api_key = metaAiKeyInput.trim();
			}
			settings = await api.updateSettings(patch);
			apiKeyInput = '';
			metaAiKeyInput = '';
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
			{#if health?.restart_required}<span class="badge err">Serveur à redémarrer : fichiers modifiés</span>{/if}
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
					disabled={busy}
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
					<label>URL du service metaai-api
						<input placeholder="http://127.0.0.1:8000" bind:value={settings.metaai_api_url} />
					</label>
					<label>Clé API MetaAI ({settings.metaai_api_key_configured ? 'configurée' : 'optionnelle / absente'})
						<input type="password" placeholder="Clé du serveur metaai-api si définie" bind:value={metaAiKeyInput} />
					</label>
					<small>Les cookies Meta restent configurés uniquement sur le serveur metaai-api, jamais dans StudioIA.</small>
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
			<h3 style="margin-top: 0;">Journal des coûts calculés (`v2.db` & `COSTS.md`)</h3>
			<p>Les montants calculés d'après le temps du worker restent des estimations. Le relevé Modal fait foi.</p>
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
								<th>Coût calculé</th>
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
				disabled={busy}
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
				<div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">
					<button disabled={busy} onclick={copyScenario}>Copier tout le scénario</button>
					<button disabled={busy} onclick={() => { scenarioText = ''; showScenarioTransfer = !showScenarioTransfer; }}>
						Remplacer tout le scénario
					</button>
				</div>
				{#if showScenarioTransfer}
					<label style="margin-top: 0.75rem;">Scénario complet (JSON)
						<textarea rows="12" bind:value={scenarioText} disabled={busy} placeholder="Collez ici le scénario copié et corrigé, avec characters, decors et shots."></textarea>
					</label>
					<p>Le contenu est vérifié avant remplacement. Les fichiers de la bibliothèque et l'historique des prises sont conservés.</p>
					<button class="primary" disabled={busy || !scenarioText.trim()} onclick={replaceScenario}>Vérifier et remplacer</button>
					<button disabled={busy} onclick={() => { showScenarioTransfer = false; scenarioText = ''; }}>Annuler</button>
				{/if}
			</section>
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

			<section class="card" style="margin-bottom: 1rem;">
				<div style="display: flex; justify-content: space-between; align-items: center; gap: 0.5rem;">
					<h2>Décors récurrents</h2>
					<button disabled={busy} onclick={addDecor}>
						Ajouter un décor
					</button>
				</div>
				<p>Les lieux sont préparés avec le scénario. Après validation, leurs images sont générées une fois avant les images des plans.</p>
				<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 0.75rem;">
					{#each project.decors as decor}
						<div>
							<label>Nom du décor<input bind:value={decor.name} /></label>
							<label>Description visuelle<textarea rows="3" bind:value={decor.description}></textarea></label>
							<button disabled={busy} onclick={() => removeDecor(decor.id)}>Supprimer ce décor</button>
						</div>
					{/each}
					{#if !project.decors.length}<p>Ce projet n'a pas encore de décors déclarés. Ajoutez ses lieux ici ou remplacez le scénario complet.</p>{/if}
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
						<button onclick={() => generateShotImages()} disabled={busy || !project.shots.length}>
							Valider le scénario et générer les images
						</button>
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

							<label style="margin-bottom: 0.6rem;">Décor du plan
								<select value={shot.decor_id ? `decor:${shot.decor_id}` : shot.decor_asset_id ? `asset:${shot.decor_asset_id}` : ''}
									onchange={(e) => {
										const value = (e.target as HTMLSelectElement).value;
										shot.decor_id = value.startsWith('decor:') ? value.slice(6) : null;
										shot.decor_asset_id = value.startsWith('asset:') ? value.slice(6) : null;
									}}>
									<option value="">D'après les informations du scénario</option>
									{#each project.decors as decor}
										<option value={`decor:${decor.id}`}>{decor.name}</option>
									{/each}
									{#each assets.filter((asset) => asset.kind === 'decor') as decor}
										<option value={`asset:${decor.id}`}>Bibliothèque : {decor.name}</option>
									{/each}
								</select>
							</label>
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
										<select aria-label="Type de voix" style="width: 145px;"
											value={line.delivery ?? 'on_screen'}
											onchange={(e) => line.delivery = e.currentTarget.value as 'on_screen' | 'voice_over' | 'off_screen'}>
											<option value="on_screen">Dialogue visible</option>
											<option value="voice_over">Voix off</option>
											<option value="off_screen">Hors champ</option>
										</select>
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
						<input placeholder={libKind === 'decor' ? 'Nom du décor (cantine, cellule, promenade)' : 'Nom du visuel (ex. Inspecteur Zeste face)'} bind:value={libName} />
						{#if libKind === 'decor'}
							<textarea placeholder="Description du lieu : mobilier, murs, lumière, ambiance..." bind:value={libDesc} rows="3"></textarea>
							<small>Le prompt du décor est préparé automatiquement, sans personnages. Sa description sera réutilisée dans les plans associés.</small>
						{:else}
							<textarea
								placeholder="Prompt d'image — description, cadrage, style…"
								bind:value={metaImagePrompt}
								rows="3"
							></textarea>
						{/if}
						<button class="primary" disabled={busy || !settings?.metaai_api_url} onclick={generateLibraryImage}>
							{busy ? 'Génération…' : libKind === 'decor' ? 'Générer le décor avec Meta AI' : 'Générer avec Meta AI'}
						</button>
						<p style="color: var(--muted); font-size: 0.78rem; margin: 0;">
							Nécessite le service metaai-api configuré dans Paramètres. La génération peut prendre 30–40 s.
						</p>
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
						<button class="primary" disabled={busy} onclick={() => (activeStep = 'RENDER')}>Étape 4 : Render →</button>
					</div>

					<h4 style="margin-bottom: 0.5rem;">Références personnages</h4>
					<p role="status" aria-live="polite">
						Personnages : {project.characters.filter((char) => char.image_ref).length}/{project.characters.length}
						- Décors : {project.decors.filter((decor) => decor.asset_id).length}/{project.decors.length}
						{#if imageStage}<br />En cours : {imageStage}{/if}
						{#if imageBatchRunning && stopImageBatch}<br />Arrêt demandé après le visuel en cours.{/if}
					</p>
					{#if imageBatchRunning}<button disabled={stopImageBatch} onclick={() => { stopImageBatch = true; }}>Arrêter après cette image</button>{/if}
					<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.65rem; margin-bottom: 1.2rem;">
						{#each project.characters as char}
							<div style="border: 1px solid var(--border); border-radius: 8px; padding: 0.65rem; background: var(--panel-alt);">
								<strong>{char.name}</strong>
								{#if imageErrors[`character:${char.id}`]}<p role="alert">{char.name} : {imageErrors[`character:${char.id}`]}</p>{/if}
								{#if !char.image_ref || missingImages.includes(`character:${char.id}`)}
									<button disabled={busy} onclick={() => generateReferenceImage('character', char.id)}>
										{imageErrors[`character:${char.id}`] ? 'Relancer ce personnage' : 'Générer ce personnage'}
									</button>
								{/if}
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
									disabled={busy}
									onchange={(e) => handleCharImageFile(char.id, (e.target as HTMLInputElement).files)}
								/>
							</div>
						{/each}
					</div>

					<h4>Références décors</h4>
					<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.65rem; margin-bottom: 1.2rem;">
						{#each project.decors as decor}
							{@const reference = assets.find((asset) => asset.id === decor.asset_id)}
							<div style="border: 1px solid var(--border); border-radius: 8px; padding: 0.65rem;">
								<strong>{decor.name}</strong>
								{#if reference}<img src={mediaUrl(reference.file_path)} alt={decor.name} style="width: 100%; height: 150px; object-fit: cover; border-radius: 6px;" />{/if}
								{#if imageErrors[`decor:${decor.id}`]}<p role="alert">{decor.name} : {imageErrors[`decor:${decor.id}`]}</p>{/if}
								{#if !decor.asset_id || missingImages.includes(`decor:${decor.id}`)}
									<button disabled={busy} onclick={() => generateReferenceImage('decor', decor.id)}>
										{imageErrors[`decor:${decor.id}`] ? 'Relancer ce décor' : 'Générer ce décor'}
									</button>
								{/if}
								<label>Importer une autre image<input type="file" accept="image/*" disabled={busy}
									onchange={(e) => handleDecorImageFile(decor.id, (e.target as HTMLInputElement).files)} /></label>
								<select disabled={busy} value={decor.asset_id ?? ''} aria-label={`Image du décor ${decor.name}`}
									onchange={async (e) => {
										const id = (e.target as HTMLSelectElement).value;
										if (!project || !id || busy) return;
										busy = true;
										try {
											project = await api.assignAssetToDecor(project.id, decor.id, id);
											imageErrors = { ...imageErrors, [`decor:${decor.id}`]: '' };
										} catch (e: any) { errorBanner = e.message; } finally { busy = false; }
									}}>
									<option value="">Choisir dans la bibliothèque...</option>
									{#each assets.filter((asset) => asset.kind === 'decor') as asset}<option value={asset.id}>{asset.name}</option>{/each}
								</select>
							</div>
						{/each}
					</div>

					<h4 style="margin-bottom: 0.5rem;">Images initiales des plans (validation humaine requise)</h4>
					<p role="status" aria-live="polite">
						Images générées : {project.shots.filter((shot) => shot.image_ref).length}/{project.shots.length}
					</p>
					<button disabled={busy || (!missingImages.length && !project.shots.some((shot) => !shot.image_ref) && !project.characters.some((char) => !char.image_ref) && !project.decors.some((decor) => !decor.asset_id))} onclick={() => generateShotImages()}>
						Générer les images manquantes
					</button>
					<p style="font-size: 0.85rem; color: var(--muted);">Un visuel est refusé ? Ouvrez « Adapter le visuel » sur le plan, décrivez une pose calme avant le geste, puis relancez ce plan. L'acceptation finale dépend de Meta AI.</p>
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
									{#if shot.continue_from_previous && !shot.image_ref}
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
									{#if imageErrors[shot.id]}
										<p role="alert">Échec du plan {shot.id} : {imageErrors[shot.id]}</p>
									{/if}
									{#if !shot.image_ref || missingImages.includes(shot.id)}
										<button disabled={busy} onclick={() => editImagePrompt(shot.id)}>
											{imagePromptDrafts[shot.id] === undefined ? 'Adapter le visuel' : 'Fermer le prompt'}
										</button>
										{#if imagePromptDrafts[shot.id] !== undefined}
											<label for="image-prompt-{shot.id}">Prompt image Meta AI</label>
											<textarea id="image-prompt-{shot.id}" rows="7" maxlength="4000" disabled={busy} bind:value={imagePromptDrafts[shot.id]}></textarea>
											<p style="font-size: 0.82rem; color: var(--muted);">Ce texte sert uniquement à l'image. Gardez personnages, vêtements, lieu et cadrage ; décrivez une pose au repos, sans blessure visible ni contact violent. Champ vide : retour au prompt automatique.</p>
											<button disabled={busy} onclick={() => saveImagePrompt(shot.id)}>Enregistrer le prompt</button>
										{/if}
										<button disabled={busy} onclick={() => generateShotImages(shot.id)}>
											{imagePromptDrafts[shot.id] !== undefined ? 'Enregistrer et relancer ce plan' : imageErrors[shot.id] ? 'Relancer ce plan' : 'Générer cette image'}
										</button>
									{/if}
										<div style="display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;">
											<label style="width: auto; cursor: pointer; border: 1px solid var(--border); padding: 0.4rem 0.75rem; border-radius: 7px; background: #0f131b;">
												Importer PNG/JPG
												<input
													type="file"
													disabled={busy}
													accept="image/*"
													style="display: none;"
													onchange={(e) =>
														handleShotImageFile(shot.id, (e.target as HTMLInputElement).files)}
												/>
											</label>
											{#if assets.length > 0}
												<select
													disabled={busy}
													style="width: auto; max-width: 220px;"
													onchange={async (e) => {
														const aid = (e.target as HTMLSelectElement).value;
														if (aid && project) {
															project = await api.assignAssetToShot(project.id, shot.id, aid);
															imageErrors = { ...imageErrors, [shot.id]: '' };
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
													disabled={busy}
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
						<h2 style="margin: 0;">4. Préparation LTX-2.5 et génération des vidéos</h2>
						<small style="color: var(--muted);">
						Garde-fou local : {health?.max_ltx_words ?? 300} mots par prompt · Une seule prise à la fois · Zéro retry GPU automatique · Autorisation explicite obligatoire.
						</small>
					</div>
					<div style="display: flex; gap: 0.5rem;">
						<button onclick={() => runPrepare(true)} disabled={busy}>
							Recompiler les prompts depuis le scénario (0 €)
						</button>
						<button class="primary" disabled={busy} onclick={() => (activeStep = 'EXPORT')}>
							Étape 5 : Export MP4 →
						</button>
					</div>
				</div>

				<div style="padding: 0.9rem; margin-bottom: 1rem; border: 1px solid var(--border); border-radius: 8px; background: var(--panel-alt);">
					<p role="status" aria-live="polite" style="margin-top: 0;">
						Vidéos générées : {project.shots.filter((shot) => shot.rendered_video_ref).length}/{project.shots.length}
						{#if videoBatchRunning}<br />{currentVideoShot ? `En cours : ${currentVideoShot}` : 'Préparation du lot...'}{/if}
						{#if videoBatchRunning && stopVideoBatch}<br />Arrêt demandé après le plan en cours.{/if}
					</p>
					<p>{pendingVideoShots.length} plan(s) restant(s). Gardez cet onglet ouvert pendant le lot.</p>
					<label style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.7rem;">
						<input type="checkbox" style="width: auto;" bind:checked={batchAuthorized} disabled={busy || !pendingVideoShots.length} />
						J'autorise une prise GPU payante pour chaque plan manquant.
					</label>
					<button class="primary" onclick={handleRenderAllShots}
						disabled={busy || !batchAuthorized || !pendingVideoShots.length}>
						Générer toutes les vidéos manquantes
					</button>
					{#if videoBatchRunning}
						<button disabled={stopVideoBatch} onclick={() => { stopVideoBatch = true; }}>Arrêter après ce plan</button>
					{/if}
				</div>

				<div style="display: grid; gap: 1rem;">
					{#each project.shots as shot}
						{@const rep = prepReports[shot.id]}
						{@const wc = wordCount(shot.ltx_prompt)}
						<div style="border: 1px solid var(--border); border-radius: 10px; padding: 1rem; background: var(--panel-alt);">
							<div style="display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0.5rem; margin-bottom: 0.6rem;">
								<div style="display: flex; align-items: center; gap: 0.55rem; flex-wrap: wrap;">
									<strong style="font-size: 1.05rem;">{shot.id}</strong>
									<span class="badge {wc > (health?.max_ltx_words ?? 300) || wc === 0 ? 'err' : 'ok'}">
										Prompt : {wc} / {health?.max_ltx_words ?? 300} mots
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
							{#if videoErrors[shot.id]}<p role="alert">Échec du plan {shot.id} : {videoErrors[shot.id]}</p>{/if}
							{#each rep?.warnings ?? [] as warning}<p class="badge warn">{warning}</p>{/each}

							<label style="display: block; margin-bottom: 0.7rem;">
								Prompt LTX-2.5 envoyé au moteur (éditable) :
								<textarea rows="3" readonly value={shot.ltx_prompt} aria-label="Prompt LTX compilé depuis le scénario"></textarea>
							</label>

							<div style="display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-bottom: 0.75rem;">
								<label style="display: flex; align-items: center; gap: 0.4rem; width: auto; cursor: pointer;">
									<input
										type="checkbox"
										style="width: auto;"
										bind:checked={authorizedByShot[shot.id]}
										disabled={busy}
									/>
									<span>
										J'autorise explicitement le paiement de <strong>1 prise GPU</strong> pour `{shot.id}`
									</span>
								</label>

								<button
									class="primary"
									disabled={busy || (!rep?.render_pending && (!authorizedByShot[shot.id] || wc === 0 || wc > (health?.max_ltx_words ?? 300)))}
									onclick={() => handleRenderSingleShot(shot.id)}
								>
									{rep?.render_pending ? `Récupérer ${shot.id} (même prise)` : shot.rendered_video_ref
										? `Refaire uniquement ${shot.id} (GPU)`
										: `Générer ${shot.id} sur Modal`}
								</button>

								<label style="width: auto; cursor: pointer; border: 1px solid var(--border); padding: 0.45rem 0.8rem; border-radius: 8px;">
									Importer un MP4 local (0 €)
									<input
										type="file"
										accept="video/mp4,video/*"
										disabled={busy}
										style="display: none;"
										onchange={(e) =>
											handleUploadManualTake(shot.id, (e.target as HTMLInputElement).files)}
									/>
								</label>
							</div>

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
												disabled={busy}
												bind:value={reviewNotesByShot[shot.id]}
											/>
											<button
												class="primary"
												disabled={busy}
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
