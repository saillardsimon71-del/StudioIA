<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { goto } from "$app/navigation";
  import ConfigDialog from "$lib/studio/ConfigDialog.svelte";
  import {
    studio,
    newRequestId,
    errorMessage,
    statusLabel,
    type Series,
  } from "$lib/studio/api";
  let { data }: { data: { id: number } } = $props();
  let item = $state<Series | null>(null);
  let draft = $state<Record<string, string>>({});
  let idea = $state("");
  let objective = $state("");
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
      if (reset) draft = { ...value.bible };
    } catch (err) {
      error = errorMessage(err);
    }
  }
  async function create(event: SubmitEvent) {
    event.preventDefault();
    if (busy) return;
    busy = "episode";
    error = "";
    const content = JSON.stringify([idea.trim(), objective.trim()]);
    if (content !== requestContent) {
      requestContent = content;
      requestId = newRequestId();
    }
    try {
      const result = await studio.createEpisode(data.id, {
        idea: idea.trim(),
        objective: objective.trim(),
        request_id: requestId,
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
      });
      await load(true);
      notice = "Contexte enregistré pour les prochains épisodes.";
    } catch (err) {
      error = errorMessage(err);
    } finally {
      busy = "";
    }
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
      <button
        class="primary"
        disabled={!!busy || !previousReady || active}
        onclick={() => (episodeOpen = true)}>Créer un nouvel épisode</button
      >
    </div>
    {#if !previousReady}<p class="notice">
        Terminez ou réexportez le dernier épisode pour poursuivre à partir de
        son histoire définitive.
      </p>{/if}
    {#if episodeOpen}<form class="episode-form stack" onsubmit={create}>
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
        <p class="hint">
          Le studio écrit le scénario, prépare les images, génère les clips,
          choisit une prise par plan et exporte le MP4 avec son mix audio. Vous
          pourrez mettre la production en pause pour intervenir.
        </p>
        <div class="row">
          <button class="primary" disabled={!!busy || !idea.trim()}
            >{busy === "episode"
              ? "Lancement..."
              : "Produire l'épisode"}</button
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
          <button onclick={() => (episodeOpen = true)}
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
