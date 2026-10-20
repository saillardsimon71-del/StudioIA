# Studio IA — reprise Arena AI / pilote « Bodycount » (20 octobre 2026)

> **Statut : préparation GitHub, pas reproduction du PC.** Cette branche a été créée depuis `fix/studio-story-repair-guardrails-20261018` (`fd84930e341f6e428fa0c49dbcf12f84338d3b9e`). Elle **ne contient pas** automatiquement le commit local `4dbfda3`, les modifications non commitées du 19 octobre, ni les images/MP4 stockés dans `work/` ou `publish/`. Ne pas présenter cette branche comme une version synchronisée du PC.

## 1. État établi

- Dépôt : `saillardsimon71-del/StudioIA`. Branche par défaut `feat/generalist-studio` à `3269424` (12 octobre), trop ancienne pour reprendre le nouveau pipeline sans perte.
- `codex/studio-director-manual` : `2c334ba`, +20 commits par rapport à la branche par défaut.
- `fix/studio-story-repair-guardrails-20261018` : `fd84930`, descend de Director et ajoute quatre commits sur les garde-fous de réparation narrative. **Tests documentés mais non exécutés sur cette branche**.
- `fix/studio-tts-voice-reference-20261018` : `f879cc3`, branche **divergente** ; ne pas présumer les changements de voix fusionnés. Voir `docs/studio/REPRISE-VOIX-CANON-2026-10-18.md`.
- `arena/33aa402b-studioia` : `402d2b6`, ancienne branche, **24 commits derrière** la branche par défaut.
- Recherche GitHub : ni le rapport `IDEA-15S-OBSERVATION-2026-10-19.md`, ni le test `bodycount`, ni le commit local `4dbfda3` ne sont présents dans les éléments distants consultés.

## 2. Journal de la session interrompue — déclarations de Work transmises par l'utilisateur, PAS revérifiées ici

Le 19 octobre, Work dit avoir corrigé la durée, la revue narrative, le regroupement des prises et les prompts avant GPU. 262 tests backend et les tests frontend auraient passé ; storyboard encore refusé (états d'objets contradictoires et gestes répétés). Plus tard : une image produite et importée ; 176 tests et préflight OK ; estimation 3,09 € sous un plafond de 4 € ; **pas de vidéo à ce stade**.

Après lancement du script local `work/bodycount15-restart.ps1`, la première prise LTX sur Modal aurait été rendue et les suivantes suspendues. Le QC local lançait Whisper depuis le mauvais environnement Python. Une transcription dans l'environnement Studio retrouvait les répliques, avec début de parole vers 2 s. Le contrôle visuel signalait **un sous-titre parasite** malgré le prompt d'interdiction. Work a refusé la prise, conservé les suivantes en pause, et commencé à brancher Tesseract sur le QC. **La session s'est arrêtée pendant les changements : leur statut Git et leur validité sont inconnus.**

## 3. Ordre obligatoire de reprise pour Arena AI

1. **Récupération sans perte** : inspecter sur le PC `git status -sb`, `git log -5 --oneline`, `git remote -v`, le commit `4dbfda3`, le rapport `docs/studio/IDEA-15S-OBSERVATION-2026-10-19.md`, les scripts `work/bodycount15-*.ps1` et les changements non commités. Ne faire ni `git reset --hard`, ni `git clean -fd`, ni force-push. Publier le travail local sur une **branche séparée** (`arena/bodycount-local-20261020`) et examiner les différences avant intégration dans cette branche.
2. **Réconciliation** : fusionner sélectivement les correctifs locaux du 19 octobre avec les garde-fous narratifs distants. La branche de verrouillage vocal TTS est une PR séparée/non testée : éviter une fusion silencieuse.
3. **Réparer le QC** : aligner le lanceur sur l'environnement Python du Studio où la transcription locale fonctionne. Ajouter et tester la détection OCR Tesseract des sous-titres imprévus, mais faire la différence entre sous-titres interdits et **chiffres volontairement affichés sur le compteur de la fiction**. Préférer un verdict « à revoir » à un faux succès ou un faux rejet.
4. **Tester sans coût GPU** : tests backend ciblés (QC, transcription, Director, préparation LTX, storyboard, reprise en pause), frontend, contrôle des prompts et de la cohérence des états/objets d'une prise à l'autre. Enregistrer commandes et résultats exacts. Ne pas interpréter « 176 tests verts » comme preuve que la vidéo est réussie.
5. **Premier vrai test** : reprendre seulement la première prise conservée, lire son MP4, inspecter les sous-titres parasitaires, le début de la première réplique, l'identité et l'état du compteur, puis passer par un contrôle humain. Si refusée, réparer et relancer une seule prise ciblée avec plafond de coût explicite ; aucune mise en file des deux prises restantes par défaut.
6. **Après validation seulement** : terminer les 3 prises totalisant ~15 s, assembler la vidéo, inspecter transitions/voix/compteur/sous-titres, et préparer l'export 9:16. Ne pas toucher à « En prison », aux médias et aux références voix canoniques.

## 4. Proposition créative — série nouvelle, séparée de « En prison »

Nom de travail : **COMPTEUR**. Genre : thriller comique absurde, personnages humains **adultes**, univers contemporain, format vertical 9:16. Un dispositif numérique prétend afficher en temps réel le « bodycount » des personnages (terme volontairement ambigu), puis révèle qu'il ne compte **pas ce qu'ils croient**. Un épisode = un mystère vérifiable, un indice visuel et une fin suspendue. **Ceci est une proposition éditoriale, non un storyboard validé.**

### Pilote expérimental : « Le compteur ne ment jamais » — objectif 15 secondes

- **Lieu unique :** petit studio d'appartement, soirée, compteur autonome posé sur une table. Deux personnages adultes, Léa et Max. Aucun fruit, aucune prison.
- **0–5 s (prise A, hook) :** gros plan compteur physique indiquant `000`, Léa l'attrape du regard, désigne l'écran sans le toucher. « Attends… ton bodycount, il est à zéro ? » Max, hors champ : « Je te jure. » À la fin, une seule impulsion sonore ; `001`. **Ne pas changer le décor ni la position du compteur.**
- **5–10 s (prise B, révélation impossible) :** même table, `001` conservé. Max, cadré avec Léa, recule en découvrant le chiffre. « Mais… j'ai rien fait ! » Léa, immobile : « Alors pourquoi ça vient de monter ? » **Pas de nouvel effet sur le compteur**.
- **10–15 s (prise C, cliffhanger) :** compteur toujours sur table, à `001`. Trois coups frappés à la porte hors champ ; le compteur passe à `002` **une seule fois**. Léa murmure : « Max… on est combien dans l'appart ? » Coupe noire avant la réponse.
- **Continuité contractuelle :** `000 → 001 → 001 → 002`, même accessoire et écran, mêmes costumes, deux personnages, pas de gestes répétitifs, une seule montée par événement, début des paroles calé sur les temps de prise. L'image de départ de chaque prise montre son **état initial**, jamais sa fin.
- **Contrôle texte :** pas de sous-titres, bulles, mots incrustés ni logos ; **seuls les trois chiffres diégétiques du compteur sont autorisés**. OCR ne doit pas confondre `000`/`001`/`002` avec un sous-titre parasite.
- **Production** : commencer avec **une prise de cinq secondes uniquement**, puis revue humaine, plutôt que payer trois erreurs en série. Conserver la possibilité de modifier l'idée après la preuve technique.

## 5. Garde-fous

- Aucune génération GPU, aucune dépense Modal/DeepSeek, aucun démarrage automatique et aucune publication par cette préparation GitHub.
- Préserver les séries, médias, comptes, secrets, bases et références voix locales. `work/`, `publish/` et `data/` ne doivent pas être importés en bloc dans GitHub (poids, données et secrets).
- Ne pas fusionner dans `feat/generalist-studio` avant tests et revue ; ne pas remplacer le commit local par l'état plus ancien du serveur distant.
- Objectif prioritaire : **un court film cohérent réellement exporté**, puis itération et publication. Le nombre de tests réussis n'est qu'un indicateur intermédiaire.
