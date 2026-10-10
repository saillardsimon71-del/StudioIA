<script lang="ts">
  import { onMount } from "svelte";
  import { goto } from "$app/navigation";
  import ConfigDialog from "$lib/studio/ConfigDialog.svelte";
  import SeriesActions from "$lib/studio/SeriesActions.svelte";
  import {
    studio,
    errorMessage,
    type Series,
    type AspectRatio,
  } from "$lib/studio/api";
  let series = $state<Series[]>([]);
  let title = $state("");
  let concept = $state("");
  let aspect = $state<AspectRatio>("9:16");
  let language = $state("en");
  let seconds = $state(65);
  let error = $state("");
  let loading = $state(true);
  let busy = $state(false);
  let configOpen = $state(false);
  let servicesReady = $state(false);
  let archived = $state(false);
  onMount(load);
  async function load() {
    try {
      const view = archived;
      const result = await studio.series(view);
      if (view === archived) series = result.series;
      const config = await studio.config();
      servicesReady =
        config.llm_configured && config.image_configured && !config.dry_run;
    } catch (err) {
      error = errorMessage(err);
    } finally {
      loading = false;
    }
  }
  async function create(event: SubmitEvent) {
    event.preventDefault();
    if (busy) return;
    busy = true;
    error = "";
    try {
      const item = await studio.createSeries({
        title: title.trim(),
        concept: concept.trim(),
        aspect_ratio: aspect,
        language,
        target_seconds: seconds,
      });
      await goto("/studio/series/" + item.id);
    } catch (err) {
      error = errorMessage(err);
    } finally {
      busy = false;
    }
  }
  async function showArchives(value: boolean) {
    archived = value;
    loading = true;
    error = "";
    await load();
  }
</script>

<svelte:head><title>Studio IA - Séries</title></svelte:head>
<header class="page-header between">
  <a class="brand" href="/studio">Studio IA</a>
  <div class="row">
    <a href="/studio/projects">Projets indépendants</a><button
      onclick={() => (configOpen = true)}>Services</button
    >
  </div>
</header>
<main class="series-home">
  <div class="stack intro">
    <p class="eyebrow">SÉRIES</p>
    <h1>Une idée. Le prochain épisode.</h1>
    <p class="muted">
      Chaque série conserve son univers, ses personnages et son histoire. Donnez
      une idée directrice : le studio prépare et produit l'épisode.
    </p>
  </div>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if !servicesReady && !loading}<div class="notice between">
      <p>
        Configurez l'écriture et les images dans Services pour la production
        automatique.
      </p>
      <button onclick={() => (configOpen = true)}
        >Configurer les services</button
      >
    </div>{/if}
  <div class="series-grid">
    <section class="stack" aria-labelledby="series-list-title">
      <div class="between">
        <h2 id="series-list-title">{archived ? "Séries archivées" : "Vos séries"}</h2>
        <button class="small subtle" onclick={load}>Actualiser</button>
      </div>
      <div class="row">
        <button disabled={!archived || loading} onclick={() => showArchives(false)}>Actives</button>
        <button disabled={archived || loading} onclick={() => showArchives(true)}>Archives</button>
      </div>
      {#if loading}<p>Chargement...</p>{:else if !series.length}<div
          class="empty"
        >
          <h3>{archived ? "Aucune série archivée." : "Créez votre première série."}</h3>
          <p class="muted">{#if archived}Les séries archivées conservent leurs épisodes et peuvent être restaurées.{:else}
            Fiction, humour, horreur, réalisme, publicité narrative ou animation
            : votre concept donne la direction.
            {/if}
          </p>
        </div>{:else}
        <div class="series-list">
          {#each series as item (item.id)}
            <article class="series-card"><a class="series-link" href={"/studio/series/" + item.id}
              ><div class="between">
                <h3>{item.title}</h3>
                <span class="badge">{item.aspect_ratio}</span>
              </div>
              <p class="muted">{item.concept}</p>
              <div class="between">
                <span class="hint"
                  >{item.episodes.length} épisode{item.episodes.length > 1
                    ? "s"
                    : ""} - {item.entities.filter((e) => e.kind === "character")
                    .length} personnages</span
                ><span>Ouvrir la série</span>
              </div></a>
              <SeriesActions {item} disabled={busy} onchanged={() => load()} />
            </article>
          {/each}
        </div>
      {/if}
    </section>
    <aside class="new-series">
      <form onsubmit={create} class="stack">
        <h2>Créer une nouvelle série</h2>
        <label
          >Titre<input
            required
            maxlength="200"
            bind:value={title}
            placeholder="Le nom de votre série"
          /></label
        ><label
          >Concept de la série<textarea
            required
            rows="6"
            maxlength="30000"
            bind:value={concept}
            placeholder="Décrivez l'univers, le ton, les personnages et le style souhaité. L'IA développera les détails manquants."
          ></textarea></label
        >
        <div class="columns">
          <label
            >Format<select bind:value={aspect}
              ><option value="9:16">Vertical</option><option value="16:9"
                >Paysage</option
              ><option value="1:1">Carré</option></select
            ></label
          ><label
            >Langue des dialogues<select bind:value={language}
              ><option value="en">English</option><option value="fr"
                >Français</option
              ><option value="es">Español</option><option value="de"
                >Deutsch</option
              ><option value="it">Italiano</option><option value="ja"
                >日本語</option
              ></select
            ></label
          >
        </div>
        <label
          >Durée habituelle d'un épisode (secondes)<input
            type="number"
            required
            min="10"
            max="120"
            step="5"
            bind:value={seconds}
          /></label
        >
        <p class="hint">
          Format visé pour chaque épisode : le scénario remplit réellement cette
          durée, chaque plan ne dure que le temps utile, et le montage coupe le
          surplus des prises.
        </p>
        <button
          class="primary"
          disabled={busy || !title.trim() || !concept.trim()}
          >{busy ? "Création..." : "Créer la série"}</button
        >
        <p class="hint">
          Son contexte et ses références restent séparés de toutes les autres
          séries.
        </p>
      </form>
    </aside>
  </div>
</main>
{#if configOpen}<ConfigDialog
    onclose={() => (configOpen = false)}
    onsaved={load}
  />{/if}

<style>
  .series-home {
    max-width: 1380px;
    margin: auto;
    padding: 40px 32px 70px;
    display: grid;
    gap: 30px;
  }
  .intro {
    max-width: 850px;
    gap: 10px;
  }
  .eyebrow {
    font-size: 12px;
    letter-spacing: 0.12em;
    color: var(--green);
    font-weight: 700;
  }
  .series-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 380px;
    align-items: start;
    gap: 36px;
  }
  .new-series {
    background: var(--white);
    border: 1px solid var(--line);
    border-top: 4px solid var(--green);
    border-radius: 5px;
    padding: 24px;
  }
  .series-list {
    display: grid;
    gap: 14px;
  }
  .series-card {
    color: var(--ink);
    text-decoration: none;
    background: var(--white);
    border: 1px solid var(--line);
    border-radius: 5px;
    padding: 24px;
    display: grid;
    gap: 16px;
  }
  .series-card:hover {
    border-color: var(--green);
  }
  .series-card p {
    white-space: pre-line;
  }
  .series-link {
    color: inherit;
    text-decoration: none;
    display: grid;
    gap: 16px;
  }
  @media (max-width: 900px) {
    .series-grid {
      grid-template-columns: 1fr;
    }
    .series-home {
      padding: 24px 16px;
    }
  }
</style>
