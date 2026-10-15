<script lang="ts">
  import { studio, errorMessage, type Series, type SeriesEntity } from "./api";

  let { seriesId, entity, disabled, onupdate }: {
    seriesId: number;
    entity: SeriesEntity;
    disabled: boolean;
    onupdate: (series: Series) => void;
  } = $props();

  let picker = $state<HTMLInputElement | null>(null);
  let editing = $state(false);
  let draft = $state("");
  let busy = $state(false);
  let error = $state("");
  let notice = $state("");

  $effect(() => {
    draft = entity.identity.visual_description || entity.description || "";
  });

  async function saveDescription(event: SubmitEvent) {
    event.preventDefault();
    if (busy || disabled) return;
    busy = true;
    error = "";
    notice = "";
    try {
      const updated = await studio.saveEntity(seriesId, entity.id, {
        identity: { visual_description: draft.trim() },
      });
      onupdate(updated);
      editing = false;
      notice = "Description enregistrée.";
    } catch (err) {
      error = errorMessage(err);
    } finally {
      busy = false;
    }
  }

  async function upload(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    input.value = "";
    if (!file || disabled || busy) return;
    error = "";
    notice = "";
    if (!file.type.startsWith("image/") || file.size > 25 * 1024 * 1024) {
      error = "Choisissez une image de 25 Mo maximum.";
      return;
    }
    busy = true;
    try {
      onupdate(await studio.uploadSeriesReference(seriesId, entity.id, file));
      notice = "Image enregistrée.";
    } catch (err) {
      error = errorMessage(err);
    } finally {
      busy = false;
    }
  }

  function closeEditing() {
    editing = false;
    draft = entity.identity.visual_description || entity.description || "";
    error = "";
  }
</script>

<article class="visual-card">
  <div class="artwork" class:missing={!entity.url}>
    {#if entity.url}
      <img src={entity.url} alt={"Référence visuelle : " + entity.name} loading="lazy" />
    {:else}
      <div class="no-image">
        <span class="placeholder-icon" aria-hidden="true">{entity.kind === "character" ? "◉" : "▧"}</span>
        <span>Aucune image</span>
      </div>
    {/if}
    <span class="photo-status" class:missing={!entity.url}>
      {entity.url ? "Image ajoutée" : "À illustrer"}
    </span>
  </div>

  <div class="body">
    <div class="identity">
      <div>
        <span class="kind">{entity.kind === "character" ? "PERSONNAGE" : "DÉCOR"}</span>
        <h4>{entity.name}</h4>
      </div>
    </div>

    {#if !editing}
      <p class="description" class:empty-description={!(entity.identity.visual_description || entity.description)}>
        {entity.identity.visual_description || entity.description ||
          (entity.kind === "character" ? "Décrivez son apparence en une phrase." : "Décrivez ce décor en une phrase.")}
      </p>
    {:else}
      <form class="edit-form" onsubmit={saveDescription}>
        <label for={"visual-" + entity.id}>
          {entity.kind === "character" ? "Apparence du personnage" : "Description du décor"}
        </label>
        <textarea id={"visual-" + entity.id} bind:value={draft} rows="3" maxlength="10000"
          disabled={busy || disabled}
          placeholder={entity.kind === "character"
            ? "Ex. Fraise rouge avec lunettes noires, robe chic et bijoux dorés."
            : "Ex. Terrasse d'hôtel luxueux, transats crème et vue sur la mer."}></textarea>
        <div class="edit-actions">
          <button type="submit" class="primary small" disabled={busy || disabled}>
            {busy ? "Enregistrement..." : "Enregistrer"}
          </button>
          <button type="button" class="small" disabled={busy} onclick={closeEditing}>Annuler</button>
        </div>
      </form>
    {/if}

    {#if error}<p class="error" role="alert">{error}</p>{/if}
    {#if notice}<p class="card-success" role="status">{notice}</p>{/if}

    <div class="actions">
      <input bind:this={picker} type="file" accept="image/*" hidden
        disabled={busy || disabled} onchange={upload} aria-label={"Choisir une image pour " + entity.name} />
      <button type="button" class="upload-button" disabled={busy || disabled} onclick={() => picker?.click()}>
        {busy ? "Patientez..." : entity.url ? "Changer l’image" : "Ajouter l’image"}
      </button>
      <button type="button" class="edit-button" disabled={busy || disabled}
        aria-expanded={editing}
        onclick={() => editing ? closeEditing() : (editing = true)}>
        {editing ? "Fermer" : "Description"}
      </button>
    </div>
  </div>
</article>

<style>
  .visual-card { min-width: 0; overflow: hidden; border: 1px solid #dedfd9; border-radius: 12px; background: #fff; display: flex; flex-direction: column; transition: border-color .15s, box-shadow .15s; }
  .visual-card:hover { border-color: #aabbb1; box-shadow: 0 5px 16px #17281e0d; }
  .artwork { height: 205px; position: relative; display: grid; place-items: center; overflow: hidden; background: #18241e; }
  .artwork.missing { background: linear-gradient(145deg, #ebf0e9, #e3e9e3); }
  .artwork img { display: block; width: 100%; height: 100%; object-fit: contain; }
  .no-image { color: #798a80; text-align: center; display: grid; justify-items: center; gap: 5px; font-size: 12px; }
  .placeholder-icon { font-size: 32px; line-height: 1; opacity: .5; }
  .photo-status { position: absolute; bottom: 10px; left: 10px; background: #f4fff2; border: 1px solid #c4e3c5; color: #235c37; padding: 5px 9px; border-radius: 20px; font-size: 11px; font-weight: 650; }
  .photo-status.missing { background: #ffffffed; border-color: #d5dcd4; color: #5d7163; }
  .body { padding: 16px; display: flex; flex-direction: column; gap: 12px; flex: 1; min-width: 0; }
  .identity { display: flex; justify-content: space-between; align-items: flex-start; gap: 6px; }
  .identity > div { min-width: 0; }
  .kind { color: #577b63; font-size: 10px; font-weight: 750; letter-spacing: .10em; }
  h4 { margin: 4px 0 0; font-size: 17px; line-height: 1.25; overflow-wrap: anywhere; }
  .description { color: #617068; line-height: 1.5; font-size: 13px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 40px; overflow-wrap: anywhere; }
  .empty-description { color: #89958b; font-style: italic; }
  .actions, .edit-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: stretch; }
  .actions { margin-top: auto; }
  .actions button { font-size: 12px; padding: 9px 11px; min-width: 0; }
  .upload-button { flex: 1 1 auto; font-weight: 650; }
  .edit-button { flex: 0 0 auto; }
  .edit-form { display: grid; gap: 8px; }
  .edit-form label { display: block; color: #344439; font-weight: 650; font-size: 12px; }
  .edit-form textarea { box-sizing: border-box; display: block; width: 100%; min-width: 0; line-height: 1.45; font-size: 13px; resize: vertical; }
  .error, .card-success { font-size: 12px; overflow-wrap: anywhere; }
  .error { color: #a22e2e; }
  .card-success { color: #23613c; }
</style>
