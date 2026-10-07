<script lang="ts">
	import { studio, isActive, statusLabel, errorMessage, type StudioJob } from './api';
	let { jobs, onrefresh }: { jobs: StudioJob[]; onrefresh: () => Promise<void> } = $props();
	let busy = $state<number | null>(null);
	let error = $state('');
	function label(job: StudioJob) {
		const kind = job.kind ?? job.job_type ?? job.type ?? '';
		return kind.includes('export') ? 'Assemblage du film' : kind.includes('image') ? 'Image' : kind.includes('video') ? 'Vidéo' : 'Travail';
	}
	async function action(job: StudioJob, resume: boolean) {
		if (busy !== null) return;
		busy = job.id; error = '';
		try { if (resume) await studio.resume(job.id); else await studio.cancel(job.id); await onrefresh(); }
		catch (err) { error = errorMessage(err); }
		finally { busy = null; }
	}
</script>

{#if jobs.length}<details class="jobs" open={jobs.some((job) => isActive(job.status) || job.status === 'failed')}><summary>Travaux de génération et d'export ({jobs.length})</summary>
	{#if error}<p class="error" role="alert">{error}</p>{/if}
	<div class="job-list">{#each [...jobs].sort((a, b) => b.id - a.id) as job (job.id)}<div class="job"><div class="between"><div class="row"><strong>{label(job)} {job.clip_id ? `- plan #${job.clip_id}` : ''}</strong><span class:pending={isActive(job.status)} class:failed={job.status === 'failed'} class="badge">{statusLabel(job.status)}</span></div><div class="row">{#if isActive(job.status)}<button class="small" disabled={busy !== null} onclick={() => action(job, false)}>{busy === job.id ? 'Annulation...' : 'Annuler'}</button>{:else if ['failed', 'cancelled', 'paused'].includes(job.status)}<button class="small" disabled={busy !== null} onclick={() => action(job, true)}>{busy === job.id ? 'Reprise...' : 'Reprendre'}</button>{/if}</div></div>{#if job.error || job.error_message}<p class="job-error">{job.error || job.error_message}</p>{/if}</div>{/each}</div>
</details>{/if}

<style>
	.jobs { border-top: 1px solid var(--line); padding-top: 18px; }
	summary { font-weight: 650; cursor: pointer; }
	.job-list { margin-top: 12px; display: grid; }
	.job { padding: 12px 0; border-bottom: 1px solid var(--line); font-size: 13px; }
	.job-error { margin-top: 7px; color: #873422; overflow-wrap: anywhere; white-space: pre-wrap; }
</style>
