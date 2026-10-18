# Studio IA — verrou de voix entre épisodes (18 octobre 2026)

Branche de base : `codex/studio-director-manual` (`2c334baef39e64a67a9e27da7407522f7f33a5da`).
Cette PR ne change pas la V3 publiée. Aucun doublage, GPU, ASR ou appel payant n'a été lancé.

## Constats établis dans le code

1. `tts._reference_for()` enregistrait le WAV dans un cache **par projet** (dérivé du chemin de la prise). Sur un nouvel épisode, le dossier change. Si aucun WAV n'est retrouvé, l'ancien code synthétisait une nouvelle voix sans demander l'avis de l'opérateur.
2. `tts._matches()` retournait `True` quand `studio_asr_checkpoint` était vide : **absence de contrôle = acceptation du dialogue**.
3. La signature du cache de doublage utilisait le nom/profil vocal et les paramètres, mais **pas les empreintes des WAV de référence**. Une référence changée pouvait être masquée par un cache de doublage jugé identique.
4. Dans le mode Director, il existe déjà des contrôles de validation humaine et des blocages de QC. Ne pas recréer une deuxième gestion parallèle d'approbation à partir du rapport externe.

## Corrections dans `studio/tts.py`

- Les épisodes d'une série utilisent maintenant un même dossier de références : `assets_dir/series/<series_id>/tts-voices/<speaker>.wav`. Les projets hors série conservent l'ancien chemin.
- Un fichier `<speaker>.sha256` verrouille les octets du WAV utilisés. Si ce WAV est modifié ou disparaît, **pas de nouvelle génération cachée** : échec explicite.
- Avant toute synthèse payante, les personnages qui ont déjà parlé dans **un export canonique publié** doivent disposer d'un WAV dans ce dossier partagé. La liste est tirée des `snapshot_json` d'exports liés à une révision canonique, pas des brouillons V1/V2 rejetés. Un **nouveau personnage** peut initialiser sa voix comme avant.
- Le cache TTS inclut `series_id` et les SHA-256 de chaque WAV de référence. Changer une référence invalide le cache.
- Sans checkpoint ASR local existant, le post-doublage s'arrête **avant** l'hydratation ou l'appel du service payant. `_matches` ne considère plus l'ASR indisponible comme validé.

**Important :** le hash SHA-256 garantit que les mêmes **octets audio** sont utilisés, pas que le timbre ou la prise soit de bonne qualité. La ressemblance effective avec l'épisode 1 V3 exige une comparaison humaine avec le film publié.

## Vérification et migration requises sur le PC

Pour « En prison » (série 30), retrouver d'abord les fichiers réellement utilisés pendant la production publiée V3, notamment dans `work/publish43/tts-voices/` et les sources de `publish/episode-01-FINAL-V3/`. Les fichiers ne sont pas dans GitHub.

1. **Écouter** les voix de l'épisode 1 V3 ; confirmer pour Durio et Momo l'échantillon exact à préserver. Ne pas considérer un nom de fichier `durio-ref.wav` comme preuve.
2. Après validation, **copier**, sans suppression ni transformation, les WAV retenus dans `calliope-backend/data/assets/series/30/tts-voices/durio.wav` et `momo.wav` (utiliser le `settings.assets_dir` réel si configuré autrement).
3. Si un `.sha256` existe déjà, vérifier l'intégrité. Ne jamais supprimer/modifier le verrou pour « faire passer » un fichier douteux ; le Studio exige une décision explicite et un nouvel audit.
4. Vérifier le checkpoint ASR configuré. Ne pas lancer de synthèse payante tant que ce contrôle manque.
5. Tester sur une prise **hors publication** avec moteur fictif/mocks, puis petite scène de 5 secondes seulement après accord budgétaire.

Il n'y a aucune migration automatique des anciens caches par projet : ils peuvent correspondre à un essai non approuvé, et les recopier silencieusement risquerait d'instituer une mauvaise voix comme référence.

## Tests à faire exécuter sans coût externe

```bash
cd calliope-backend
uv run pytest tests/test_studio_voice_reference_lock.py tests/test_studio_tts.py -vv
uv run pytest tests/test_studio_director.py tests/test_studio_transcription.py -vv
```

Ces nouveaux tests sont écrits mais **non exécutés dans l'environnement GitHub du connecteur**. Ils utilisent des WAV fictifs/mocks, des répertoires temporaires et n'appellent aucun moteur distant.

## Limites

- Les personnages présents dans un export canonique sont identifiés à partir des répliques enregistrées dans le snapshot. Si un export historique ne contient pas de métadonnées de locuteur fiables, le verrou ne peut pas reconnaître automatiquement ces voix : vérifier les données sur le PC.
- Une référence importée est figée dès son premier usage, mais l'approbation perceptive **n'est pas automatisée**.
- La mécanique de doublage actuelle n'est pas une preuve de synchronisation labiale.
- Ne pas fusionner sans passage des tests et revue des implications pour les anciens projets.
