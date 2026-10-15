<script lang="ts">
  import { onDestroy } from "svelte";
  import { studio, errorMessage, type Series } from "./api";
  import SeriesReferenceCard from "./SeriesReferenceCard.svelte";

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

  const groups: { kind: Kind; title: string }[] = [
    { kind: "character", title: "Personnages" },
    { kind: "location", title: "Décors" },
  ];
  const MAX_SIZE = 25 * 1024 * 1024;

  let { series, disabled, onupdate }: {
    series: Series;
    disabled: boolean;
    onupdate: (updated: Series) => void;
  } = $props();
  let picker = $state<HTMLInputElement | null>(null);
  let entries = $state<Entry[]>([]);
  let counter = 0;
  let dragging = $state(false);
  let uploading = $state(false);
  let feedback = $state("");
  let showNew = $state(false);
  let creating = $state(false);
  let newName = $state("");
  let newKind = $state<Kind>("character");
  let newDescription = $state("");

  onDestroy(() => {
    for (const entry of entries) URL.revokeObjectURL(entry.preview);
  });

  function comparable(value: string) {
    return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .toLowerCase().replace(/[^a-z0-9]+/g, "");
  }

  function addFiles(files: FileList | File[]) {
    if (uploading || disabled) return;
    feedback = "";
    const valid = Array.from(files).filter(file =>
      file.type.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp)$/i.test(file.name));
    if (!valid.length) {
      feedback = "Sélectionnez des images PNG, JPEG ou WebP.";
      return;
    }
    const added: Entry[] = valid.map(file => {
      const name = file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim();
      const match = series.entities.find(entity =>
        comparable(entity.name) === comparable(name) || comparable(entity.key) === comparable(name));
      return {
        id: ++counter,
        file,
        preview: URL.createObjectURL(file),
        name: name || "Nouveau personnage",
        description: "",
        kind: match?.kind === "location" ? "location" : "character",
        targetId: match ? String(match.id) : "",
        status: "ready",
        message: file.size > MAX_SIZE ? "Image supérieure à 25 Mo." : "",
      };
    });
    entries = [...entries, ...added];
  }

  function selected(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    if (input.files) addFiles(input.files);
    input.value = "";
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
    const ids = pending.map(entry => entry.targetId).filter(Boolean);
    if (new Set(ids).size !== ids.length) {
      feedback = "Plusieurs fichiers ciblent la même référence. Associez une image par personnage ou décor.";
      return;
    }
    if (pending.some(entry => (!entry.targetId && !entry.name.trim()) ||
      entry.file.size > MAX_SIZE)) {
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
          const previousIds = new Set(updated.entities.map(entity => entity.id));
          const created = await studio.createSeriesEntity(updated.id, {
            name: entry.name.trim(), kind: entry.kind, description: entry.description.trim(),
          });
          const entity = created.entities.find(candidate => !previousIds.has(candidate.id));
          if (!entity) throw new Error("Impossible de retrouver la nouvelle référence.");
          entry.targetId = String(entity.id);
          updated = created;
          onupdate(updated);
        }
        updated = await studio.uploadSeriesReference(updated.id, Number(entry.targetId), entry.file);
        onupdate(updated);
        entry.status = "done";
        count++;
      } catch (err) {
        entry.status = "error";
        entry.message = errorMessage(err);
      }
    }
    uploading = false;
    const failed = pending.filter(entry => entry.status === "error").length;
    feedback = count + " image(s) ajoutée(s)." +
      (failed ? " " + failed + " erreur(s) : corrigez et réessayez." : " Vos références sont enregistrées.");
  }

  async function createWithoutImage(event: SubmitEvent) {
    event.preventDefault();
    if (disabled || uploading || creating || !newName.trim()) return;
    creating = true;
    feedback = "";
    try {
      const updated = await studio.createSeriesEntity(series.id, {
        name: newName.trim(), kind: newKind, description: newDescription.trim(),
      });
      onupdate(updated);
      showNew = false;
      newName = "";
      newDescription = "";
      feedback = "Référence créée. Vous pourrez lui ajouter une image ensuite.";
    } catch (err) {
      feedback = errorMessage(err);
    } finally {
      creating = false;
    }
  }
</script>

<section class="library" aria-labelledby="visual-library-title">
  <div class="library-header">
    <div class="intro">
      <span class="eyebrow">BIBLIOTHÈQUE VISUELLE</span>
      <h2 id="visual-library-title">Personnages & décors</h2>
      <p>Une image, un nom, une petite description. Le studio réutilisera ces références pour les prochains épisodes.</p>
    </div>
    <div class="header-actions">
      <span class="count">{series.entities.length} référence{series.entities.length > 1 ? "s" : ""}</span>
      <button type="button" class="primary" disabled={disabled || uploading || creating}
        onclick={() => picker?.click()}>+ Importer des images</button>
    </div>
  </div>

  <input bind:this={picker} type="file" accept="image/*" multiple hidden
    disabled={disabled || uploading || creating} onchange={selected}
    aria-label="Sélectionner plusieurs images de personnages ou de décors" />

  {#each groups as group (group.kind)}
    {#if series.entities.some(entity => entity.kind === group.kind)}
      <div class="group-heading">
        <h3>{group.title}</h3>
        <span class="group-count">{series.entities.filter(entity => entity.kind === group.kind).length}</span>
      </div>
      <div class="gallery">
        {#each series.entities.filter(entity => entity.kind === group.kind) as entity (entity.id)}
          <SeriesReferenceCard seriesId={series.id} {entity}
            disabled={disabled || uploading || creating} {onupdate} />
        {/each}
      </div>
    {/if}
  {/each}

  {#if !series.entities.length}
    <div class="empty-state">
      <div class="empty-art" aria-hidden="true">✦</div>
      <h3>Votre univers commence ici</h3>
      <p>Importez les portraits de vos personnages et les images de vos décors. Vous pourrez ensuite créer un épisode en conservant leur apparence.</p>
    </div>
  {/if}

  <button type="button" class="dropzone" class:dragging
    disabled={disabled || uploading || creating}
    onclick={() => picker?.click()}
    ondragover={(event) => { event.preventDefault(); dragging = true; }}
    ondragleave={() => dragging = false} ondrop={dropped}>
    <span class="drop-icon" aria-hidden="true">↑</span>
    <span><strong>Déposez vos images ici</strong> ou cliquez pour en sélectionner plusieurs.</span>
    <small>PNG, JPEG, WebP — 25 Mo maximum par image</small>
  </button>

  {#if entries.length}
    <div class="batch">
      <div class="batch-heading">
        <div>
          <h3>Préparer l’import</h3>
          <p>Vérifiez le nom et la destination de chaque image. Un nom de fichier identique à un personnage déjà présent est automatiquement associé.</p>
        </div>
        <span class="batch-counter">{entries.filter(entry => entry.status === "done").length}/{entries.length} importées</span>
      </div>
      <div class="batch-list">
        {#each entries as entry (entry.id)}
          <div class="batch-item">
            <img class="preview" src={entry.preview} alt={"Aperçu de " + entry.file.name} />
            <div class="batch-fields">
              <strong class="filename" title={entry.file.name}>{entry.file.name}</strong>
              <label>Associer à
                <select bind:value={entry.targetId} disabled={disabled || uploading || entry.status === "done"}>
                  <option value="">Créer une nouvelle référence</option>
                  {#each series.entities as entity (entity.id)}
                    <option value={String(entity.id)}>{entity.name} · {entity.kind === "location" ? "Décor" : "Personnage"}</option>
                  {/each}
                </select>
              </label>
              {#if !entry.targetId}
                <div class="batch-pair">
                  <label>Nom<input bind:value={entry.name} maxlength="100"
                    disabled={disabled || uploading || entry.status === "done"} /></label>
                  <label>Type
                    <select bind:value={entry.kind} disabled={disabled || uploading || entry.status === "done"}>
                      <option value="character">Personnage</option>
                      <option value="location">Décor</option>
                    </select>
                  </label>
                </div>
                <label>Description (facultative)
                  <input bind:value={entry.description} maxlength="10000"
                    disabled={disabled || uploading || entry.status === "done"}
                    placeholder="Ex. Banane jaune, chemise blanche, lunettes dorées" />
                </label>
              {/if}
              {#if entry.status === "done"}<p class="success">✓ Image ajoutée</p>{/if}
              {#if entry.message}<p class="entry-error">{entry.message}</p>{/if}
            </div>
            <button type="button" class="remove" disabled={uploading}
              onclick={() => remove(entry)} aria-label={"Retirer " + entry.file.name}>×</button>
          </div>
        {/each}
      </div>
      <div class="batch-actions">
        <button type="button" class="primary" onclick={importAll}
          disabled={disabled || uploading || creating || !entries.some(entry => entry.status !== "done")}>
          {uploading ? "Importation en cours…" : "Enregistrer les images"}
        </button>
        <button type="button" disabled={uploading || !entries.some(entry => entry.status === "done")}
          onclick={clearDone}>Effacer les éléments terminés</button>
      </div>
    </div>
  {/if}

  <div class="footer-row">
    <button type="button" class="text-action" disabled={disabled || uploading || creating}
      aria-expanded={showNew} onclick={() => showNew = !showNew}>
      {showNew ? "− Fermer" : "+ Ajouter un personnage ou décor sans image"}
    </button>
    <span class="footnote">Les réglages complexes restent dans « Options avancées » en bas de page.</span>
  </div>

  {#if showNew}
    <form class="manual-create" onsubmit={createWithoutImage}>
      <label>Nom<input bind:value={newName} placeholder="Ex. Velvet, Bar, Piscine…" required maxlength="100"
        disabled={disabled || creating || uploading} /></label>
      <label>Type
        <select bind:value={newKind} disabled={disabled || creating || uploading}>
          <option value="character">Personnage</option><option value="location">Décor</option>
        </select>
      </label>
      <label class="wide">Description courte (facultative)
        <input bind:value={newDescription} maxlength="10000"
          placeholder="Apparence ou environnement principal"
          disabled={disabled || creating || uploading} />
      </label>
      <button type="submit" class="primary" disabled={disabled || creating || uploading || !newName.trim()}>
        {creating ? "Création…" : "Ajouter"}
      </button>
    </form>
  {/if}

  {#if feedback}<p class="feedback" role="status">{feedback}</p>{/if}
</section>

<style>
  .library { display: grid; gap: 20px; min-width: 0; padding: 26px; border: 1px solid #d8e0d8; background: linear-gradient(180deg, #f8faf7 0, #f3f6f1 100%); border-radius: 15px; }
  .library-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; flex-wrap: wrap; }
  .intro { max-width: 710px; display: grid; gap: 7px; }
  .eyebrow { letter-spacing: .12em; font-weight: 800; font-size: 10px; color: #3e7952; }
  h2 { margin: 0; font-size: 24px; line-height: 1.2; }
  .intro p, .batch-heading p { color: #63746a; font-size: 13px; line-height: 1.55; margin: 0; }
  .header-actions { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .count { color: #607668; font-size: 12px; font-weight: 650; }
  .header-actions button { white-space: nowrap; }
  .group-heading { display: flex; align-items: center; gap: 8px; border-bottom: 1px solid #dfe5dc; padding-bottom: 10px; }
  .group-heading h3 { font-size: 16px; margin: 0; }
  .group-count { padding: 3px 8px; border-radius: 15px; color: #51775d; font-size: 11px; background: #e5efe5; font-weight: 700; }
  .gallery { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(min(100%, 225px), 1fr)); align-items: stretch; }
  .empty-state { text-align: center; display: grid; justify-items: center; gap: 9px; padding: 25px 15px; }
  .empty-state h3 { margin: 0; font-size: 18px; }
  .empty-state p { max-width: 410px; margin: 0; color: #607568; font-size: 13px; line-height: 1.6; }
  .empty-art { color: #528c68; background: #e4eee3; border-radius: 18px; display: grid; place-items: center; font-size: 32px; width: 62px; height: 62px; }
  .dropzone { min-width: 0; border: 1.5px dashed #a0baa7; background: #eff5ee; border-radius: 11px; color: #3d674a; display: grid; justify-items: center; gap: 5px; padding: 17px 14px; cursor: pointer; text-align: center; font-size: 13px; line-height: 1.4; }
  .dropzone:hover, .dropzone.dragging { background: #e0f0df; border-color: #497f5d; }
  .dropzone small { color: #758879; font-size: 11px; }
  .drop-icon { font-size: 22px; font-weight: 700; line-height: 1; }
  .batch { display: grid; gap: 14px; background: #fff; padding: 18px; border-radius: 12px; border: 1px solid #dbe2da; }
  .batch-heading, .batch-actions, .footer-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .batch-heading h3 { font-size: 17px; margin: 0 0 6px; }
  .batch-heading > div { max-width: 730px; }
  .batch-counter { color: #507962; white-space: nowrap; font-size: 12px; font-weight: 650; }
  .batch-list { display: grid; gap: 10px; }
  .batch-item { display: grid; grid-template-columns: 100px minmax(0, 1fr) 30px; gap: 14px; padding: 12px; border: 1px solid #e4e8e1; border-radius: 9px; align-items: start; min-width: 0; }
  .preview { display: block; width: 100px; height: 115px; object-fit: contain; background: #19251e; border-radius: 7px; }
  .batch-fields { min-width: 0; display: grid; gap: 9px; }
  .filename { font-size: 13px; overflow-wrap: anywhere; }
  .batch-fields label, .manual-create label { display: grid; gap: 5px; font-size: 12px; font-weight: 650; color: #475d4e; min-width: 0; }
  .batch-fields select, .batch-fields input, .manual-create input, .manual-create select { width: 100%; min-width: 0; box-sizing: border-box; }
  .batch-pair { display: grid; grid-template-columns: minmax(0, 1fr) minmax(110px, 140px); gap: 10px; }
  .remove { padding: 5px 8px; border: 0; background: #f6f6f2; color: #667669; font-size: 19px; border-radius: 6px; }
  .success { font-size: 12px; color: #2f7d49; }
  .entry-error { color: #ac3535; font-size: 12px; overflow-wrap: anywhere; }
  .batch-actions { justify-content: flex-start; }
  .footer-row { border-top: 1px solid #dfe6db; padding-top: 14px; }
  .text-action { color: #2f6842; background: transparent; border: none; padding: 5px 0; text-align: left; font-size: 12px; font-weight: 700; }
  .footnote { font-size: 11px; color: #79897d; }
  .manual-create { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, .65fr); gap: 12px; background: #fff; border: 1px solid #dbe2d9; border-radius: 10px; padding: 16px; }
  .manual-create .wide { grid-column: 1 / -1; }
  .manual-create button { justify-self: start; }
  .feedback { font-size: 13px; color: #315d40; background: #e8f4e8; border: 1px solid #d0e5d0; border-radius: 8px; padding: 10px 13px; overflow-wrap: anywhere; }
  @media (max-width: 640px) {
    .library { padding: 16px; gap: 15px; }
    .header-actions { width: 100%; justify-content: space-between; }
    .gallery { grid-template-columns: repeat(auto-fill, minmax(min(100%, 220px), 1fr)); }
    .batch-item { grid-template-columns: 75px minmax(0, 1fr); }
    .preview { width: 75px; height: 90px; }
    .remove { grid-column: 2; justify-self: start; }
    .batch-pair, .manual-create { grid-template-columns: 1fr; }
    .manual-create .wide { grid-column: auto; }
  }
</style>
