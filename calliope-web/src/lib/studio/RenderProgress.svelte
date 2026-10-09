<script lang="ts">
    import type { RenderState } from './api';
    let { state, callId }: { state?: RenderState; callId?: string | null } = $props();
    const labels: Record<string, string> = { QUEUED: 'En file', SUBMITTED: 'Appel confirmé', WAITING_FOR_GPU: 'Attente GPU / démarrage', CONTAINER_STARTED: 'Worker démarré', LOADING_MODEL: 'Préparation du modèle', GENERATING: 'Génération', ARTIFACT_READY: 'Média distant prêt', DOWNLOADING: 'Récupération', VALIDATING: 'Validation', DONE: 'Terminé', FAILED: 'Échec', CANCELLED: 'Annulé', RESUMING: 'Reprise' };
    let events = $derived([...(state?.events || [])].sort((a, b) => Date.parse(a.at) - Date.parse(b.at)));
    function duration(index: number) {
        const current = events[index];
        if (['DONE', 'FAILED', 'CANCELLED', 'SUBMITTED', 'ARTIFACT_READY'].includes(current.phase)) return '';
        const next = current.phase === 'WAITING_FOR_GPU'
            ? events.find((event) => event.source === 'remote')
            : events.slice(index + 1).find((event) => event.source === current.source);
        const seconds = next ? (Date.parse(next.at) - Date.parse(current.at)) / 1000 : -1;
        return seconds >= 0 ? `${seconds.toFixed(1)} s` : '';
    }
</script>

{#if state?.phase}<p>{labels[state.phase] || state.phase}{#if state.sampling} - échantillonnage {state.sampling.value}/{state.sampling.max}{/if}</p>{/if}
{#if state?.poll_error}<p>Connexion en attente : {state.poll_error}</p>{/if}
{#if state?.progress_limited}<p>Appel historique : progression distante indisponible.</p>{/if}
{#if events.length || callId}<details><summary>Progression et identifiants</summary>
    {#if callId}<p>CallID : {callId}</p>{/if}
    {#if state?.container_id}<p>ContainerID : {state.container_id}</p>{/if}
    {#if state?.identity}<p>Profil : {state.identity.profile || 'inconnu'} - workspace : {state.identity.workspace || 'non résolu'} - environnement : {state.identity.environment || 'défaut du workspace (nom inconnu)'}</p>{/if}
    <ol>{#each events as event, index}<li>{labels[event.phase] || event.phase} - {new Date(event.at).toLocaleString('fr-FR')} {duration(index)}{#if event.detail}<br />{event.detail}{/if}</li>{/each}</ol>
</details>{/if}

<style>
    p, details { font-size: 12px; overflow-wrap: anywhere; }
    ol { padding-left: 20px; }
    li { margin: 6px 0; }
</style>
