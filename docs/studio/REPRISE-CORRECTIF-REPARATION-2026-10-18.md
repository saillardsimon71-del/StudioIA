# Studio IA — handoff pré-Codex : réparation du scénario et preuves Épisode 2 V2

Référence de départ : `codex/studio-director-manual` au commit `2c334baef39e64a67a9e27da7407522f7f33a5da`.

## 1. Correction effectivement apportée sur cette branche

**Périmètre limité :** `calliope-backend/src/calliope/studio/series_planner.py`.

- La vraie boucle est `series_planner.plan_episode()`, **pas** le flux hypothétique `director.py` du rapport externe.
- Un plan de réalisation technique qui perd **plus de 2 répliques** par rapport à l'histoire créative ne peut pas être réparé en ciblant les seuls tableaux `shots` des scènes encore présentes. La même limite s'applique aux ajouts massifs.
- Une `_UnrecoverableStoryRealizationError` interrompt la boucle immédiatement. Le traitement `except ValueError` conserve le message d'erreur et les diagnostics, puis relève l'exception avant tout nouvel appel DeepSeek. Le seuil conservateur de 2 écarts est une politique de sécurité à revoir selon les résultats.
- Les erreurs localisées et peu étendues restent réparables suivant le chemin existant `REPAIR VALUES ONLY`.

**Cas exact reproduit par le test préexistant** : `tests/test_studio_repair_values.py::test_unlocated_error_stops_without_arbitrary_whole_episode_repair` attend 2 appels texte (histoire + réalisation défectueuse), alors que la version de départ en faisait 4. Les cinq répliques manquantes devraient désormais déclencher l'arrêt sur la deuxième réponse. **Cette prédiction n'est pas un résultat de test.**

Tests **ajoutés et non exécutés** dans `tests/test_studio_story_repair_guardrails.py` :

1. Trois répliques manquantes -> erreur non réparable, pas de fausse localisation.
2. Une réplique manquante -> chemin de réparation ciblée existant préservé.
3. Trois répliques ajoutées -> arrêt, pas de réécriture arbitraire.

### Vérification à exécuter sur le PC, sans GPU ni modèle payant

Depuis `calliope-backend`, après récupération de cette branche et **sans reset --hard** :

```bash
uv run pytest tests/test_studio_repair_values.py -k "test_unlocated_error_stops_without_arbitrary_whole_episode_repair" -vv
uv run pytest tests/test_studio_story_repair_guardrails.py tests/test_studio_story_preparation.py -vv
uv run pytest tests/test_studio_repair_values.py -vv
```

Lancer ensuite les tests backend pertinents de la suite existante. Conserver les sorties exactes et corriger toutes les régressions avant fusion.

**Statut : PR de travail, NON TESTÉE localement, PAS FUSIONNÉE.** Aucun modèle payant, aucune génération vidéo, aucune modification de base locale ou de configuration n'a été effectuée.

## 2. Vérifications factuelles du dossier de preuves, indépendantes du rapport externe

- `docs/studio/episode-02-v2-evidence/render_v2.py.txt` : six spécifications `SHOTS` avec images locales distinctes, puis appels `ModalH3Engine.spawn` dans une boucle. Ce script n'assure aucun chaînage de l'image finale du plan précédent vers le suivant. Cela **expose à un risque** de rupture visuelle ; la gravité exacte dépend des médias.
- `tts_build.py.txt` : les fichiers de référence sont copiés depuis `work/publish43/tts-voices`. Leur correspondance avec la voix effectivement entendue dans l'épisode 1 **publié V3** n'est pas démontrée par un simple nom de fichier. En cas d'absence, `ensure_reference()` peut synthétiser une nouvelle référence pour un personnage donné ; les conditions exactes doivent être vérifiées personnage par personnage.
- `tts_build.py.txt` **utilise déjà** `transcription.transcribe_dialogue()` et `assess_transcript_v2()`. Le diagnostic externe disant qu'aucun ASR n'est exécuté sur les prises TTS est donc inexact. Cependant la sélection `best` permet, en l'absence de verdict `ok`, de garder la première synthèse ; le contrôle du résultat doit être revu.
- `assemble_v2.py.txt` : `tts_dialogue()` refuse une durée de parole qui déborde le plan (`prev_end > duration + 0.02`). Un écrêtage mécanique de toutes les répliques n'est donc **pas démontré**. Les retards, rythmes artificiels et fondus restent à écouter.
- `assemble_v2.py.txt` : les plans A/B/C/F prennent le doublage TTS et des ambiances/sfx synthétiques ; les plans D/E prennent la piste native. Les cinq raccords utilisent `xfade`/`acrossfade` de 4 images à 24 fps. Normalisation intermédiaire à -18 LUFS puis finale à -14 LUFS. Il y a bien des ambiances et des fondus ; leur qualité et la cohérence entre les deux modes restent incertaines.
- `docs/studio/VERIFICATION-PUBLICATION-2026-10-18.md` : le test des appels supplémentaires **reste en échec** dans la version de référence ; les autres tests cités correspondent à des exécutions antérieures sur le PC, pas à ce correctif.
- `media-manifest.json`, les rapports QC et les copies textuelles ne remplacent **ni** le visionnage de l'épisode 2 V2, **ni** l'écoute comparative de l'épisode 1 V3, ni un test GPU court.

## 3. Recommandation de priorité après la vérification du correctif

1. Prouver le passage du test réel et des tests adjacents ; corriger une éventuelle régression, ne pas fusionner au seul vu du diff.
2. Tracer la référence vocale précise de Durio et Momo sur l'épisode 1 V3 et empêcher les changements silencieux dans le pipeline réutilisable.
3. Comparer les six médias V2 originaux (voix TTS, pistes natives, exports des plans) et l'épisode 1 V3 en accès local ; produire des constats horodatés.
4. Faire remonter les contrôles défaillants dans le QC de `production.py` / `service.py` et dans l'interface, sans créer un second système parallèle.
5. Tester une seule scène de 5 s après validation du code et estimation du coût, sans nouvelle génération de l'épisode entier.

## 4. Garde-fous

Ne pas appliquer le patch complet de l'audit externe : il introduit des modèles annexes sans intégration démontrée au code existant. Préserver tous les projets, médias, voix, références utilisateur, Octopus et les préférences CTA. Aucune clé dans Git ; aucun appel GPU ou DeepSeek payant pour l'audit. Ne pas toucher à la branche de référence ni à la branche par défaut sans revue.
