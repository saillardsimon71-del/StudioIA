<script lang="ts">
	import { onMount } from 'svelte';
	import { studio, errorMessage, type Scene } from './api';
	let { projectId, scene = null, onclose, onrefresh }: { projectId: number; scene?: Scene | null; onclose: () => void; onrefresh: () => Promise<void> } = $props();
	let dialog: HTMLDialogElement;
	let heading = $state('');
	let action = $state('');
	let dialogText = $state('');
	let busy = $state(false);
	let error = $state('');
	onMount(() => { heading = scene?.heading ?? ''; action = scene?.action ?? ''; dialogText = scene?.dialog ?? ''; dialog.showModal(); });
	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (busy) return;
		busy = true; error = '';
		try { const payload = { heading: heading.trim(), action, dialog: dialogText }; if (scene) await studio.updateScene(projectId, scene.id, payload); else await studio.addScene(projectId, payload); await onrefresh(); onclose(); }
		catch (err) { error = errorMessage(err); }
		finally { busy = false; }
	}
</script>

<dialog bind:this={dialog} onclose={onclose} aria-labelledby="scene-title"><div class="dialog-header between"><h2 id="scene-title">{scene ? 'Modifier la scène' : 'Ajouter une scène'}</h2><button class="small subtle" disabled={busy} onclick={onclose}>Fermer</button></div>
	<form onsubmit={save}><div class="dialog-body stack">{#if error}<p class="error" role="alert">{error}</p>{/if}<label>Titre de la scène<input required bind:value={heading} placeholder="EXT. Jardin - Matin" /></label><label>Action<textarea bind:value={action} placeholder="Ce qui se passe dans cette scène."></textarea></label><label>Dialogue de la scène<textarea bind:value={dialogText} rows="3"></textarea></label>{#if !scene}<p class="hint">La scène sera créée avec un premier plan à éditer.</p>{/if}</div><div class="dialog-actions row"><button type="button" disabled={busy} onclick={onclose}>Annuler</button><button class="primary" disabled={busy || !heading.trim()}>{busy ? 'Enregistrement...' : scene ? 'Enregistrer la scène' : 'Ajouter la scène'}</button></div></form>
</dialog>
