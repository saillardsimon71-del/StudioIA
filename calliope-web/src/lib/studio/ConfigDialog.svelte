<script lang="ts">
	import { onMount } from 'svelte';
	import { studio, errorMessage, type StudioConfig } from './api';
	let { onclose, onsaved }: { onclose: () => void; onsaved: () => void } = $props();
	let dialog: HTMLDialogElement;
	let config = $state<StudioConfig | null>(null);
	let llmKey = $state('');
	let imageKey = $state('');
	let error = $state('');
	let saving = $state(false);
	let useAgnes = $state(false);

	onMount(() => {
		dialog.showModal();
		studio.config().then((value) => config = { ...value, image_provider: value.image_provider ?? 'api', video_profile: value.video_profile ?? 'fast' }).catch((err) => error = errorMessage(err));
	});

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (!config || saving) return;
		saving = true;
		error = '';
		try {
			const { llm_configured, image_configured, ...payload } = config;
			await studio.saveConfig({ ...payload, use_agnes_for_text: config.image_provider === 'api' && useAgnes, ...(llmKey ? { llm_api_key: llmKey } : {}), ...(config.image_provider === 'api' && imageKey ? { image_api_key: imageKey } : {}) });
			onsaved();
			onclose();
		} catch (err) { error = errorMessage(err); }
		finally { saving = false; }
	}

	function selectVideoModel() {
		if (config && ['studio-ia-h3', 'studio-ia-ltx'].includes(config.modal_app)) {
			config.modal_app = config.video_profile === 'ltx' ? 'studio-ia-ltx' : 'studio-ia-h3';
		}
	}
</script>

<dialog bind:this={dialog} onclose={onclose} aria-labelledby="config-title">
	<div class="between dialog-header"><h2 id="config-title">Services de génération</h2><button class="subtle small" onclick={onclose} aria-label="Fermer la configuration">Fermer</button></div>
	<form onsubmit={save}>
		<div class="dialog-body stack">
			{#if error}<p class="error" role="alert">{error}</p>{/if}
			{#if config}
				<p class="muted">Les paramètres et les clés restent sur cet ordinateur. Les clés enregistrées sont masquées.</p>
				<fieldset class="stack"><legend>Vidéo sur Modal</legend>
					<label>Mode vidéo<select bind:value={config.video_profile} onchange={selectVideoModel} disabled={config.restart_required === true}><option value="ltx">LTX-2.5, 768p</option><option value="quality">Qualité - H3-Base, 768p</option><option value="fast">Rapide - FastH3, 480p</option></select></label>
					<p class="hint">{#if config.restart_required}Relancez le studio pour charger le choix du modèle vidéo.{:else}LTX et H3 utilisent chacun leur propre préparation vidéo.{/if}</p>
					<div class="columns"><label>Application Modal<input required bind:value={config.modal_app} placeholder="studio-ia-h3" /></label><label>Fonction<input required bind:value={config.modal_function} placeholder="render_clip" /></label></div>
					<p class="hint">Le compte Modal connecté sur cet ordinateur doit pouvoir appeler cette fonction.</p>
				</fieldset>
				<fieldset class="stack"><legend>Écriture des séries et épisodes</legend>
					{#if config.image_provider === 'api'}<label class="check"><input type="checkbox" bind:checked={useAgnes} />Utiliser Agnes avec la même clé que les images</label>{/if}
					<label>Adresse de l'API<input type="url" bind:value={config.llm_base_url} placeholder="https://api.openai.com/v1" /></label>
					<div class="columns"><label>Modèle<input bind:value={config.llm_model} placeholder="Nom du modèle" /></label><label>Clé API<input type="password" autocomplete="new-password" bind:value={llmKey} placeholder={config.llm_configured ? 'Clé enregistrée, conserver si vide' : 'Clé si votre service en exige une'} /></label></div>
					<p class="hint">API compatible OpenAI. Un script peut être découpé localement sans ce service.</p>
				</fieldset>
				<fieldset class="stack"><legend>Images de référence</legend>
					<label>Générateur d'images<select bind:value={config.image_provider}><option value="codex">Codex - abonnement ChatGPT</option><option value="api">Service API</option></select></label>
					{#if config.image_provider === 'codex'}
						<p class="hint">Codex doit être installé et connecté via ChatGPT sur cet ordinateur. Aucune clé API nécessaire. La génération utilise les limites de votre abonnement Codex.</p>
					{:else}
					<label>Adresse de l'API<input type="url" bind:value={config.image_base_url} placeholder="https://api.openai.com/v1" /></label>
					<div class="columns"><label>Modèle d'image<input bind:value={config.image_model} placeholder="Nom du modèle" /></label><label>Clé API<input type="password" autocomplete="new-password" bind:value={imageKey} placeholder={config.image_configured ? 'Clé enregistrée, conserver si vide' : 'Clé si votre service en exige une'} /></label></div>
					{/if}
					<p class="hint">Vous pouvez aussi importer vos propres images dans chaque plan.</p>
				</fieldset>
				<details><summary>Mode de diagnostic</summary><label class="check mode"><input type="checkbox" bind:checked={config.dry_run} />Activer la simulation sans génération de médias</label></details>
			{:else if !error}<p class="muted">Chargement des paramètres...</p>{/if}
		</div>
		<div class="row dialog-actions"><button type="button" onclick={onclose}>Annuler</button><button class="primary" disabled={!config || saving}>{saving ? 'Enregistrement...' : 'Enregistrer'}</button></div>
	</form>
</dialog>

<style>.mode { margin-top: 12px; }</style>
