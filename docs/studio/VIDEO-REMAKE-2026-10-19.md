# Vidéo à refaire - plan et décisions

## Objectif

Importer un MP4 dans une série, mesurer son découpage, transcrire localement,
faire adapter son récit par DeepSeek avec le canon de cette série, puis utiliser
le storyboard, les images manuelles, le préflight, les prises et l'export existants.
La V4, le projet 44 et les fichiers publiés restent intacts.

## Choix

- Import MP4 local uniquement pour cette tranche. Pas de téléchargement TikTok.
- FFmpeg et Whisper CPU existants, sans nouvelle dépendance ni téléchargement de modèle.
- Analyse en tâche de fond, enregistrée et consultable. Relance explicite si interrompue.
- Les coupes sont des détections heuristiques à revoir (fondus et raccords
  similaires peuvent échapper au détecteur). Durées et mots ASR horodatés sont
  des mesures. Un panneau facultatif permet de corriger les coupes avant adaptation,
  en conservant les anciennes images et en invalidant l'approbation. Expressions, identités,
  gestes et mouvements caméra restent non vérifiés sans analyse visuelle :
  images extraites et notes éditables permettent la revue avant adaptation.
- DeepSeek adapte les dialogues dans la langue de la série, avec les personnages
  existants. Les données importées sont du contenu, jamais des instructions.
- Le découpage source est verrouillé par étapes ; les continuations techniques
  restent possibles. Les durées et toutes les répliques adaptées sont contrôlées.
- Appels texte uniquement sur action explicite dans l'interface. Aucun appel
  payant durant le développement. Images exclusivement manuelles dans ChatGPT.
- Stockage additionnel, aucune réécriture des épisodes existants.

## Incréments exécutés dans cette session

- [x] Analyse source : nouveau `studio/source_video.py`, migration additive,
  routes d'import, consultation, correction et approbation. Tests de vrai MP4
  synthétique, coupure, mots chevauchant une coupe, ASR absent, fichier invalide,
  données d'une autre série et approbation périmée.
- [x] Adaptation : branche source dans `series_planner`, snapshot figé dans
  `series.create_episode`, validation du rythme et des locuteurs avant Director.
  Tests avec LLM simulé, aucune dépense, aucune coupe inventée ou ligne perdue.
- [x] Interface : nouveau composant d'import dans le formulaire d'épisode,
  revue des images/transcriptions/notes et accès à la source dans le storyboard.
  Tests du client, vérification Svelte et build.
- [x] Régression : suites Studio/export pertinentes, golden V4 avec médias
  locaux, essai local complet import -> storyboard -> préflight bloquant,
  sans toucher au projet 44. Rapport final avec limites réelles.

## Critères de revue

Refuser les horodatages invalides, les approbations obsolètes et les sources
d'une autre série. Une ASR indisponible exige une transcription manuelle ou une
confirmation explicite d'absence de paroles. Aucun média source ne devient une
première image de production ni une prise approuvée. Aucune validation visuelle
ou vocale ne peut être déduite de la seule transcription.

## Utilisation

1. Ouvrir une série, puis "Créer un nouvel épisode" et "Vidéo à refaire".
2. Importer le MP4 (1 à 120 s, 250 Mo maximum). Aucun téléchargement TikTok n'est intégré.
   La langue source proposée suit celle de la série ; corrigez-la si le MP4
   est dans une autre langue.
3. Relire les coupes, les paroles et les images extraites. Compléter les paroles
   si Whisper est indisponible ; confirmer chaque plan sans parole. Les plans de
   moins de 1 s sont refusés avant adaptation, car le moteur exige au moins 1 s.
4. Valider l'analyse, puis "Adapter et préparer le storyboard". Cette dernière
   action utilise le service texte configuré ; le développement ne l'appelle pas.
5. Relire et approuver le storyboard. Le paquet ChatGPT contient les références
   du canon et les images source pour le cadrage et les poses uniquement.
6. Générer les premières images manuellement dans ChatGPT, les importer et les
   valider. Le préflight et le budget restent obligatoires avant toute vidéo.
7. Réviser les prises dans le Studio. L'export source vérifie les fenêtres vidéo
   réelles, conserve le temps de jeu après une réplique courte et refuse une
   coupe qui tronque une parole transcrite. Les continuations techniques ne
   deviennent pas de nouvelles coupes autorisées.

La création attend toujours la finalisation de l'épisode précédent de la série,
afin de conserver son canon. Aucun épisode existant n'est réécrit par cet import.

## Vérifications et diagnostic

- Revue indépendante : correction du hash attendu lors de la création d'épisode,
  du respect de la langue cible et du refus préalable des plans inférieurs à 1 s.
- `test_studio_source_video.py` : import réel, données invalides, approbations,
  adaptation simulée, conservation des paroles, rythme, paquet et références,
  import d'images, préflight bloquant et export MP4 local. Aucun GPU ou LLM réel.
- Essai navigateur sur le port 8251, base isolée : import, restauration après
  redémarrage, validation, invalidation par édition et correction des coupes.
- Essai sur la V4 réelle : le seuil 0,32 omettait les changements à 21,750 s et
  42,583 s (scores 0,257 et 0,305). Le seuil 0,20 retrouve ces changements. Ce
  contrôle ne garantit pas la détection des fondus ou des raccords similaires.
- Trois tests hérités échouaient déjà sur `e8cfc38`, reproduction dans une
  archive isolée : le test de production attendait l'export de sept clips de
  0,6 s, désormais justement refusé ; deux tests d'incrustation échantillonnaient
  des pixels 720x1280 après le passage à 1080x1920. Le premier vérifie maintenant
  le refus puis la reprise ciblée ; les deux autres utilisent les mêmes zones
  normalisées. Les contrôles de production n'ont pas été affaiblis.
- Logs de vérification et capture d'interface dans `work/source-video-*`,
  volontairement exclus de Git car ils contiennent des chemins et médias locaux.
- Suite backend complète : 1646 passed, 2 skipped, 2 warnings en 779,28 s.
  Les deux avertissements concernent la fermeture de transports asyncio Windows
  dans `test_shell_tool.py::test_settings_roundtrip_for_new_keys`.
- Tests de source après les derniers ajustements du détecteur et du panneau de
  coupes : 18 passed. Source et exports : 61 passed. Intégration Director et
  contrats natifs : 147 passed lors du passage précédent.
- Frontend : `npm test` passe ; `npm run check` donne 0 erreur et 12 avertissements
  préexistants dans 6 anciens fichiers ; `npm run build` passe.
- Aucune clé n'est affichée ou enregistrée dans les nouveaux fichiers. Aucun
  appel texte, TTS, image générative ou GPU payant n'a été effectué.

## Préservation avant installation

Sauvegarde SQLite cohérente dans `work/before-video-remake-20261019-*.db`.
Avant installation : 21 projets, 9 séries, 15 épisodes, 231 prises, 34 exports,
aucun job pending/running. Le projet 44 est en pause au QC.

- Episode 1 publié : SHA-256
  `7ab17711fc0a4aa49842108c6f2bbfe5aa81b791077085f615371234760efda3`.
- Golden V4 : SHA-256
  `1465e4cecc8a72887e907c8f37e4c73e8c8ee7f8ef13c8e497c46d67979d7cef`,
  57469808 octets, 60,666 s, 1080x1920, audio 48 kHz stéréo.

## Installation vérifiée

Le Studio a redémarré le 19 octobre sur le port 8247 avec
`pipeline_revision=d072499b461b157e` et `restart_required=false`.
DeepSeek reste `deepseek-v4-pro`. L'épisode 44 reste `paused/qc`.
Les comptes de projets/séries/épisodes/prises/exports sont identiques à la
sauvegarde. Comparaison intégrale des lignes inchangées dans les sept tables
`studio_episodes`, `studio_shots`, `studio_director_shots`, `studio_takes`,
`studio_exports`, `studio_productions`, `studio_entities`. Les deux MP4 ont
strictement les mêmes SHA-256. Aucun job actif et aucune source de test ajoutée
à la base réelle. La migration ajoute uniquement la table des sources.

Le test navigateur a utilisé une base séparée sur le port 8251. Capture locale :
`work/source-video-interface.png`.

Pour relancer ultérieurement, après avoir fermé l'instance existante :

```powershell
Set-Location 'C:\Users\saill\Documents\Codex\2026-10-07\files-pasted-by-the-user-mission\outputs\studio-ia'
.\.venv\Scripts\python.exe .\run_studio.py --port 8247
```

Le Studio est déjà lancé ; il n'est pas nécessaire de le relancer maintenant.

## Limites de cette tranche

Le module ne reconnaît pas automatiquement expressions, identités, poses ou
mouvements caméra par vision. Il expose les images et les notes de revue ; DeepSeek
propose les informations manquantes comme choix d'adaptation. L'ASR seule ne valide
ni le locuteur réel, ni le timbre, ni le lip-sync. Les prises incertaines gardent
leur revue humaine obligatoire. L'écriture réelle n'a pas été évaluée sur une
source anglaise : aucun MP4 anglais n'a encore été fourni et aucun appel payant
n'a été autorisé pour ce développement. Cette tranche ne remplace pas la prochaine
intégration des huit prises V4 existantes dans un nouveau parcours source verrouillé.
