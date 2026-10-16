<script lang="ts">
  import { untrack } from 'svelte';
  import { studio, errorMessage, newRequestId, selectedTake, type Board, type Take } from './api';
  import { director, type DirectorState, type DirectorPreflight, type ShotContract } from './director-api';
  import VisualReview from './VisualReview.svelte';

  let { board, active, onchanged }: { board: Board; active: boolean; onchanged: () => Promise<void> } = $props();
  let current = $state<DirectorState | null>(null);
  let check = $state<DirectorPreflight | null>(null);
  let error = $state('');
  let notice = $state('');
  let busy = $state(false);
  let revision = $state('');
  let selection = $state<number[]>([]);
  let expert = $state<Record<number, string>>({});
  let checks = $state<Record<number, Record<string, boolean>>>({});
  let loaded = '';
  const qcFields = [
    ['audio_ok', 'Paroles exactes, sans ajout ni doublon'],
    ['speakers_ok', 'Bons locuteurs et voix'],
    ['visual_ok', 'Identités, cadrage, écrans et texte corrects'],
    ['continuity_ok', 'Raccords et états cohérents'],
  ];
  $effect(() => {
    const key = `${board.project.id}:${board.project.revision}:${board.production?.status}:${board.production?.stage}`;
    if (key !== loaded) { loaded = key; untrack(load); }
  });
  async function load() {
    try { current = await director.state(board.project.id); check = null; }
    catch (err) { error = errorMessage(err); }
  }
  async function act(task: () => Promise<unknown>) {
    if (busy) return;
    busy = true; error = ''; notice = '';
    try { await task(); await onchanged(); await load(); }
    catch (err) { error = errorMessage(err); }
    finally { busy = false; }
  }
  async function preflight() {
    error = ''; busy = true;
    try { check = await director.preflight(board.project.id); }
    catch (err) { error = errorMessage(err); }
    finally { busy = false; }
  }
  function choose(id: number, value: boolean) { selection = value ? [...selection, id] : selection.filter((v) => v !== id); }
  async function upload(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    if (!current || !files.length) return;
    await act(() => director.images(board.project.id, current!.storyboard_hash, files));
    input.value = '';
  }
  async function saveExpert(id: number) {
    if (!current) return;
    await act(async () => {
      const value = JSON.parse(expert[id]) as ShotContract;
      await director.edit(board.project.id, current!.storyboard_hash, [{ id, contract: value }]);
      delete expert[id];
    });
  }
  const retakes = new Map<number, string>();
  function audioLabel(take: Take) {
    const qc = take.metadata.dialogue_qc as { status?: string } | undefined;
    return qc?.status === 'ok' ? 'vérifiée' : !qc || qc.status === 'unknown' ? 'non vérifié' : 'à corriger';
  }
  async function retake(id: number) {
    if (!retakes.has(id)) retakes.set(id, newRequestId());
    await act(async () => {
      await studio.retakeFrom(board.project.id, id, retakes.get(id)!);
      retakes.delete(id);
    });
  }
</script>

<section class="director stack" aria-label="Studio Director">
  <div class="between"><div><p class="hint">STUDIO DIRECTOR</p><h1>{board.project.title}</h1></div>
    <button disabled={busy} onclick={load}>Actualiser</button></div>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if notice}<p class="notice" role="status">{notice}</p>{/if}
  {#if board.production && !(busy && current && !current.enabled)}<p role="status">{board.production.message}</p>
    {#if active}<progress max="1" value={board.production.progress}></progress>{/if}
    {#each board.jobs.filter(job => ['pending', 'running'].includes(job.status)) as job}
      <button disabled={busy} onclick={() => act(() => studio.cancel(job.id))}>Annuler la prise en cours (travail {job.id})</button>
    {/each}
    {#if board.production.error}<p class="error" role="alert">{board.production.error}</p>{/if}
  {/if}
  {#if !current}<p>Ouverture du storyboard...</p>
  {:else if !current.enabled}
    <p>DeepSeek prépare le storyboard et les réglages de réalisation à partir de votre série.</p>
    <button class="primary" disabled={busy || active || !board.scenes.length}
      onclick={() => act(() => director.prepare(board.project.id))}>{busy ? 'Préparation des plans en cours...' : 'Préparer les contrats du Director'}</button>
    {#if busy}<progress aria-label="Préparation du storyboard"></progress><p role="status">Les plans sont préparés un par un. Les contrats déjà prêts sont conservés en cas d'interruption. Aucune vidéo ne démarre à cette étape.</p>{/if}
    {#if !board.scenes.length}<p class="hint">Le scénario est en cours de préparation. Les contrats apparaîtront ensuite.</p>{/if}
  {:else}
    <ol class="steps"><li class:done={current.approved}>Relire le storyboard</li><li class:done={current.shots.filter(s => !s.continue_from_previous).every(s => s.image?.approved)}>Importer les images ChatGPT</li><li>Produire et vérifier les prises</li></ol>
    <h2>1. Votre storyboard</h2>
    <p>Relisez les actions et les paroles. Sélectionnez les plans à modifier et décrivez votre changement. Les autres plans et leurs médias sont conservés.</p>
    <div class="story-grid">
      {#each current.shots as shot, index (shot.id)}
        <article class="story-card stack" data-shot-id={shot.id}>
          <label class="row"><input type="checkbox" checked={selection.includes(shot.id)} disabled={busy || active}
            onchange={event => choose(shot.id, event.currentTarget.checked)} /><strong>Plan {index + 1} - {shot.contract.duration_sec} s</strong>{#if shot.purpose === 'midroll' || shot.purpose === 'outro'}<span>CTA</span>{/if}</label>
          <p>{shot.description}</p>
          <p class="hint">Lieu : {shot.contract.location}. Personnages : {shot.contract.casting.join(', ') || 'aucun'}.</p>
          {#each shot.contract.dialogue as line}<p><strong>{line.speaker} :</strong> {line.text}</p>{/each}
          {#if !shot.contract.dialogue.length}<p class="hint">Sans dialogue.</p>{/if}
          {#if shot.continue_from_previous}
            {@const live = board.scenes.flatMap(s => s.shots).find(s => s.id === shot.id)}
            <p class="hint">Ce plan poursuit la dernière image conservée du plan précédent. L'image du paquet est une référence facultative.</p>
            {#if live?.continuation_source}
              <VisualReview take={live.continuation_source} labels={live.screen_text} disabled={busy || active}
                initialFrame={live.continuation_frame_index ?? 0}
                onreview={frame => act(() => studio.reviewVisuals(live.continuation_source!.id, true, frame, shot.id))} />
            {/if}
          {/if}
          {#if shot.image}<img src={shot.image.url} alt={`Première image du plan ${index + 1}`} loading="lazy" />
            <p>{shot.image.approved ? 'Image validée' : 'Image à vérifier'}</p>
            <button disabled={busy || active || !current.approved}
              onclick={() => act(() => director.reviewImage(board.project.id, shot.image!.take_id, current!.storyboard_hash, !shot.image!.approved))}>
              {shot.image.approved ? 'Retirer la validation de cette image' : 'Valider cette première image'}</button>
          {/if}
          <p class="filename">{shot.filename}</p>
          <details><summary>Réglages experts du plan</summary><div class="stack">
            <p>{shot.contract.action}</p>
            <p>{shot.contract.framing}. {shot.contract.camera}.</p>
            <label>Contrat complet (JSON)<textarea rows="12" value={expert[shot.id] ?? JSON.stringify(shot.contract, null, 2)}
              oninput={event => expert[shot.id] = event.currentTarget.value}></textarea></label>
            <button disabled={busy || active || !expert[shot.id]} onclick={() => saveExpert(shot.id)}>Enregistrer ce plan</button>
          </div></details>
        </article>
      {/each}
    </div>
    <label>Demander une révision<textarea rows="3" bind:value={revision} placeholder="Par exemple : dans les plans sélectionnés, rapproche la caméra et raccourcis la réponse."></textarea></label>
    <div class="row"><button disabled={busy || active || !selection.length || !revision.trim()}
      onclick={() => act(async () => { await director.revise(board.project.id, current!.storyboard_hash, selection, revision); revision = ''; selection = []; })}>Réviser les {selection.length} plans sélectionnés</button>
      <button class="primary" disabled={busy || active || current.approved}
        onclick={() => act(() => director.approve(board.project.id, current!.storyboard_hash))}>{current.approved ? 'Storyboard validé' : 'Valider le storyboard'}</button></div>
    <h2>2. Images dans ChatGPT, puis import</h2>
    <p>Téléchargez le paquet avec prompts, références disponibles et noms des images. Générez les images vous-même dans cette conversation ChatGPT, puis importez-les en lot avec ces noms. Vérifiez chaque image avant de la valider.</p>
    <div class="row">{#if current.approved}<a class="package-link" href={`/api/studio/projects/${board.project.id}/director/package`} download>Télécharger le paquet pour ChatGPT</a>{:else}<p class="hint">La validation du storyboard ouvre le paquet.</p>{/if}
      <label class="upload">Importer le lot d'images<input type="file" multiple accept="image/png,image/jpeg,image/webp" disabled={busy || active || !current.approved} onchange={upload} /></label></div>
    <h2>3. Préflight, production et contrôle des prises</h2>
    <p>Le préflight vérifie localement les contrats, les images et le budget. Chaque nouvelle prise attend votre revue avant le rendu suivant.</p>
    <details><summary>Panneau expert : budget de production</summary><div class="stack budget">
      <p class="hint">Tarif estimatif à renseigner. Ces plafonds portent sur les demandes réservées ; ils ne garantissent pas la facture du fournisseur.</p>
      <label>Estimation en euros par seconde vidéo<input type="number" min="0.000001" step="0.001" bind:value={current.budget.estimated_eur_per_video_second} /></label>
      <label>Plafond estimatif en euros<input type="number" min="0.01" step="0.01" bind:value={current.budget.max_cost_eur} /></label>
      <label>Maximum de nouvelles prises<input type="number" min="1" max="500" bind:value={current.budget.max_new_takes} /></label>
      <label>Maximum de secondes vidéo demandées<input type="number" min="1" max="7200" bind:value={current.budget.max_video_seconds} /></label>
      <button disabled={busy || active} onclick={() => act(() => director.budget(board.project.id, current!.budget))}>Enregistrer le budget</button>
    </div></details>
    <div class="row"><button disabled={busy || active} onclick={preflight}>Vérifier avant production</button>
      <button class="primary" disabled={busy || active || !check?.ok || check.storyboard_hash !== current.storyboard_hash}
        onclick={() => act(() => studio.resumeProduction(board.project.id))}>Produire la prochaine prise / continuer le montage</button>
      {#if active}<button disabled={busy} onclick={() => act(() => studio.pauseProduction(board.project.id))}>Mettre en pause</button>{/if}</div>
    {#if check}<div class:notice={check.ok} class:errors={!check.ok} role="status">
      <p>{check.ok ? 'Préflight valide' : 'Production bloquée'}. {check.cost.new_takes} nouvelles prises, {check.cost.video_seconds} s. Coût estimé : {check.cost.estimated_eur == null ? 'non renseigné' : check.cost.estimated_eur.toFixed(3) + ' EUR'}. Déjà réservé : {check.cost.reserved_eur.toFixed(3)} EUR.</p>
      {#each check.errors as message}<p>{message}</p>{/each}{#each check.warnings as message}<p class="hint">{message}</p>{/each}
    </div>{/if}
    {#each board.scenes.flatMap(s => s.shots) as shot (shot.id)}
      {@const take = selectedTake(shot, 'video')}
      {#if take?.url}
        <article class="take-review stack" data-take-id={take.id}><h3>QC du plan {current.shots.findIndex(s => s.id === shot.id) + 1}</h3>
          <video controls preload="metadata" src={take.url}><track kind="captions" /></video>
          {#if shot.trim_start_sec != null || shot.trim_end_sec != null}<p class="hint">Segment conservé au montage : {shot.trim_start_sec ?? 0} s à {shot.trim_end_sec ?? 'la fin'} s. Vérifiez ce segment à l'écoute.</p>{/if}
          <p>Parole automatique : {audioLabel(take)}.</p>
          <p class="hint">Identités, locuteurs, cadrage, texte et raccords : à vérifier à l'image et à l'écoute.</p>
          {#each qcFields as [key, label]}<label class="row"><input type="checkbox" checked={checks[take.id]?.[key] ?? false}
            disabled={active || busy || shot.approved} onchange={event => checks[take.id] = { ...checks[take.id], [key]: event.currentTarget.checked }} />{label}</label>{/each}
          <div class="row"><button disabled={busy || active} onclick={() => act(async () => { const result = await director.verify(board.project.id, take.id); notice = result.audio_label + ' : ' + result.dialogue.reason; })}>Vérifier cette prise</button>
            <button class="primary" disabled={busy || active || shot.approved || !qcFields.every(([key]) => checks[take.id]?.[key])}
              onclick={() => act(() => director.review(board.project.id, take.id, current!.storyboard_hash, true, checks[take.id]))}>{shot.approved ? 'Prise validée' : 'Valider cette prise'}</button>
            <button disabled={busy || active} onclick={() => act(() => director.review(board.project.id, take.id, current!.storyboard_hash, false, {}))}>Rejeter / retirer la validation</button>
            <button disabled={busy || active} onclick={() => retake(shot.id)}>Refaire ce plan et ses seuls raccords dépendants</button></div>
        </article>
      {/if}
    {/each}
    {#if board.exports.some(item => item.url)}<section class="stack"><h2>Votre épisode</h2>
      {#each board.exports.filter(item => item.url).slice(0, 1) as item}
        <video controls preload="metadata" src={item.url ?? undefined}><track kind="captions" /></video>
        <a href={item.url ?? undefined} download>Télécharger le MP4{item.stale ? ' (version antérieure)' : ''}</a>
      {/each}
    </section>{/if}
  {/if}
</section>

<style>
  .director { max-width: 1280px; margin: 24px auto; padding: 24px; background: #fffdf7; border: 1px solid #d8e0d3; border-radius: 12px; }
  .steps { display: flex; flex-wrap: wrap; gap: 32px; padding: 16px 32px; background: #edf2e8; border-radius: 8px; }
  .done { color: #285334; font-weight: 700; }
  .story-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 310px), 1fr)); gap: 16px; }
  .story-card, .take-review { padding: 16px; border: 1px solid #d8e0d3; border-radius: 8px; min-width: 0; align-content: start; }
  .story-card img { width: 100%; max-height: 320px; object-fit: contain; background: #edf2e8; }
  .filename { overflow-wrap: anywhere; font: 12px monospace; color: #46583e; }
  .budget { max-width: 620px; padding: 16px 0; }
  .package-link { padding: 10px 14px; background: #31543a; color: white; border-radius: 6px; }
  .upload { max-width: 440px; }
  .take-review video { width: 100%; max-height: 480px; background: #18251c; }
  .errors { padding: 12px; background: #fff0eb; color: #713320; }
  details > summary { cursor: pointer; padding: 10px 0; }
  @media(max-width: 700px) { .director { padding: 16px; margin: 12px; } }
</style>
