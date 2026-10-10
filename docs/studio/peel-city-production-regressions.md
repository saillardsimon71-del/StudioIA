# PEEL CITY, projet 16 : répétition, interlocuteur absent et fausse progression

Constats du 10 octobre 2026, sur la branche `codex/studioia-reliability`.
Le run existant a été observé sans génération supplémentaire de média.

## Preuves du run

- Job principal 203, plans internes 123 et 124, prises vidéo 241 et 242.
- Le plan 123 attend une seule occurrence de "WESH KIKI !! T'AS VENDU LA GRAVITÉ POUR DES CHIPS ?".
- Le prompt compilé de la prise 241 contient une seule balise de dialogue.
- Son CallID est `fc-01M4JEYEES7AJ8AC9J6S04D885`.
- La prise brute dure 5,166667 s. Whisper transcrit deux occurrences successives,
  dans les intervalles 0-2,74 s et 2,74-5,16 s. Le dernier mot lexical de la
  première occurrence finit à 2,46 s.
- Le QC renvoyait `unknown`, avec une couverture de dialogue complète. La
  précision de 0,5 échappait au seuil de paroles supplémentaires. Les deux
  occurrences étaient transmises au calcul du trim, qui gardait le clip entier.
- Le plan 124 ne déclarait que `kiki`. Son image de départ et son prompt demandaient
  un gros plan sur Kiki. Aucun interlocuteur Benny n'était déclaré dans cette composition.
- La série exige une approbation visuelle des médias. La prise vidéo 241 n'avait
  pas cette approbation, alors que son image de départ l'avait.
- Le job 203 était déjà `done`, la production encore `running` et le job 214
  `pending`. La prise 242 était `running`, phase `QUEUED`, soumission `new`, sans CallID.
  Aucun rendu Modal n'avait donc été soumis pour le plan 124.

## Causes et corrections

Une répétition finale exacte et horodatée est récupérable au montage. Le QC
retient la première occurrence, persiste la raison et une borne avant la suivante.
Le trim respecte cette borne même si la durée narrative demandée est plus longue.
Dans le cas observé, la borne sur la grille 24 fps est 2,708333 s. Une répétition
prévue dans le scénario reste intacte. Sans repères temporels fiables, la répétition
reste un défaut de parole bloquant. Des paroles inventées ne sont pas éliminées
par cette règle. La prise brute et le transcript d'origine sont conservés.

La contrainte de locuteur unique n'existe plus, mais le schéma de génération
mettait encore en avant les champs singuliers `dialogue` et `speaker`, avec une
indication de 10 à 30 plans. Le planner utilise maintenant principalement les
tours de parole, sans objectif de nombre de plans, et demande une composition
commune pour un échange bref. Les champs singuliers restent compatibles à la lecture.
Les `listener_keys` explicites alimentent `character_keys` et les deux prompts,
donc aussi les références canoniques. Une référence ne doit pas perdre un
personnage simplement parce qu'il écoute.

La pause de validation du premier rendu libérait le sémaphore avant que
`gather` annule les autres tâches. La tâche suivante pouvait alors écrire un
nouveau checkpoint `running`, avant son annulation. Le pool interrompt maintenant
le lancement des tâches en attente dès la première pause ou erreur. Le traitement
final de la pause réaffirme également son état persistant.

## Qualification et limites

Les tests reproduisent le transcript français, la conservation du premier
intervalle dans le QC, la coupe avant répétition, les répétitions intentionnelles,
les repères manquants, les paroles inventées, le listener absent et la course de pause.
Un test réel du planner, limité au texte de l'échange Benny/Kiki, produit un plan
unique de 7 s avec les deux personnages et des tours de parole successifs.
Aucune image, vidéo ou requête Modal n'a été créée par ce test.

Cela qualifie le découpage et les corrections logicielles. La nouvelle composition
à deux n'a pas été rendue sur H3 pendant ce correctif. Le modèle peut toujours
produire une répétition ; la récupération dépend de sa transcription fiable.
Le correctif ne reconstruit pas les images ou le découpage déjà enregistrés.

Le projet 16 a été remis en pause après sauvegarde SQLite et contrôle que le
job principal était terminé et que la prise 242 n'avait aucune soumission Modal.
Ses médias n'ont pas été modifiés. Le redémarrage du backend est nécessaire
avant utilisation des nouveaux contrôles. Reprendre cet épisode conserve son
ancien découpage ; obtenir la conversation commune nécessite une nouvelle
préparation du découpage et de sa référence visuelle.

## Vérification finale

- Tests ciblés de qualification et de régression : 34 réussis.
- Suite complète avec `python -m pytest --asyncio-mode=auto -q` : 1126 réussis,
  2 ignorés, 2 warnings de nettoyage asyncio sous Windows, aucune erreur.
- Vérification sur le transcript existant de la prise 241 : première occurrence
  conservée, coupe calculée à 2,708333 s, aucun média modifié.
- Le SHA-256 de la vidéo brute reste
  `8a147c2390ef56dad3ea51419c9f9b1e33f2cf3a1177c48cb2fdc7891b493952`.

Les captures d'état, la sauvegarde SQLite, le contrôle du transcript et la sortie
réelle du planner se trouvent dans le dossier de travail de cette mission,
`outputs/peel-city-16`. Les journaux de tests sont dans
`work/peel-city-regression-tests`.

Après redémarrage du serveur, vérifier le plan 1 dans "Identité et ancrages de
la vidéo choisie", approuver son identité, puis reprendre la production si
l'ancien découpage convient. Aucun clic de reprise n'a été effectué pendant
ce correctif.
