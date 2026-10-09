<script lang="ts">
  import type { Shot, Take } from './api';
  let { take, labels, disabled, onreview, initialFrame = 0 }: {
    take: Take; labels: Shot['screen_text']; disabled: boolean;
    onreview: (frameIndex: number) => void;
    initialFrame?: number;
  } = $props();
  let frame = $state(0);
  let video = $state<HTMLVideoElement>();
  let frameReady = $state(false);
  let dimensions = $state({ takeId: 0, width: 0, height: 0 });
  let fps = $derived(Number(take.metadata.fps) || 24);
  let dimensionsReady = $derived(dimensions.takeId === take.id && dimensions.width > 0 && dimensions.height > 0);
  let ratio = $derived(dimensionsReady ? `${dimensions.width} / ${dimensions.height}` : '16 / 9');
  let reviewed = $derived(!!(take.metadata.visual_review as { approved?: boolean } | undefined)?.approved);
  $effect(() => { frame = initialFrame; });
  function seek() { if (video) { frameReady = false; video.currentTime = frame / fps; } }
  function frameDisplayed() { if (video) { frame = Math.round(video.currentTime * fps); frameReady = true; } }
  function imageLoaded(event: Event) {
    const media = event.currentTarget as HTMLImageElement;
    dimensions = { takeId: take.id, width: media.naturalWidth, height: media.naturalHeight };
  }
  function videoLoaded(event: Event) {
    const media = event.currentTarget as HTMLVideoElement;
    dimensions = { takeId: take.id, width: media.videoWidth, height: media.videoHeight };
    frameReady = false;
    media.currentTime = frame / fps;
  }
</script>

<div class="stack">
  <div class="review-media" style:aspect-ratio={ratio}>
    {#if take.kind === 'video'}
      <!-- svelte-ignore a11y_media_has_caption (La prise brute ne contient pas de sous-titres.) -->
      <video bind:this={video} src={take.url ?? undefined} controls preload="metadata" onloadedmetadata={videoLoaded}
        onseeked={frameDisplayed} onloadeddata={() => { if (frame === 0) frameDisplayed(); }}
        onplay={() => frameReady = false} onpause={frameDisplayed}></video>
    {:else}<img src={take.url ?? ''} alt="Prise réelle à vérifier avant la vidéo" onload={imageLoaded} />{/if}
    {#each labels ?? [] as label}
      {#if label.anchor === 'character' && label.anchor_box}
        <div class="roi" style:left={`${label.anchor_box[0] * 100}%`} style:top={`${label.anchor_box[1] * 100}%`}
          style:width={`${label.anchor_box[2] * 100}%`} style:height={`${label.anchor_box[3] * 100}%`}>
          <span>{label.entity_key}</span>
        </div>
      {/if}
    {/each}
  </div>
  {#if take.kind === 'video'}<label>Frame à vérifier<input type="number" min="0" step="1" bind:value={frame} onchange={seek} /></label>{/if}
  <p class="hint">Vérifiez l'identité de chaque personnage et ses zones encadrées sur cette image réelle. Les valeurs exactes seront ajoutées au montage.</p>
  <button disabled={disabled || !take.url || !dimensionsReady || (take.kind === 'video' && (!frameReady || !video?.paused))} onclick={() => onreview(take.kind === 'image' ? 0 : frame)}>
    {reviewed ? "Renouveler l'approbation visuelle" : "Confirmer l'identité et les zones"}
  </button>
</div>

<style>
  .review-media { position: relative; width: 100%; overflow: hidden; background: #111; }
  .review-media img, .review-media video { width: 100%; height: 100%; object-fit: contain; display: block; }
  .roi { position: absolute; border: 2px solid #ffcc55; pointer-events: none; }
  .roi span { position: absolute; top: 0; left: 0; color: #111; background: #ffcc55; font-size: 12px; padding: 2px 4px; }
</style>
