<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { goto } from "$app/navigation";
  import ConfigDialog from "$lib/studio/ConfigDialog.svelte";
  import EntityIdentityEditor from "$lib/studio/EntityIdentityEditor.svelte";
  import SeriesActions from "$lib/studio/SeriesActions.svelte";
  import {
    studio,
    newRequestId,
    errorMessage,
    statusLabel,
    type Series,
    type DirectingProfile,
  } from "$lib/studio/api";
  let { data }: { data: { id: number } } = $props();
  let item = $state<Series | null>(null);
  let draft = $state<Record<string, string>>({});
  let profile = $state<DirectingProfile | null>(null);
  let idea = $state("");
  let objective = $state("");
  let prepareOnly = $state(true);
  let error = $state("");
  let notice = $state("");
  let busy = $state("");
  let configOpen = $state(false);
  let episodeOpen = $state(false);
  let requestId = "";
  let requestContent = "";
  let loadedId = 0;
  const fields = [
    ["universe", "Thème et univers"],
    ["tone", "Ton"],
    ["visual_style", "Style visuel"],
    ["characters", "Personnages et voix"],
    ["relationships", "Relations"],
    ["locations", "Lieux"],
    ["world_rules", "Règles du monde"],
    ["past_events", "Événements antérieurs au premier épisode"],
    ["ongoing_plot", "Intrigue de départ"],
    ["continuity", "Continuité visuelle et narrative"],
  ];
  let previousReady = $derived(
    !item?.episodes.length ||
      (!!item.episodes.at(-1)?.url && item.episodes.at(-1)?.canon_revision != null &&
        !item.episodes.at(-1)?.continuity_stale),
  );
  let active = $derived(
    item?.episodes.some((e) => ["pending", "running"].includes(e.status)) ??
      false,
  );
  $effect(() => {
    const id = data.id;
    if (loadedId !== id) {
      loadedId = id;
      untrack(() => {
        item = null;
        load(true);
      });
    }
  });
  onMount(() => {
    const timer = setInterval(() => {
      if (active && !document.hidden) load(false);
    }, 2500);
    return () => clearInterval(timer);
  });
  async function load(reset = false) {
    try {
      const value = await studio.getSeries(data.id);
      item = value;
      if (reset) {
        draft = { ...value.bible };
        profile = { ...value.directing_profile };
      }
    } catch (err) {
      error = errorMessage(err);
    }
  }
  async function create(event: SubmitEvent) {
    event.preventDefault();
    if (busy) return;
    busy = "episode";
    error = "";
    const content = JSON.stringify([idea.trim(), objective.trim(), prepareOnly]);
    if (content !== requestContent) {
      requestContent = content;
      requestId = newRequestId();
    }
    try {
      const result = await studio.createEpisode(data.id, {
        idea: idea.trim(),
        objective: objective.trim(),
        request_id: requestId,
        prepare_only: prepareOnly,
      });
      await goto("/studio/" + result.project_id);
    } catch (err) {
      error = errorMessage(err);
      await load(false);
    } finally {
      busy = "";
    }
  }
  async function saveBible(event: SubmitEvent) {
    event.preventDefault();
    if (!item || busy) return;
    busy = "bible";
    error = "";
    try {
      await studio.saveSeries(item.id, {
        bible: draft,
        concept: item.concept,
        title: item.title,
        language: item.language,
        aspect_ratio: item.aspect_ratio,
        target_seconds: item.target_seconds,
        ...(profile ? { directing_profile: profile } : {}),
      });
      await load(true);
      notice = "Contexte enregistré pour les prochains épisodes.";
    } catch (err) {
      error = errorMessage(err);
    } finally {
      busy = "";
    }
  }
  function chooseProfile(event: Event) {
    const preset = (event.currentTarget as HTMLSelectElement).value;
    const social = preset === "fast_dialogue_social_fiction";
    profile = {
      preset: social ? "fast_dialogue_social_fiction" : "generalist",
      framing: social ? "close_preferred" : "story_driven",
      camera: social ? "subtle" : "scene_driven",
      dialogue_density: social ? "brisk" : "natural",
      reactions: social ? "immediate" : "story_driven",
      active_listening: true,
      continuity: true,
      silences: social ? "brief" : "story_driven",
      music: "none",
      music_style: "neutral",
      music_gain_db: -12,
      ambience_gain_db: -34,
      number_format: "grouped_comma",
      attached_values: profile?.attached_values ?? false,
      require_reference_approval: profile?.require_reference_approval ?? false,
      production_mode: profile?.production_mode ?? "automatic",
      transitions: profile?.transitions ?? "scene_fades",
      cta_placement: profile?.cta_placement ?? "middle",
    };
  }
  async function reference(event: Event, entityId: number) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file || !item) return;
    busy = "reference";
    error = "";
    try {
      item = await studio.uploadSeriesReference(item.id, entityId, file);
      notice = "Référence enregistrée pour les prochaines images.";
    } catch (err) {
      error = errorMessage(err);
    } finally {
      busy = "";
      input.value = "";
    }
  }
  async function seriesChanged(deleted: boolean) {
    if (deleted) await goto("/studio");
    else await load(false);
  }
</script>

<svelte:head><title>{item?.title ?? "Série"} - Studio IA</title></svelte:head>
<header class="page-header between">
  <div class="row">
    <a class="brand" href="/studio">Studio IA</a><a href="/studio"
      >Toutes les séries</a
    >
  </div>
  <button onclick={() => (configOpen = true)}>Services</button>
</header>
<main class="series-page stack">
  {#if error}<p class="error" role="alert">{error}</p>{/if}{#if notice}<p
      class="notice"
      role="status"
    >
      {notice}
    </p>{/if}
  {#if !item}<p>Ouverture de la série...</p>{:else}
    <div class="between series-heading">
      <div class="stack">
        <span class="hint">SÉRIE - {item.aspect_ratio} - {item.language}</span>
        <h1>{item.title}</h1>
        <p class="muted">{item.concept}</p>
      </div>
      <div class="stack">
      <SeriesActions {item} disabled={!!busy} onchanged={seriesChanged} />
      <button
        class="primary"
        disabled={!!busy || !previousReady || active || item.archived}
        onclick={() => (episodeOpen = true)}>Créer un nouvel épisode</button
      >
      </div>
    </div>
    {#if item.archived}<p class="notice">Cette série est archivée. Restaurez-la pour produire de nouveaux épisodes.</p>{/if}
    {#if !previousReady}<p class="notice">
        Terminez ou réexportez le dernier épisode pour poursuivre à partir de
        son histoire définitive.
      </p>{/if}
    {#if episodeOpen && !item.archived}<form class="episode-form stack" onsubmit={create}>
        <h2>Épisode {item.episodes.length + 1}</h2>
        <label
          >Idée directrice<textarea
            required
            rows="3"
            maxlength="20000"
            bind:value={idea}
            placeholder="Quel événement doit se produire dans cet épisode ?"
          ></textarea></label
        ><label
          >Thème ou objectif narratif (facultatif)<input
            bind:value={objective}
            maxlength="10000"
            placeholder="Une révélation, une tension, une émotion..."
          /></label
        >
        <label class="row"><input type="checkbox" bind:checked={prepareOnly} />Préparer et relire le scénario avant les rendus</label>
        <p class="hint">{prepareOnly
          ? "Le scénario et sa revue apparaissent sans générer de médias. Vous pourrez les corriger, puis lancer les rendus."
          : "Le studio prépare le scénario et les médias, puis exporte le MP4. Vous pourrez mettre la production en pause."}
          {item.directing_profile.cta_placement === "end"
            ? "L'incitation est ajoutée à la fin, après la chute de l'histoire."
            : "Chaque épisode inclut une courte incitation au milieu. La fin reste consacrée à l'histoire et à la suite."}
        </p>
        <div class="row">
          <button class="primary" disabled={!!busy || !idea.trim()}
            >{busy === "episode"
              ? "Lancement..."
              : prepareOnly ? "Préparer le scénario" : "Produire l'épisode"}</button
          ><button
            type="button"
            onclick={() => (episodeOpen = false)}
            disabled={!!busy}>Fermer</button
          >
        </div>
      </form>{/if}
    <section class="stack">
      <div class="between">
        <h2>Épisodes</h2>
        <button class="small subtle" onclick={() => load(false)}
          >Actualiser</button
        >
      </div>
      {#if !item.episodes.length}<div class="empty">
          <p>Votre série est prête à recevoir sa première idée.</p>
          <button disabled={item.archived} onclick={() => (episodeOpen = true)}
            >Créer un nouvel épisode</button
          >
        </div>{:else}
        <div class="episodes">
          {#each item.episodes as episode (episode.project_id)}<article
              class="episode-card stack"
            >
              <div class="between">
                <span class="badge">Épisode {episode.number}</span><span
                  class:approved={!!episode.url && !episode.continuity_stale}
                  class:failed={episode.status === "failed"}
                  class="badge"
                  >{episode.continuity_stale
                    ? "À réexporter"
                    : statusLabel(episode.status)}</span
                >
              </div>
              <h3>{episode.title}</h3>
              <p class="muted">{episode.idea}</p>
              {#if ["pending", "running"].includes(episode.status)}<progress
                  max="1"
                  value={episode.progress}
                ></progress>
                <p class="hint">{episode.message}</p>{/if}{#if episode.error}<p
                  class="error"
                >
                  {episode.error}
                </p>{/if}{#if episode.summary}<details>
                  <summary>Histoire et continuité</summary>
                  <p>{episode.summary}</p>
                  <ul>
                    {#each episode.events as event}<li>{event}</li>{/each}
                  </ul>
                  <p class="hint">Intrigue en cours : {episode.ongoing_plot}</p>
                </details>{/if}
              <div class="row">
                <a class="button" href={"/studio/" + episode.project_id}
                  >Ouvrir l'épisode</a
                >{#if episode.url}<a
                    class="button primary"
                    href={episode.url}
                    download={episode.title + ".mp4"}>Télécharger le MP4</a
                  >{/if}
              </div>
            </article>{/each}
        </div>
      {/if}
    </section>
    <details class="bible">
      <summary>Contexte persistant de cette série</summary>
      <form class="stack" onsubmit={saveBible}>
        <p class="hint">
          Ces informations s'appliquent aux prochains épisodes. Les histoires
          déjà produites sont conservées ci-dessus.
        </p>
        <label>Titre<input required bind:value={item.title} /></label><label
          >Concept<textarea required bind:value={item.concept} rows="4"
          ></textarea></label
        >
        <div class="columns">
          <label>Langue<input required bind:value={item.language} /></label
          ><label
            >Durée habituelle (s)<input
              type="number"
              required
              min="10"
              max="120"
              bind:value={item.target_seconds}
            /></label
          ><label
            >Format<select bind:value={item.aspect_ratio}
              ><option value="9:16">Vertical</option><option value="16:9"
                >Paysage</option
              ><option value="1:1">Carré</option></select
            ></label
          >
        </div>
        {#if profile}
          <fieldset class="stack">
            <legend>Réalisation</legend>
            <label>Profil<select value={profile.preset} onchange={chooseProfile}>
              <option value="generalist">Généraliste</option>
              <option value="fast_dialogue_social_fiction">Fiction sociale à dialogues rapides</option>
            </select></label>
            <p class="hint">Le profil guide les cadrages, les échanges et les réactions des prochains épisodes. Les plans larges restent possibles quand l'action en a besoin.</p>
            <div class="columns">
              <label>Cadrages<select bind:value={profile.framing}>
                <option value="story_driven">Selon la scène</option>
                <option value="close_preferred">Plutôt proches</option>
              </select></label>
              <label>Caméra<select bind:value={profile.camera}>
                <option value="scene_driven">Selon la scène</option>
                <option value="static">Fixe</option>
                <option value="subtle">Mouvements discrets</option>
              </select></label>
              <label>Dialogue<select bind:value={profile.dialogue_density}>
                <option value="natural">Rythme naturel</option>
                <option value="brisk">Échanges vifs</option>
              </select></label>
              <label>Réactions<select bind:value={profile.reactions}>
                <option value="story_driven">Selon la scène</option>
                <option value="immediate">Immédiates</option>
              </select></label>
              <label>Silences<select bind:value={profile.silences}>
                <option value="story_driven">Selon l'émotion</option>
                <option value="brief">Courts et motivés</option>
              </select></label>
              <label>Volume musical (dB)<input type="number" min="-60" max="0" bind:value={profile.music_gain_db} /></label>
              <label>Format des nombres<select bind:value={profile.number_format}>
                <option value="grouped_comma">12,349</option>
                <option value="grouped_space">12 349</option>
                <option value="plain">12349</option>
              </select></label>
            </div>
            <label><input type="checkbox" bind:checked={profile.active_listening} /> Personnages attentifs et réactifs pendant l'écoute</label>
            <label><input type="checkbox" bind:checked={profile.continuity} /> Préserver les directions des regards, les tenues et la lumière</label>
            <label>Production<select bind:value={profile.production_mode} onchange={() => { if (profile) profile.require_reference_approval = profile.production_mode === 'manual'; }}><option value="automatic">Automatique jusqu'au MP4</option><option value="manual">Validation manuelle des références et des plans</option></select></label>
            <p class="hint">En automatique, les plans indépendants peuvent avancer sans attendre votre approbation. Les échecs audio ont une seule relance ; un défaut persistant bloque l'export.</p>
            <label>Transitions<select bind:value={profile.transitions}><option value="scene_fades">Fondu court aux changements de scène</option><option value="cuts">Coupes franches</option></select></label>
            <label>Incitation spectateur<select bind:value={profile.cta_placement}><option value="end">À la fin, après l'histoire</option><option value="middle">Au milieu</option></select></label>
            <label><input type="checkbox" bind:checked={profile.attached_values} /> Afficher les valeurs exactes définies pour les personnages</label>
          </fieldset>
        {/if}
        {#each fields as [key, label]}<label
            >{label}<textarea bind:value={draft[key]} rows="3"
            ></textarea></label
          >{/each}<button class="primary" disabled={!!busy}
          >{busy === "bible"
            ? "Enregistrement..."
            : "Enregistrer le contexte"}</button
        >
      </form>
    </details>
    {#if item.entities.length}<section class="stack">
        <h2>Références de la série</h2>
        <p class="hint">
          Ces images sont réutilisées pour conserver les identités et les lieux.
          Elles appartiennent uniquement à cette série.
        </p>
        <div class="references">
          {#each item.entities as entity (entity.id)}<article
              class="reference stack"
            >
              {#if entity.url}<img
                  src={entity.url}
                  alt={entity.name}
                  loading="lazy"
                />{/if}
              <h3>{entity.name}</h3>
              <span class="hint"
                >{entity.kind === "character" ? "Personnage" : "Lieu"}</span
              >
              <p class="hint">{entity.description}</p>
              <EntityIdentityEditor seriesId={item.id} {entity} entities={item.entities}
                disabled={!!busy || active} onupdate={(updated) => item = updated} />
              <label
                >Remplacer la référence<input
                  type="file"
                  accept="image/*"
                  disabled={!!busy || active}
                  onchange={(event) => reference(event, entity.id)}
                /></label
              >
            </article>{/each}
        </div>
      </section>{/if}
  {/if}
</main>
{#if configOpen}<ConfigDialog
    onclose={() => (configOpen = false)}
    onsaved={() => load(false)}
  />{/if}

<style>
  .series-page {
    max-width: 1280px;
    margin: auto;
    padding: 36px 32px 70px;
    gap: 30px;
  }
  .series-heading {
    align-items: flex-start;
  }
  .series-heading .stack {
    max-width: 850px;
    gap: 10px;
  }
  .episode-form,
  .episode-card,
  .bible,
  .reference {
    background: var(--white);
    border: 1px solid var(--line);
    border-radius: 5px;
    padding: 24px;
  }
  .episode-form {
    border-left: 4px solid var(--green);
  }
  .episodes {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(290px, 1fr));
    gap: 18px;
  }
  .bible summary {
    font-weight: 650;
    cursor: pointer;
  }
  .bible form {
    padding-top: 24px;
  }
  .references {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
  }
  .reference img {
    width: 100%;
    height: 240px;
    object-fit: contain;
    background: #17231e;
  }
  .reference {
    padding: 18px;
  }
  .episode-card details p {
    margin-top: 12px;
  }
  progress {
    width: 100%;
    accent-color: var(--green);
  }
  @media (max-width: 700px) {
    .series-page {
      padding: 24px 16px;
    }
    .series-heading {
      flex-wrap: wrap;
    }
  }
</style>
