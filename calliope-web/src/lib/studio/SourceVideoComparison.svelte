<script lang="ts">
  import { untrack } from 'svelte';
  import { errorMessage, type Board } from './api';
  import { sourceVideo, type VideoSource } from './source-video-api';

  let { board }: { board: Board } = $props();
  let source = $state<VideoSource | null>(null);
  let error = $state('');
  $effect(() => {
    const id = board.project.id;
    untrack(async () => {
      try { source = (await sourceVideo.project(id)).source; }
      catch (err) { error = errorMessage(err); }
    });
  });
  const shots = $derived(board.scenes.flatMap(scene => scene.shots));
</script>

{#if error}<p class="error" role="alert">{error}</p>{/if}
{#if source}
  <details class="comparison">
    <summary>Vidéo source et comparaison du découpage</summary>
    <div class="stack">
      <video controls preload="none" src={source.url} aria-label="Vidéo originale"><track kind="captions" /></video>
      <p class="hint">Les paroles adaptées et les réglages se trouvent dans le storyboard. Identités, expressions, voix et raccords restent à vérifier sur les prises.</p>
      <div class="source-plans">
        {#each source.analysis.shots as original (original.index)}
          {@const adapted = shots.filter(shot => shot.source_beat_index === original.index)}
          {@const duration = adapted.reduce((sum, shot) => sum + shot.duration_sec, 0)}
          <article>
            {#if original.frame_url}<img src={original.frame_url} alt={`Source, plan ${original.index + 1}`} loading="lazy" />{/if}
            <strong>Source {original.index + 1} : {(original.end_sec - original.start_sec).toFixed(1)} s</strong>
            <p>Storyboard : {adapted.length ? `${duration.toFixed(1)} s` : 'En préparation'}</p>
            <p>{original.transcript || 'Sans parole'}</p>
            {#if original.notes}<p>{original.notes}</p>{/if}
          </article>
        {/each}
      </div>
    </div>
  </details>
{/if}

<style>
  .comparison { margin: 1rem; padding: 1rem; border: 1px solid var(--border, #d6ded6); border-radius: .6rem; }
  .source-plans { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 200px), 1fr)); gap: 1rem; }
  video { width: 100%; max-height: 360px; background: #17231f; }
  img { width: 100%; max-height: 180px; object-fit: contain; }
</style>
