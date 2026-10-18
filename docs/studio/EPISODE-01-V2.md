# Episode 1 v2 — montage cinématographique

**But** : corriger le montage « une coupe par réplique » et l’inversion de voix, sans refaire le scénario.

## Découpage final

| # | Plan | Durée | Contenu | Audio |
| --- | --- | ---: | --- | --- |
| A | Comptoir, master 2-shot | 14,0 s | Plateau claqué, répliques 1–6, joues attrapées, plateau rempli. Aucune coupe. | Natif, exact |
| B | Cantine, tracking | 11,0 s | Durio s’adresse à toute la salle (« Hé, le bloc B !… »), puis rejoint Momo et lui parle. Aucune coupe entre les deux. | TTS Durio + « Toi » natif |
| C | Table, medium 2-shot | 11,0 s | Momo répond, Durio renverse le plateau, Momo enlève ses lunettes et se lève. | TTS Momo (réplique 9) + natif Durio/Momo |
| D | Cliffhanger | 2,7 s | Gros plan Durio, sourire qui disparaît, silence. | Ambiance seule |
| E | CTA | 3,4 s | « Commente GO pour recevoir le guide. » | TTS Durio |

**Total : 42,2 s. 5 plans, 4 coupes.**

## Corrections

- **B** est un seul plan : l’apostrophe à la prison et le déplacement vers Momo ne sont plus séparés.
- **C** est un medium plus serré : la coupe B→C est motivée par le retournement, pas un faux raccord sur le même cadre.
- **A** garde l’audio natif exact : six répliques, bonnes voix, aucune retouche.
- **C** garde le Durio natif et les deux répliques Momo natives correctes ; seule la réplique inversée « À moi… » est remplacée par TTS Momo.
- **B** est post-doublé uniquement sur la fin de la ligne 8 ; « Hé, le bloc B ! » et « Toi, » restent natifs.
- **CTA** passe de ~6 s à **1,85 s**.
- Les coupes sont espacées (aucune coupe par réplique) et l’ambiance est continue.

## Pipeline

- `calliope-backend/src/calliope/studio/editorial.py` : lint éditorial (coupes/8 s, durée mini, CTA ≤ 4 s, avertissement multi-locuteur sans post-dub).
- `ltx_realization.prepare_ltx_plan` : `post_dub=True` empêche le split automatique par locuteur.
- `calliope-backend/tests/test_studio_editorial.py` : 5 tests.
- `work/audit/tools/render_ep02.py` : 5 prises longues.
- `work/audit/tools/post_ep02.py` : post-doublage ciblé, ambiance, assemblage.
- `work/audit/tools/tts_ep02.py` : synthèse/références vocales.

## Risques résiduels

1. **B, réplique 7** : le TTS prononce « qu’il mange » au lieu de « qui mange ». C’est la seule approximation textuelle restante ; à écouter.
2. **C** : la vidéo medium a été refaite, mais l’audio de Durio/Momo vient de la prise précédente. Le lip-sync est approximatif, pas bloquant en animation.
3. **E** : le TTS dit bien « Commente GO pour recevoir le guide » à la source ; l’ASR du mix entend parfois autre chose.
4. Le timbre TTS Momo de la réplique 9 peut différer légèrement du Momo natif.
