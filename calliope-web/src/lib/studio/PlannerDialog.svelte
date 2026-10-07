<script lang="ts">
	import { onMount } from 'svelte';
	import { studio, errorMessage, type StudioProject } from './api';
	let { project, onclose, onrefresh }: { project: StudioProject; onclose: () => void; onrefresh: () => Promise<void> } = $props();
	let dialog: HTMLDialogElement;
	let text = $state('');
	let mode = $state<'script' | 'idea'>('script');
	let useLlm = $state(false);
	let busy = $state(false);
	let error = $state('');
	onMount(() => {
		text = project.script_source || project.idea || '';
		mode = project.idea && !project.script_source ? 'idea' : 'script';
		dialog.showModal();
	});
	async function prepare(event: SubmitEvent) {
		event.preventDefault();
		if (busy || !text.trim()) return;
		busy = true; error = '';
		try { await studio.plan(project.id, { text, mode, use_llm: mode === 'idea' || useLlm }); await onrefresh(); onclose(); }
		catch (err) { error = errorMessage(err); }
		finally { busy = false; }
	}
</script>

<dialog bind:this={dialog} onclose={onclose} aria-labelledby="planner-title"><div class="dialog-header between"><h2 id="planner-title">Préparer les scènes et les plans</h2><button class="small subtle" onclick={onclose} disabled={busy}>Fermer</button></div>
	<form onsubmit={prepare}><div class="dialog-body stack">{#if error}<p class="error" role="alert">{error}</p>{/if}
		<fieldset><legend>Point de départ</legend><div class="row"><label class="check"><input type="radio" value="script" bind:group={mode} />J'ai un script</label><label class="check"><input type="radio" value="idea" bind:group={mode} />J'ai une idée</label></div></fieldset>
		<label>{mode === 'script' ? 'Votre script' : 'Votre idée'}<textarea required bind:value={text} rows="10" placeholder={mode === 'script' ? 'Collez le script. Séparez les scènes par une ligne vide ou un titre INT./EXT.' : 'Décrivez le sujet, les personnages, la durée et le style souhaités.'}></textarea></label>
		{#if mode === 'script'}<label class="check"><input type="checkbox" bind:checked={useLlm} />Utiliser l'IA pour proposer le découpage</label><p class="hint">Sans cette option, le découpage fonctionne localement, sans clé API. Vous ajusterez ensuite les plans.</p>{:else}<p class="hint">L'IA développe votre idée en scènes et en plans. Configurez le service d'écriture dans les services de génération.</p>{/if}
		<p class="hint">Cette étape prépare le montage. Aucune image ni vidéo n'est générée.</p>
	</div><div class="dialog-actions row"><button type="button" onclick={onclose} disabled={busy}>Annuler</button><button class="primary" disabled={busy || !text.trim()}>{busy ? 'Préparation...' : 'Préparer le découpage'}</button></div></form>
</dialog>
