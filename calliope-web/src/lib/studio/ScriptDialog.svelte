<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { studio, errorMessage, type Board } from "./api";
  let {
    board,
    onclose,
    onrefresh,
  }: { board: Board; onclose: () => void; onrefresh: () => Promise<void> } =
    $props();
  let dialog: HTMLDialogElement;
  let drafts = $state(
    untrack(() =>
      board.scenes.flatMap((scene) =>
        scene.shots.map((shot) => ({
          id: shot.id,
          description: shot.description,
          dialogue: shot.dialogue,
          prompt: shot.prompt,
          image_prompt: shot.image_prompt,
        })),
      ),
    ),
  );
  let error = $state("");
  let saving = $state(false);
  onMount(() => dialog.showModal());
  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (saving) return;
    saving = true;
    error = "";
    try {
      await studio.saveScript(board.project.id, drafts);
      await onrefresh();
      onclose();
    } catch (err) {
      error = errorMessage(err);
    } finally {
      saving = false;
    }
  }
</script>

<dialog bind:this={dialog} {onclose} aria-labelledby="script-title">
  <div class="dialog-header between">
    <h2 id="script-title">Modifier le scénario</h2>
    <button class="small" onclick={onclose} disabled={saving}>Fermer</button>
  </div>
  <form onsubmit={save}>
    <div class="dialog-body stack">
      <p class="hint">
        Un locuteur par plan. Gardez les répliques courtes. Les prises restent
        conservées ; les plans modifiés devront être régénérés ou approuvés
        avant le nouvel export.
      </p>
      {#if error}<p class="error" role="alert">
          {error}
        </p>{/if}{#each drafts as shot, index (shot.id)}<fieldset class="stack">
          <legend>Plan {index + 1}</legend><label
            >Action et intention<textarea rows="2" bind:value={shot.description}
            ></textarea></label
          ><label
            >Dialogue à entendre<textarea rows="2" bind:value={shot.dialogue}
            ></textarea></label
          >
          <details>
            <summary>Action filmée et référence visuelle</summary>
            <div class="stack advanced">
              <label
                >Action, cadrage, mouvement et son<textarea
                  rows="4"
                  bind:value={shot.prompt}
                ></textarea></label
              ><label
                >Image de départ<textarea
                  rows="3"
                  bind:value={shot.image_prompt}
                ></textarea></label
              >
            </div>
          </details>
        </fieldset>{/each}
    </div>
    <div class="dialog-actions row">
      <button type="button" onclick={onclose} disabled={saving}>Annuler</button
      ><button class="primary" disabled={saving}
        >{saving ? "Enregistrement..." : "Enregistrer le scénario"}</button
      >
    </div>
  </form>
</dialog>

<style>
  .advanced {
    margin-top: 14px;
  }
</style>
