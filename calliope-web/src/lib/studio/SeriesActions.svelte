<script lang="ts">
  import ConfirmDialog from "$lib/components/ui/ConfirmDialog.svelte";
  import { studio, errorMessage, type Series } from "./api";

  let { item, disabled = false, onchanged }: {
    item: Series;
    disabled?: boolean;
    onchanged: (deleted: boolean) => void | Promise<void>;
  } = $props();
  let busy = $state(false);
  let error = $state("");
  let confirmOpen = $state(false);
  let active = $derived(item.episodes.some((episode) => ["pending", "running"].includes(episode.status)));

  async function archive() {
    if (busy) return;
    busy = true;
    error = "";
    try {
      await studio.saveSeries(item.id, { archived: !item.archived });
      await onchanged(false);
    } catch (err) {
      error = errorMessage(err);
    } finally {
      busy = false;
    }
  }

  async function remove() {
    if (busy) return;
    busy = true;
    error = "";
    try {
      await studio.deleteSeries(item.id);
      await onchanged(true);
    } catch (err) {
      error = errorMessage(err);
    } finally {
      busy = false;
    }
  }
</script>

<div class="stack">
  <div class="row">
    <button class="small" disabled={disabled || busy || active} onclick={archive}>
      {item.archived ? "Restaurer" : "Archiver"}
    </button>
    <button class="small danger" disabled={disabled || busy || active} onclick={() => (confirmOpen = true)}>
      Supprimer
    </button>
  </div>
  {#if active}<p class="hint">Mettez la production en pause pour archiver ou supprimer cette série.</p>{/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</div>

<ConfirmDialog
  bind:open={confirmOpen}
  title="Supprimer la série ?"
  message={`Supprimer définitivement "${item.title}" et tous ses épisodes ? Pour les conserver, choisissez Archiver.`}
  confirmLabel="Supprimer"
  cancelLabel="Annuler"
  danger
  onconfirm={remove}
/>
