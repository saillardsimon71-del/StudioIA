# 8 octobre 2026 — rythme utile, trims 24 fps et sound design synchronisé

Branche `arena/33aa402b-studioia`. Objectif : supprimer le montage artificiel
bâti sur des plans d'environ cinq secondes et la matière morte conservée à
l'export, tout en gardant un pipeline permissif (H3 génère librement, le
montage garde l'utile, les validateurs ne rejettent que les échecs manifestes).

## Doctrine temporelle

- Le scénariste écrit d'abord une histoire qui remplit la durée cible
  (65 secondes par défaut pour les nouvelles séries), puis découpe librement :
  nombre de plans libre (jusqu'à 40), `duration_sec` à 0,1 s près entre 1 et
  15 s, une réplique d'au plus 2,2 mots par seconde de son propre plan.
- La cible reste une contrainte de format : la somme des durées utiles doit
  rester à ±5 s de `target_seconds`, sinon le plan est réparé avant tout GPU.
- H3 continue de recevoir la marge dont il a besoin (124 frames minimum) ; le
  montage ramène un clip de 5,17 s à 1,6 s utiles quand c'est tout ce que le
  beat demande. Aucune prise n'est rallongée : plus courte que le beat, elle
  entre entière.

## Trim par plan (grille 24 fps commune)

- Nouveau module `studio/pacing.py` : quantification grille 24 fps, détection
  de parole (FFmpeg `silencedetect` + `volumedetect`, analyse locale sans GPU),
  fenêtre utile alignée sur la parole détectée, points explicites.
- `studio_shots` gagne `trim_start_sec` / `trim_end_sec` (migration douce par
  `ALTER TABLE`, valeurs nulles = coupe automatique). Éditables via PATCH et
  affichés dans l'éditeur de plan.
- L'export applique les fenêtres (`-ss`/`-t` par segment) ; SFX, holds et cues
  d'assets quantifient leurs départs sur la même grille (`mix_command` et
  `render_stems`). Le snapshot d'export transporte `beat_sec`, `dialogue` et
  les points de coupe ; les métadonnées d'export ajoutent `cuts_sec` et
  `grid_fps`.

## Validation dialogue tolérante

- `assess_dialogue` ne rejette que : réplique attendue absente (`missing`),
  invention importante (`extra`, parole > ~2,3 fois la réplique écrite), audio
  inexploitable (`inaudible`, niveau max < -48 dB). Mesure impossible =
  `unknown`, jamais bloquant.
- Tolérés (rattrapés par le trim, sans rerender) : début de parole tardif,
  silence court, quelques images de trop, léger décalage lèvres/voix.
- En production automatique, un échec manifeste déclenche **une** relance
  avec un nouvel identifiant de génération (seed fraîche), puis le montage
  continue avec la meilleure prise et un avertissement visible. Les prises
  bonnes ne rerend jamais.

## Sound design générique

- Ambiances : `room`, `wind`, `rain` + `cafe`, `night`, `city`, `forest`
  (lits synthétisés à très bas niveau, ducking sous les voix inchangé).
- Effets : `riser` (montée de tension ~2,2 s) et `boom` (accent grave) en plus
  de `click/beep/impact/whoosh/door/footstep`.
- Nouveaux `holds` : silences volontaires (`shot_index`, `at_sec`,
  `duration_sec`, `drop_db`) cuits dans les stems musique/ambiance — les voix
  ne sont jamais touchées.
- Le scénariste choisit musique très légère, ambiance de lieu cohérente, SFX
  éparses, au plus une ou deux retenues avant une révélation ; les prompts H3
  restent image + dialogue natif, la post-production fait le reste.

## Frontend et contrats

- Durée de plan 1–15 s à 0,1 s près (`ShotCreate`/`ShotUpdate`, éditeur) ;
  ligne « partie conservée » affichée dans la fiche plan.
- Types `Shot.trim_*`, `AudioHold` ; options effet/ambiance du dialogue mix ;
  création de série à 65 s par défaut.

## Tests

`tests/test_studio_pacing.py` (24 tests) : durées variables préservées et
format 65 s, fenêtres utiles sans padding, trims explicites et guidés par la
parole, tolérance de la validation et échecs manifestes, grille SFX 24 fps,
holds, ducking fonctionnel, une seule relance GPU, routes flexibles. Suites
existantes ajustées : défaut série 65 s, budget de tokens sur la cible,
réparation d'épisode.
