# Production 162, projet 15: réparation du planner

Le lancement de "The 1 - Épisode 1" a échoué pendant l'écriture. Le job 162 n'a soumis aucun rendu H3. Ses trois réponses ont successivement déclenché: scène vide, locuteur non identifié, puis "Le locuteur doit être visible dans son plan". La dernière erreur compare le speaker identifié à character_keys; elle pouvait aussi bloquer un plan sans parole qui conservait un ancien champ speaker.

Les JSON de ce lancement initial n'avaient pas été conservés. Le nom du personnage et le contenu exact du dernier plan sont donc inconnus. Le job historique reste conservé avec son erreur.

## Corrections

- Un personnage parlant déjà déclaré est ajouté aux références du plan lorsqu'il a été omis de character_keys. Les prompts image et vidéo demandent explicitement sa présence dans la composition. Les voix inconnues restent refusées.
- Sans dialogue, le champ speaker est vidé. Un plan de réaction garde son ambiance native.
- Un préfixe non ambigu comme `Teacher: "..."` peut identifier le locuteur d'un dialogue_turn avant validation; le texte final reste une réplique brute.
- Les lieux manquants et voix non attribuées sont signalés ensemble avec leurs champs et valeurs. Le modèle peut réparer tous ces défauts dans une réponse plutôt que découvrir une nouvelle erreur à chaque essai.
- Les instructions demandent de déclarer les lieux et de représenter les notifications sans personnage parlant par une action et un son, sans inventer de voix non attribuée.
- Le nom de notification `chime`, observé dans la réponse réelle, est normalisé vers l'effet audible existant `beep`. Les effets inconnus restent refusés.
- Les réparations de voix multiples demandent des dialogue_turns, sans imposer un nouveau plan.
- Chaque réponse JSON du planner est désormais conservée avec sa requête, son erreur de validation, son projet, son job et un horodatage UTC dans `data/assets/<project>/studio/diagnostics/planner-<job>-<run>-attempt-<n>.json`. Les paramètres de connexion et clés API ne sont pas inclus.

## Vérification réelle sans GPU

L'écriture a été exécutée sur le contexte et la demande réels du projet 15, sans générer d'image, de voix ou de vidéo. Les réponses ont confirmé des lieux non déclarés et des annonces REGISTRY sans personnage parlant. Après les messages de réparation précis, ces défauts ont été corrigés; une seconde réparation a ajusté la durée. La dernière réponse n'avait plus qu'un nom de SFX non pris en charge: `chime`.

Après normalisation de ce nom, la dernière réponse réelle et le parcours complet des trois réponses enregistrées passent dans le planner corrigé, sans nouvel appel au fournisseur. Le manifest contient 4 scènes, 21 plans, 59,5 secondes prévues, avec l'outro anglais. Cette durée est une prévision de scénario; aucun nouveau rendu ne prouve sa durée finale.

Ce manifest a été enregistré via la fonction normale de persistance dans le projet 15. Sa production reste en pause, avec aucun job de génération lancé. Une sauvegarde SQLite a été créée avant cette opération. Le projet 11 et son MP4 restent inchangés.

Les preuves sont dans le dossier de mission `outputs/planner-project15-visibility-check` pour les premiers essais, puis `outputs/planner-project15-visibility-fixed` pour les réponses réparées, le manifest et les résultats de rejeu.

## Tests et reprise

- 85 tests ciblés planner, production et contrat H3 réussis, dont 9 nouveaux cas de non-régression.
- Suite complète finale: 1040 passed, 2 skipped, 2 warnings, 0 failed en 543,67 secondes. Les deux warnings concernent le nettoyage asyncio/Proactor Windows.
- Ruff et la vérification des différences réussis.

Le backend a été redémarré avec le correctif. Recharger le projet 15, puis cliquer "Reprendre la production". Le scénario validé est déjà enregistré: cette reprise commencera la préparation des références puis les images et vidéos, sans recommencer l'écriture. Aucun épisode terminé n'a été régénéré pendant cette correction.
