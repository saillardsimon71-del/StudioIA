<script lang="ts">
  import { onDestroy } from "svelte";
  import { studio, errorMessage, type Series } from "./api";

  type Kind = "character" | "location";
  type Entry = {
    id: number;
    file: File;
    preview: string;
    name: string;
    description: string;
    kind: Kind;
    targetId: string;
    status: "ready" | "done" | "error";
    message: string;
  };

  let { series, disabled, onupdate }: {
    series: Series;
    disabled: boolean;
    onupdate: (updated: Series) => void;
  } = $props();
  let input = $state<HTMLInputElement | null>(null);
  let entries = $state<Entry[]>([]);
  let counter = 0;
  let dragging = $state(false);
  let uploading = $state(false);
  let feedback = $state("");

  onDestroy(() => {
    for (const entry of entries) URL.revokeObjectURL(entry.preview);
  });

  function comparable(value: string): string {
    return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .toLowerCase().replace(/[^a-z0-9]+/g, "");
  }

  function addFiles(files: FileList | File[]) {
    feedback = "";
    const images = Array.from(files).filter(file =>
      file.type.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp)$/i.test(file.name));
    if (!images.length) {
      feedback = "Sélectionnez des images PNG, JPEG ou WebP.";
      return;
    }
    const added = images.map(file => {
      const name = file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim();
      const match = series.entities.find(entity =>
        comparable(entity.name) === comparable(name) ||
        comparable(entity.key) === comparable(name));
      return {
        id: ++counter,
        file,
        preview: URL.createObjectURL(file),
        name: name || "Nouveau personnage",
        description: "",
        kind: (match?.kind === "location" ? "location" : "character") as Kind,
        targetId: match ? String(match.id) : "",
        status: "ready" as const,
        message: file.size > 25 * 1024 * 1024 ? "Image supérieure à 25 Mo." : "",
      };
    });
    entries = [...entries, ...added];
  }

  function selected(event: Event) {
    const picker = event.currentTarget as HTMLInputElement;
    if (picker.files) addFiles(picker.files);
    picker.value = "";
  }

  function dropped(event: DragEvent) {
    event.preventDefault();
    dragging = false;
    if (!disabled && !uploading && event.dataTransfer?.files) addFiles(event.dataTransfer.files);
  }

  function remove(entry: Entry) {
    URL.revokeObjectURL(entry.preview);
    entries = entries.filter(item => item.id !== entry.id);
    feedback = "";
  }

  function clearDone() {
    for (const entry of entries.filter(item => item.status === "done")) {
      URL.revokeObjectURL(entry.preview);
    }
    entries = entries.filter(item => item.status !== "done");
    feedback = "";
  }

  async function importAll() {
    if (disabled || uploading) return;
    const pending = entries.filter(entry => entry.status !== "done");
    if (!pending.length) return;
    const selectedIds = pending.map(entry => entry.targetId).filter(Boolean);
    if (new Set(selectedIds).size !== selectedIds.length) {
      feedback = "Deux images ciblent la même référence. Choisissez une seule image par personnage ou lieu.";
      return;
    }
    if (pending.some(entry => (!entry.targetId && !entry.name.trim()) ||
      entry.file.size > 25 * 1024 * 1024)) {
      feedback = "Vérifiez les noms et la taille des images (25 Mo maximum).";
      return;
    }
    uploading = true;
    feedback = "";
    let updated = series;
    let count = 0;
    for (const entry of pending) {
      entry.message = "";
      try {
        if (!entry.targetId) {
          const known = new Set(updated.entities.map(entity => entity.id));
          const created = await studio.createSeriesEntity(updated.id, {
            name: entry.name.trim(), kind: entry.kind, description: entry.description.trim(),
          });
          const entity = created.entities.find(candidate => !known.has(candidate.id));
          if (!entity) throw new Error("La référence créée est introuvable.");
          entry.targetId = String(entity.id);
          updated = created;
          onupdate(updated);
        }
        updated = await studio.uploadSeriesReference(updated.id, Number(entry.targetId), entry.file);
        onupdate(updated);
        entry.status = "done";
        count++;
      } catch (error) {
        entry.status = "error";
        entry.message = errorMessage(error);
      }
    }
    uploading = false;
    const failures = entries.filter(entry => entry.status === "error").length;
    feedback = count + " image(s) importée(s)." +
      (failures ? " " + failures + " échec(s) : corrigez et réessayez." : " Les références sont conservées pour la série.");
  }
</script>

<section class="reference-import" aria-labelledby="reference-import-title">
  <div class="heading">
    <div>
      <h2 id="reference-import-title">Personnages et décors</h2>
      <p class="hint">Ajoutez vos images avant de préparer l'épisode. Elles serviront de références visuelles à toute la série.</p>
    </div>
    <button type="button" class="primary" disabled={disabled || uploading} onclick={() => input?.click()}>
      Ajouter des images
    </button>
  </div>
  <input bind:this={input} type="file" accept="image/*" multiple
    onchange={selected} disabled={disabled || uploading} class="hidden-picker" aria-label="Choisir plusieurs images" />
  <div class:dragging class="dropzone" ondragover={(event) => { event.preventDefault(); dragging = true; }}
    ondragleave={() => dragging = false} ondrop={dropped} role="group"
    aria-label="Déposer des images de personnages ou de décors">
    Glissez plusieurs images ici, ou utilisez « Ajouter des images ».
  </div>

  {#if series.entities.length}
    <div class="existing">
      {#each series.entities as entity (entity.id)}
        <div class="existing-item">
          {#if entity.url}
            <img src={entity.url} alt={"Référence de " + entity.name} loading="lazy" />
          {:else}
            <div class="missing-image">Image à ajouter</div>
          {/if}
          <div class="existing-label">
            <strong>{entity.name}</strong>
            <span class="hint">{entity.kind === "location" ? "Décor" : "Personnage"}</span>
          </div>
        </div>
      {/each}
    </div>
  {/if}

  {#if entries.length}
    <div class="batch stack">
      <h3>Images sélectionnées ({entries.length})</h3>
      <p class="hint">Vérifiez l'association proposée à partir du nom du fichier. Choisissez une référence existante pour la remplacer, ou créez-en une nouvelle.</p>
      {#each entries as entry (entry.id)}
        <div class="entry">
          <img src={entry.preview} alt={"Aperçu " + entry.file.name} />
          <div class="entry-fields">
            <strong>{entry.file.name}</strong>
            <label>Associer à
              <select bind:value={entry.targetId} disabled={uploading || entry.status === "done"}>
                <option value="">Nouveau personnage ou décor</option>
                {#each series.entities as entity (entity.id)}
                  <option value={String(entity.id)}>{entity.name} — {entity.kind === "location" ? "décor" : "personnage"}{entity.url ? " (remplacer)" : ""}</option>
                {/each}
              </select>
            </label>
            {#if !entry.targetId}
              <div class="new-fields">
                <label>Nom<input bind:value={entry.name} maxlength="100" disabled={uploading || entry.status === "done"} /></label>
                <label>Type
                  <select bind:value={entry.kind} disabled={uploading || entry.status === "done"}>
                    <option value="character">Personnage</option>
                    <option value="location">Décor</option>
                  </select>
                </label>
              </div>
              <label>Description visuelle (facultative)
                <input bind:value={entry.description} maxlength="10000" disabled={uploading || entry.status === "done"}
                  placeholder="Ex. Fruit rouge, moustache noire, veste bleue" />
              </label>
            {/if}
            {#if entry.status === "done"}<p class="success">Image importée.</p>{/if}
            {#if entry.message}<p class="error">{entry.message}</p>{/if}
          </div>
          <button type="button" class="small" disabled={uploading} onclick={() => remove(entry)}
            aria-label={"Retirer " + entry.file.name}>Retirer</button>
        </div>
      {/each}
      <div class="actions">
        <button type="button" class="primary" disabled={uploading || disabled || !entries.some(entry => entry.status !== "done")}
          onclick={importAll}>{uploading ? "Importation en cours..." : "Importer les images"}</button>
        <button type="button" disabled={uploading || !entries.some(entry => entry.status === "done")}
          onclick={clearDone}>Effacer les imports terminés</button>
      </div>
    </div>
  {/if}
  {#if feedback}<p class="notice" role="status">{feedback}</p>{/if}
  <p class="hint">Les images déjà importées ne sont pas supprimées. Pour les descriptions détaillées et les validations, ouvrez les réglages avancés plus bas.</p>
</section>

<style>
  .reference-import { display: grid; gap: 16px; padding: 24px; background: var(--white); border: 1px solid var(--line); border-radius: 5px; }
  .heading, .actions { display: flex; gap: 14px; justify-content: space-between; align-items: center; flex-wrap: wrap; }
  .heading > div { display: grid; gap: 8px; max-width: 800px; }
  .hidden-picker { display: none; }
  .dropzone { padding: 22px; text-align: center; border: 2px dashed var(--line); border-radius: 6px; color: var(--muted); }
  .dropzone.dragging { border-color: var(--green); background: #e9f8ef; }
  .existing { display: grid; grid-template-columns: repeat(auto-fill, minmax(135px, 1fr)); gap: 12px; }
  .existing-item { overflow: hidden; border: 1px solid var(--line); border-radius: 5px; }
  .existing-item img, .missing-image { width: 100%; height: 115px; object-fit: contain; background: #17231e; }
  .missing-image { display: grid; place-items: center; color: white; font-size: 12px; }
  .existing-label { display: grid; gap: 3px; padding: 9px; }
  .existing-label strong { overflow-wrap: anywhere; }
  .batch { padding-top: 14px; border-top: 1px solid var(--line); }
  .entry { display: grid; grid-template-columns: 105px minmax(0, 1fr) auto; gap: 12px; align-items: start; border: 1px solid var(--line); padding: 12px; border-radius: 5px; }
  .entry > img { width: 105px; height: 105px; object-fit: contain; background: #17231e; }
  .entry-fields { min-width: 0; display: grid; gap: 9px; }
  .entry-fields strong { overflow-wrap: anywhere; }
  .entry-fields label { display: grid; gap: 5px; font-size: 13px; }
  .entry-fields input, .entry-fields select { width: 100%; }
  .new-fields { display: grid; grid-template-columns: minmax(0, 1fr) 125px; gap: 8px; }
  .success { color: #167944; }
  .error { color: #ae2828; }
  @media (max-width: 640px) { .entry { grid-template-columns: 75px minmax(0, 1fr); } .entry > img { width: 75px; height: 75px; } .entry > button { grid-column: 2; justify-self: start; } .new-fields { grid-template-columns: 1fr; } }
</style>
