# Prompt d'audit externe de StudioAI

Tu interviens comme un auditeur indépendant, compétent en logiciels de génération audiovisuelle, prompting multimodal et écriture de fiction courte. Je veux un diagnostic honnête et un plan de simplification, avant toute modification.

## Version à auditer

Dépôt et branche à auditer : https://github.com/saillardsimon71-del/StudioIA/tree/codex/studioia-reliability

Audite explicitement `codex/studioia-reliability`, pas la branche par défaut. Elle contient les corrections locales et les nouveaux tests. Note le SHA exact au début de ton audit pour figer la version étudiée.

Les preuves et le contexte sont publiés dans `docs/studio/audit-2026-10-12/`. Commence par son `README.md`, les réglages publics et la vérification de publication. Les vidéos, la base locale et la configuration avec clés restent hors du dépôt ; précise ce que tu ne peux pas vérifier.

## Résultat recherché

StudioAI doit permettre : idée ou scénario -> écriture et mise en scène pertinentes -> prompts adaptés aux modèles -> images -> vidéos avec dialogues français intelligibles -> montage cohérent -> MP4. Le parcours ordinaire doit fonctionner sans scripts ponctuels écrits par un assistant, manipulations de base de données, imports manuels répétés ou clonage de série pour contourner les difficultés.

Nous créons notamment des fictions verticales avec des fruits très humanisés, des décors denses et profonds et des conflits humains immédiatement compréhensibles : jalousie, mensonge, ragots, vengeance, famille. Le début doit montrer une situation qui donne envie de connaître la réaction suivante. Les dialogues sont oraux, vifs, les gestes utiles et les conséquences concrètes. Ce sont des intentions artistiques à interpréter, pas un modèle de gag ou de caméra à imposer à toutes les histoires.

Chaque épisode comprend une incitation centrale rapide, dite par un personnage qui pointe le spectateur, sans téléphone géant ajouté : "Abonne-toi, copie le lien et commente GO si tu veux recevoir un guide pour faire des vidéos gratuitement." Retour immédiat à l'histoire, fin narrative sans outro promotionnelle. Les durées doivent respecter les possibilités réelles du moteur et l'intelligibilité.

## Problèmes observés

- Images et son peuvent être satisfaisants, mais la qualité des histoires, des hooks et du rythme reste irrégulière.
- Des paroles sont parfois incompréhensibles ou semblent attribuées au mauvais personnage. Une transcription proche du texte attendu ne certifie ni l'écoute ni la voix réelle.
- Des images initiales contredisent l'action demandée ; cette erreur se transmet ensuite aux vidéos.
- Des pauses et des transitions lentes affaiblissent les échanges et l'incitation.
- Le dernier lancement a demandé beaucoup trop d'interventions malgré un scénario déjà approuvé.

Exemple documenté : projet 27 "Mort de honte", puis version retravaillée, projet 28. Enzo devait être dans un chariot, Clara derrière la poignée, Jade debout. Une image a placé Jade dans le chariot et Enzo dans un fauteuil. Une autre a ajouté un personnage en double. L'assistant a finalement préparé séparément une image avec une demande simplifiée et la seule référence d'Enzo, puis une image d'incitation à partir de cette composition, et les a importées dans StudioAI. L'apparence féminine a aussi varié. Ce contournement a permis le lancement ; il ne démontre pas que le parcours normal est fiable ni que le nouveau film est réussi.

Preuves à consulter dans `docs/studio/audit-2026-10-12/` : `diagnostic-original.md`, `revision2-script.md`, `revision2-brief.json`, `project-27-snapshot.json`, `project-28-snapshot.json`, les demandes d'image, les images associées et les scripts de `manual-helpers/`. Les chemins locaux de ces helpers sont anonymisés : ce sont des pièces documentaires à lire, pas à exécuter. Distingue préparation, appel fournisseur, résultat généré, choix manuel et résultat exporté. Le projet 28 est terminé techniquement à la capture ; sa qualité humaine reste à vérifier.

## Audit demandé

1. Retrace la chaîne réelle : brief, contexte, rédaction, sélection/réécriture, découpage, états entre plans, références, compilation des prompts, appels fournisseurs, diagnostic sonore, coupes, mix et export. Repère chaque transformation qui modifie ou surcharge l'intention.
2. Lis en priorité `series_planner.py`, `h3_prompt.py`, `visual_identity.py`, `production.py`, `service.py`, `transcription.py`, `pacing.py`, `export.py`, les routeurs et les tests correspondants. Vérifie les réglages effectifs ; ne suppose pas qu'ils correspondent à la documentation du dépôt.
3. Compare les requêtes réellement envoyées aux documentations officielles H3/FastH3 et du service Agnes utilisé. Cherche les consignes contradictoires, les actions futures dans les images de départ, les références qui imposent une mauvaise composition, les duplications de contexte et les limites de continuation. Pour chaque explication, cherche un contre-exemple.
4. Sépare défaut d'écriture, défaut de prompt, orchestration inutile, bug logiciel et limite ou variabilité du modèle. Ne considère ni "le modèle fonctionne forcément" ni "il faut ajouter un contrôle" comme une conclusion de départ.
5. Détermine ce qu'on peut supprimer, raccourcir ou fusionner sans perdre la fidélité au scénario, la reprise des travaux et la conservation des prises. Évalue notamment l'utilité réelle des étapes IA successives, des états d'objets, des réparations et des validations.
6. Propose un parcours minimal et une validation audiovisuelle représentative : même petite scène jouée par la chaîne normale et par une demande directe simple aux mêmes modèles. Compare image initiale, locuteurs, intelligibilité, débit, raccords, appels IA, interventions, temps et coût observés. Les tests logiciels ne remplacent pas le visionnage et l'écoute.

## Contraintes et livrable

Audit en lecture seule : ne modifie rien, ne lance aucun rendu payant, ne publie rien et n'interromps aucune production. N'affiche aucune clé ou donnée d'accès. Octopus, publication, funnels et licences H3 sont hors périmètre. Pas de reconstruction complète, de nouvelle architecture d'agents ou d'empilement de garde-fous par défaut.

Livre :

- Un verdict clair sur la cause de la complexité et la fiabilité actuelle.
- Les cinq problèmes prioritaires, avec fichiers/lignes, preuve, impact et niveau de certitude. Indique quand un média ou une donnée manque.
- Pour chacun : ce qu'il faut garder, supprimer ou corriger, et comment vérifier l'amélioration.
- Un plan court de corrections ciblées, ordonnées par impact, effort et risque, avec un protocole d'essai réel avant une production complète.

Ne promets pas la viralité. Notre critère est une production compréhensible, intéressante, cohérente et facile à lancer, avec des limites clairement établies.
