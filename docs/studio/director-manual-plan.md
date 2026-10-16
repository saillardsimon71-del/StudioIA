# Studio Director manuel - plan d'implementation

Objectif: idee -> storyboard relu -> paquet ChatGPT -> import et validation des images -> preflight local -> videos -> QC humain -> reprise ciblee.

Architecture: conserver SQLite, la bible/canon, le planificateur natif, les moteurs H3/LTX, les prises et le CTA. Ajouter un contrat Director strict par plan et un etat de revue dans des tables additives. Aucun fournisseur d'images n'est appele dans ce parcours. Les projets anciens restent accessibles, avec activation explicite du nouveau parcours.

Contraintes: pas de GPU ou d'images pendant les tests; pas de cle dans prompts, journaux ou Git; pas de suppression de donnees; aucun changement Octopus. La branche de depart est codex/series-quick-image-import, ee71803, propre. PR #1 ouverte, non fusionnee. Autre worktree laisse intact.

- [x] Contrat et memoire: models stricts, contraintes de casting, lieux, temps, repliques, etats/raccords; specialisation des prompts DeepSeek; revision limitee aux IDs demandes, controle de concurrence.
- [x] Revue/images: approbation liee au contenu, export ZIP avec manifest/prompts/references, import atomique par noms stables, conservation des anciennes prises, validation humaine liee au hash image/contrat.
- [x] Production: preflight sans reseau/GPU, budget explicite estime par seconde video et plafonds, blocage des autres routes de generation d'images pour ce parcours, reservation des couts par prise, reprise du seul sous-ensemble dependant.
- [x] QC: aucune auto-approbation dans ce parcours; parole incorrecte bloquante, ASR absent = non verifie; identite du locuteur et vision = a verifier; revue humaine explicite, prises rejetees conservees.
- [x] Interface: idee et objectif simples; storyboard/revision/paquet/import/validation/preflight/QC visibles; options techniques en panneau expert facultatif; configuration locale existante conservee.
- [x] Verification: tests backend et front, compilation, parcours navigateur sur base temporaire sans services externes; revue finale et corrections; commits locaux coherents et commandes Windows.

Verification finale: 751 tests backend passes, 1 test de liens symboliques ignore sous Windows; tests frontend passes; controle Svelte 0 erreur, 12 avertissements preexistants; build passe. Parcours navigateur verifie sur une base distincte, sans cle et avec la file de production suspendue. Aucune generation payante.

Cas de revue: paquet obsolete; revision concurrente; fichiers inconnus/doublons/lot partiellement invalide; image modifiee apres validation; tentative GPU par endpoint secondaire; budget depasse par nouvelles prises; QC indisponible ou mauvais; raccord affectant seulement la chaine dependante. Les prix sont des estimations saisies par l'operateur, jamais une garantie de facturation fournisseur.
