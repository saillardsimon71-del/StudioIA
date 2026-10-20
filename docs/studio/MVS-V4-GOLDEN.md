# MVS — Golden V4 (19 octobre 2026)

## Objectif

Prouver que le studio peut réassembler l'épisode 2 validé à partir des huit
prises existantes, sans GPU, sans TTS et sans script FFmpeg manuel.

## Résultat

| Élément | Mesure |
| --- | --- |
| Fichier | `work/ep02-v4/final/EPISODE-02-V4-golden.mp4` |
| Durée | 60,666 s |
| Image | 1080 × 1920, 24 fps, remplissage sans barres |
| Audio | AAC 48 000 Hz, stéréo |
| Loudness | −14,0 LUFS intégré, LRA 5,2 LU, crête vraie −1,9 dBFS |
| Structure | 8 scènes, 7 coupes, aucune coupe par réplique |
| ASR | 21 répliques + CTA retrouvées |

L'ASR signale en plus un « Hé ! » dans la prise 617 (entrée de cour), déjà
présente dans la V4 validée. « Duryo » est une variante ASR de « Durio ».
Aucun GPU, aucun TTS et aucune génération d'image n'ont été appelés.

## Corrections livrées

- `export.py` : format social 9:16 en 1080 × 1920 avec recadrage de
  remplissage ; loudness final −14 LUFS et limiteur de sécurité à −2 dBFS ;
  garde contre le loudness NaN sur une piste entièrement muette.
- `editorial.py` : `validate_cuts()` refuse plus d'une coupe par 8 secondes ;
  l'export bloque avant encodage. Un plan par réplique ne peut plus sortir.
- `ltx_prompt_v3.py` : « Only the named speaker speaks », « Speak each quoted
  line exactly once », la priorité de la voix et l'absence de texte à l'écran
  ne sont plus optionnels. Un dépassement critique échoue explicitement au
  lieu d'être supprimé en silence.
- Tests : refus des coupes par réplique, master social 1080 × 1920 / −14 LUFS,
  contraintes critiques du prompt, golden V4 sans GPU.

## Validation

```text
148 passed in 205.13s
```

La suite couvre `test_studio_export.py`, `test_studio_ltx_prompt.py`,
`test_studio_editorial.py`, `test_studio_ltx_realization.py`,
`test_studio_episode02_audit.py`, `test_studio_integration.py`,
`test_studio_production_regressions.py`, `test_studio_series.py` et
`test_studio_golden_v4.py`.

## Limite assumée

Le golden construit son snapshot dans le test à partir des huit fichiers. Il
prouve le moteur de montage/QC/export du studio, pas encore le parcours
board/UI qui attache les prises et appelle `enqueue_export`. La prochaine
étape est le mode source-locked : créer les 8 scènes, attacher les prises
605/633/634/617 et les 4 rendus V4, puis lancer un seul rendu cantine payant
via le studio.

La V4 manuelle validée (`EPISODE-02-V4-social.mp4`) et le projet 44 restent
intacts.
