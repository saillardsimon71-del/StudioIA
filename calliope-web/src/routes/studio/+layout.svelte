<script lang="ts">
	import { onMount } from 'svelte';
	import { studio } from '$lib/studio/api';
	import '$lib/studio/studio.css';
	let { children } = $props();
	let restartRequired = $state(false);
	onMount(() => {
		const check = () => studio.config().then((config) => {
			restartRequired = config.restart_required === true || !config.pipeline_revision;
		}).catch(() => {});
		check();
		const visible = () => { if (!document.hidden) check(); };
		document.addEventListener('visibilitychange', visible);
		return () => document.removeEventListener('visibilitychange', visible);
	});
</script>

<div class="studio">
	{#if restartRequired}
		<p class="notice" role="status">Des corrections sont installées mais le studio utilise encore l'ancienne version.
			Attendez la fin des productions en cours, puis fermez et relancez le studio.</p>
	{/if}
	{@render children()}
</div>
