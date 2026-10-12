<script lang="ts">
  import { onMount, untrack } from "svelte";
  import {
    studio,
    errorMessage,
    type AudioSettings,
    type AudioAsset,
  } from "./api";
  let {
    projectId,
    initial,
    shotNames,
    onclose,
    onrefresh,
  }: {
    projectId: number;
    initial: AudioSettings;
    shotNames: string[];
    onclose: () => void;
    onrefresh: () => Promise<void>;
  } = $props();
  let dialog: HTMLDialogElement;
  let draft = $state<AudioSettings>(
    untrack(() => JSON.parse(JSON.stringify(initial))),
  );
  let assets = $state<AudioAsset[]>(untrack(() => [...(initial.assets ?? [])]));
  let saving = $state(false);
  let error = $state("");
  let preview = $state<{ music_url: string | null; effects_url: string } | null>(null);
  const effects = [
    ["click", "Clic"],
    ["beep", "Signal électronique"],
    ["impact", "Impact"],
    ["boom", "Boom grave"],
    ["whoosh", "Souffle bref"],
    ["riser", "Montée de tension"],
    ["door", "Porte"],
    ["footstep", "Pas"],
    ["pop", "Pop bref"],
    ["boing", "Rebond comique"],
    ["scratch", "Arrêt comique"],
  ];
  onMount(() => dialog.showModal());
  async function persist() {
    const { assets: ignored, ...config } = draft;
    draft = await studio.saveAudio(projectId, config);
  }
  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (saving) return;
    saving = true;
    error = "";
    try {
      await persist();
      await onrefresh();
      onclose();
    } catch (err) {
      error = errorMessage(err);
    } finally {
      saving = false;
    }
  }
  async function audition() {
    if (saving) return;
    saving = true;
    error = "";
    try {
      await persist();
      preview = await studio.previewAudio(projectId);
      await onrefresh();
    } catch (err) {
      error = errorMessage(err);
    } finally {
      saving = false;
    }
  }
  async function upload(event: Event, kind: string) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    saving = true;
    error = "";
    try {
      const asset = await studio.uploadAudio(projectId, kind, file);
      assets = [...assets, asset];
      if (kind === "music") draft.music_asset_id = asset.id;
      else
        draft.sfx = [
          ...draft.sfx,
          {
            effect: "click",
            shot_index: 0,
            at_sec: 0,
            gain_db: -18,
            asset_id: asset.id,
          },
        ];
    } catch (err) {
      error = errorMessage(err);
    } finally {
      saving = false;
      input.value = "";
    }
  }
  function addCue() {
    draft.sfx = [
      ...draft.sfx,
      {
        effect: "click",
        shot_index: 0,
        at_sec: 0.2,
        gain_db: -18,
        asset_id: null,
      },
    ];
  }
</script>

<dialog bind:this={dialog} {onclose} aria-labelledby="audio-title">
  <div class="dialog-header between">
    <h2 id="audio-title">Musique et bruitages</h2>
    <button class="small" onclick={onclose} disabled={saving}>Fermer</button>
  </div>
  <form onsubmit={save}>
    <div class="dialog-body stack">
      {#if error}<p class="error" role="alert">{error}</p>{/if}<label
        class="check"
        ><input type="checkbox" bind:checked={draft.enabled} />Ajouter le mix
        musical et les bruitages au montage</label
      >
      <p class="hint">
        Les voix et ambiances natives de H3 restent dans les clips. Les réglages
        ci-dessous s'appliquent au prochain export, sans nouveau rendu GPU.
      </p>
      <fieldset class="stack">
        <legend>Votre musique</legend>
        <p class="hint">
          Importez votre fichier pour le prochain export. Sans fichier sélectionné,
          aucune musique n'est ajoutée.
        </p>
        <label
          >Musique importée<select bind:value={draft.music_asset_id}
            ><option value={null}>Sans musique</option
            >{#each assets.filter((a) => a.kind === "music") as asset}<option
                value={asset.id}>{asset.name}</option
              >{/each}</select
          ></label
        ><label
          >Importer une musique<input
            type="file"
            accept="audio/*"
            disabled={saving}
            onchange={(event) => upload(event, "music")}
          /></label
        >
        <div class="columns">
          <label
            >Volume musique (dB)<input
              type="number"
              min="-60"
              max="0"
              bind:value={draft.music_gain_db}
            /></label
          ><label
            >Volume voix (dB)<input
              type="number"
              min="-24"
              max="6"
              bind:value={draft.voice_gain_db}
            /></label
          >
        </div>
        <label class="check"
          ><input type="checkbox" bind:checked={draft.ducking} />Baisser
          automatiquement la musique pendant les voix</label
        >
        <p class="hint">Point de départ conseillé : musique à -12 dB, puis
          ajuster à l'écoute. Elle baisse pendant les voix sans disparaître.</p>
      </fieldset>
      <fieldset class="stack">
        <legend>Ambiance supplémentaire</legend><label
          >Fond sonore<select bind:value={draft.ambience}
            ><option value="none">Aucun</option><option value="room"
              >Fond intérieur discret</option
            ><option value="wind">Vent</option><option value="rain"
              >Pluie</option
            ><option value="cafe">Murmure de café</option><option value="night"
              >Nuit</option
            ><option value="city">Ville lointaine</option><option value="forest"
              >Forêt</option
            ></select
          ></label
        ><label
          >Volume ambiance (dB)<input
            type="number"
            min="-60"
            max="0"
            bind:value={draft.ambience_gain_db}
          /></label
        >
      </fieldset>
      <section class="stack">
        <div class="between">
          <h3>Bruitages ponctuels</h3>
          <button type="button" class="small" onclick={addCue}
            >Ajouter un bruitage</button
          >
        </div>
        <p class="hint">Placez les accents sur une chute, une surprise, une
          notification ou un retournement. Un rebond comique ou un arrêt bref
          suffit ; évitez de ponctuer chaque cut.</p>
        <label
          >Importer un bruitage<input
            type="file"
            accept="audio/*"
            disabled={saving}
            onchange={(event) => upload(event, "sfx")}
          /></label
        >{#each draft.sfx as cue, index}<fieldset class="stack">
            <div class="between">
              <span>Bruitage {index + 1}</span><button
                type="button"
                class="small danger"
                onclick={() =>
                  (draft.sfx = draft.sfx.filter((_, i) => i !== index))}
                >Retirer</button
              >
            </div>
            <label
              >Son<select bind:value={cue.asset_id}
                ><option value={null}>Son du studio</option
                >{#each assets.filter((a) => a.kind === "sfx") as asset}<option
                    value={asset.id}>{asset.name}</option
                  >{/each}</select
              ></label
            >{#if !cue.asset_id}<label
                >Effet<select bind:value={cue.effect}
                  >{#each effects as [value, label]}<option {value}
                      >{label}</option
                    >{/each}</select
                ></label
              >{/if}<label
              >Plan<select bind:value={cue.shot_index} onchange={() => cue.shot_id = null}
                >{#each shotNames as name, i}<option value={i}
                    >{i + 1}. {name}</option
                  >{/each}</select
              ></label
            >
            <div class="columns">
              <label
                >Secondes après le début du plan<input
                  type="number"
                  min="0"
                  max="120"
                  step="0.1"
                  bind:value={cue.at_sec}
                /></label
              ><label
                >Volume (dB)<input
                  type="number"
                  min="-60"
                  max="0"
                  bind:value={cue.gain_db}
                /></label
              >
            </div>
          </fieldset>{/each}
      </section>
      <button type="button" disabled={saving} onclick={audition}
        >Enregistrer et écouter les pistes</button
      >{#if preview}<div class="stack">
          {#if preview.music_url}<div class="stack">
            <p>Musique</p>
            <audio controls src={preview.music_url}></audio>
          </div>{/if}
          <div class="stack">
            <p>Ambiance et bruitages du studio</p>
            <audio controls src={preview.effects_url}></audio>
          </div>
          <p class="hint">
            Aperçu des sources avant volume et mix avec les voix. Les bruitages
            importés s'écoutent ci-dessous.
          </p>
          {#each assets.filter((a) => a.kind === "sfx") as asset}<div
              class="stack"
            >
              <p>{asset.name}</p>
              <audio controls src={asset.url}></audio>
            </div>{/each}
        </div>{/if}
    </div>
    <div class="dialog-actions row">
      <button type="button" onclick={onclose} disabled={saving}>Fermer</button
      ><button class="primary" disabled={saving}
        >{saving ? "Enregistrement..." : "Enregistrer le son"}</button
      >
    </div>
  </form>
</dialog>

<style>
  audio {
    width: 100%;
    max-width: 100%;
  }
</style>
