<script lang="ts">
  import { studio, errorMessage, type Series, type SeriesEntity } from './api';
  let { seriesId, entity, entities, disabled, onupdate }: {
    seriesId: number; entity: SeriesEntity; entities: SeriesEntity[]; disabled: boolean;
    onupdate: (series: Series) => void;
  } = $props();
  let visual = $state('');
  let base = $state('');
  let value = $state<number | undefined>();
  let labelKey = $state('');
  let busy = $state(false);
  let error = $state('');
  $effect(() => {
    visual = entity.identity.visual_description ?? '';
    base = entity.identity.variant_of ?? '';
    value = entity.identity.attached_value ?? undefined;
    labelKey = entity.identity.label_key ?? '';
  });
  async function save(action: 'identity' | 'approve' | 'reset') {
    busy = true; error = '';
    try {
      onupdate(await studio.saveEntity(seriesId, entity.id,
        action === 'identity' ? { identity: { visual_description: visual, variant_of: base,
          attached_value: value ?? null, label_key: labelKey } }
        : action === 'approve' ? { reference_approved: !entity.identity.reference_approved }
        : { reset_reference: true }));
    } catch (err) { error = errorMessage(err); }
    finally { busy = false; }
  }
</script>

<div class="stack">
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <label>Identité visuelle immuable<textarea rows="3" bind:value={visual} disabled={disabled || busy}
    placeholder="Espèce, forme du corps, traits du visage, yeux, bouche et tenue." ></textarea></label>
  <label>Variante de<select bind:value={base} disabled={disabled || busy}>
    <option value="">Identité de base</option>
    {#each entities.filter((item) => item.id !== entity.id && item.kind === entity.kind) as item}
      <option value={item.key}>{item.name} ({item.key})</option>
    {/each}
  </select></label>
  {#if entity.kind === 'character'}
    <label>Valeur exacte attachée<input type="number" step="1" bind:value={value} disabled={disabled || busy} /></label>
    <label>Clé stable de la valeur<input bind:value={labelKey} disabled={disabled || busy} placeholder="Identique entre variantes du même personnage" /></label>
  {/if}
  <button disabled={disabled || busy} onclick={() => save('identity')}>Enregistrer l'identité</button>
  <p class="hint">{entity.identity.reference_approved ? 'Référence approuvée.' : 'Référence à vérifier.'} Vérifiez l'espèce, le visage et la tenue sur l'image réelle avant de l'approuver.</p>
  <div class="row">
    <button disabled={disabled || busy || !entity.url || !entity.identity.visual_description?.trim()}
      onclick={() => save('approve')}>{entity.identity.reference_approved ? "Retirer l'approbation" : 'Approuver cette référence'}</button>
    <button disabled={disabled || busy || !entity.url} onclick={() => save('reset')}>Réinitialiser la référence</button>
  </div>
</div>
