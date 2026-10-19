<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { errorMessage } from './api';
  import { sourceVideo, type VideoSource } from './source-video-api';

  let { seriesId, disabled = false, onchanged, defaultLanguage = 'en' }: {
    seriesId: number; disabled?: boolean; onchanged: (source: VideoSource | null) => void;
    defaultLanguage?: 'en' | 'fr';
  } = $props();
  let item = $state<VideoSource | null>(null);
  let busy = $state(false);
  let error = $state('');
  let dirty = $state(false);
  let cutDraft = $state('');
  let cutsDirty = $state(false);
  let language = $state<'en' | 'fr'>(untrack(() => defaultLanguage));
  const storageKey = () => `studio-video-source-${seriesId}`;

  onMount(() => {
    try {
      const id = localStorage.getItem(storageKey());
      if (id) refresh(id);
    } catch { /* Stockage facultatif. */ }
    const timer = setInterval(() => {
      if (item?.status === 'analyzing' && !busy) refresh(item.id);
    }, 2000);
    return () => clearInterval(timer);
  });

  function receive(value: VideoSource) {
    item = value;
    dirty = false;
    cutsDirty = false;
    cutDraft = value.analysis.shots?.slice(0, -1).map(s => s.end_sec.toFixed(3)).join(' ; ') ?? '';
    onchanged(value.approved ? value : null);
    try { localStorage.setItem(storageKey(), value.id); } catch { /* Stockage facultatif. */ }
  }
  async function refresh(id: string) {
    try { receive(await sourceVideo.get(seriesId, id)); }
    catch (err) { error = errorMessage(err); }
  }
  async function upload(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file || busy || disabled) return;
    error = ''; busy = true;
    onchanged(null);
    try {
      if (file.size > 250 * 1024 * 1024) throw new Error('Le MP4 dépasse 250 Mo.');
      receive(await sourceVideo.upload(seriesId, file, language));
    } catch (err) { error = errorMessage(err); }
    finally { busy = false; input.value = ''; }
  }
  function changed() {
    dirty = true;
    onchanged(null);
  }
  async function approve() {
    if (!item || busy) return;
    busy = true; error = '';
    try {
      let current = item;
      if (dirty) {
        current = await sourceVideo.review(seriesId, current.id, current.hash, current.analysis.shots);
        item = current; dirty = false;
      }
      receive(await sourceVideo.approve(seriesId, current.id, current.hash));
    } catch (err) { error = errorMessage(err); }
    finally { busy = false; }
  }
  async function retry() {
    if (!item || busy) return;
    busy = true; error = '';
    try { receive(await sourceVideo.retry(seriesId, item.id)); }
    catch (err) { error = errorMessage(err); }
    finally { busy = false; }
  }
  async function recut() {
    if (!item || busy) return;
    busy = true; error = '';
    try {
      const cuts = cutDraft.split(/[;\s]+/).filter(Boolean).map(s => Number(s.replace(',', '.')));
      if (cuts.some(n => !Number.isFinite(n))) throw new Error('Indiquez des secondes, séparées par un point-virgule.');
      let current = item;
      if (dirty) {
        current = await sourceVideo.review(seriesId, current.id, current.hash, current.analysis.shots);
        item = current; dirty = false;
      }
      receive(await sourceVideo.cuts(seriesId, current.id, current.hash, cuts));
    } catch (err) { error = errorMessage(err); }
    finally { busy = false; }
  }
</script>

<section class="source-import stack" aria-label="Vidéo à refaire">
  <p>Importez votre MP4, vérifiez son découpage et ses paroles, puis adaptez-le dans l'univers de cette série.</p>
  <div class="row">
    <label>Langue de la vidéo
      <select bind:value={language} disabled={busy || disabled}>
        <option value="en">Anglais</option><option value="fr">Français</option>
      </select>
    </label>
    <label>Vidéo MP4
      <input type="file" accept="video/mp4,.mp4" onchange={upload} disabled={busy || disabled} />
    </label>
  </div>
  <p class="hint">Analyse locale sans génération payante. MP4 de 1 à 120 secondes, 250 Mo maximum. Pour un TikTok, importez le fichier MP4 dont vous disposez.</p>
  {#if error}<p class="error" role="alert">{error}</p>{#if item}<button type="button" disabled={busy} onclick={() => item && refresh(item.id)}>Actualiser l'analyse</button>{/if}{/if}
  {#if busy}<p role="status">Traitement en cours...</p>{/if}
  {#if item}
    {#if item.status === 'analyzing'}
      <p role="status">Analyse du découpage et transcription locale en cours. Vous pouvez laisser cette page ouverte.</p>
      <progress aria-label="Analyse en cours"></progress>
    {:else if item.status === 'failed'}
      <p class="error" role="alert">{item.error}</p>
      <button type="button" disabled={busy || disabled} onclick={retry}>Relancer l'analyse locale</button>
    {:else if item.status === 'ready'}
      <p><strong>{item.filename}</strong> : {item.analysis.duration_sec.toFixed(1)} s, {item.analysis.shots.length} plans détectés.</p>
      {#if item.analysis.shots.some(s => s.end_sec - s.start_sec < 1)}<p class="error" role="alert">Cette source contient des plans inférieurs à 1 seconde, non pris en charge par le moteur actuel. Choisissez un autre extrait avant l'adaptation.</p>{/if}
      <video controls preload="metadata" src={item.url} aria-label="Vidéo source"><track kind="captions" /></video>
      <p class="notice">Les coupes sont détectées automatiquement et restent à vérifier, particulièrement les fondus. Les personnages, expressions, gestes et mouvements de caméra ne sont pas analysés visuellement. Ajoutez vos observations sous les images ; DeepSeek proposera la mise en scène manquante.</p>
      <details><summary>Corriger les coupes (facultatif)</summary><div class="stack">
        <label>Instants des coupes, en secondes<input bind:value={cutDraft} oninput={() => { cutsDirty = true; onchanged(null); }} disabled={busy || disabled} placeholder="2.5 ; 6 ; 9.2" /></label>
        <p class="hint">Séparez les instants par un point-virgule. Chaque plan doit durer au moins 1 seconde. Une correction demande de revérifier la répartition des paroles ; les anciennes images sont conservées.</p>
        <button type="button" onclick={recut} disabled={busy || disabled || !cutsDirty}>Appliquer les coupes</button>
      </div></details>
      {#if item.analysis.transcription.status !== 'ok'}
        <p class="notice">Transcription non vérifiée : {item.analysis.transcription.reason} Complétez les paroles ci-dessous, ou confirmez les plans sans parole.</p>
      {/if}
      <div class="source-shots">
        {#each item.analysis.shots as shot (shot.index)}
          <article class="stack">
            <strong>Plan source {shot.index + 1} : {shot.start_sec.toFixed(1)} à {shot.end_sec.toFixed(1)} s</strong>
            {#if shot.frame_url}<img src={shot.frame_url} alt={`Image du plan source ${shot.index + 1}`} loading="lazy" />{/if}
            <label>Paroles originales (vide si aucune)<textarea rows="3" bind:value={shot.transcript} oninput={changed} disabled={busy || disabled}></textarea></label>
            <label>Actions et mise en scène observées (facultatif)<textarea rows="3" bind:value={shot.notes} oninput={changed} disabled={busy || disabled} placeholder="Qui agit, quel objet change de main, quel mouvement est visible..."></textarea></label>
            <label class="row"><input type="checkbox" bind:checked={shot.speech_reviewed} onchange={changed} disabled={busy || disabled} />J'ai vérifié les paroles ou confirmé leur absence.</label>
          </article>
        {/each}
      </div>
      <button type="button" class="primary" disabled={busy || disabled || cutsDirty || (!dirty && item.approved) || item.analysis.shots.some(s => !s.speech_reviewed || s.end_sec - s.start_sec < 1)} onclick={approve}>
        {item.approved && !dirty && !cutsDirty ? 'Analyse validée' : 'Valider cette analyse'}
      </button>
      <p class="hint">L'adaptation utilisera vos personnages et vos décors. Les images source servent à la revue ; les premières images de production seront créées manuellement dans ChatGPT.</p>
    {/if}
  {/if}
</section>

<style>
  .source-import { padding: 1rem; border: 1px solid var(--border, #d6ded6); border-radius: .6rem; }
  .source-shots { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr)); gap: 1rem; }
  article { padding: .8rem; border: 1px solid var(--border, #d6ded6); border-radius: .5rem; }
  img { width: 100%; max-height: 240px; object-fit: contain; background: #17231f; }
  video { width: 100%; max-height: 360px; background: #17231f; }
  textarea { width: 100%; }
  input[type='checkbox'] { width: auto; }
</style>
