# Verification avant publication de l'etat local

Cette publication sauvegarde le travail local et prepare sa reprise ailleurs. Elle ne valide pas la qualite de l'episode 2 V2, refuse par l'utilisateur. Aucun rendu, appel texte payant ou deploiement GPU n'a ete lance pour cette publication.

## Interface

Commandes dans `calliope-web` :

- `npm run check` : code 0, aucune erreur, 12 avertissements dans 6 fichiers.
- `npm test` : code 0, controles du stockage, valeurs, brouillons et client Director reussis.
- `npm run build` : code 0, compilation terminee.

## Backend

La suite complete a ete executee avec le Python local et un plugin de test externe bloquant les connexions reseau non locales. La configuration a ete redirigee vers un fichier de test, sans utiliser la configuration privee du Studio.

Premier passage : **1605 passes, 3 echecs, 2 ignores, 2 avertissements**, en 662,10 secondes. Les trois echecs ont ensuite ete reproduits separement :

1. `tests/test_llm_context_budget.py::test_test_session_never_writes_the_live_config` : le plugin externe utilisait `audit-test-config.json`, alors que ce test exige le nom `calliope_test_config.json`. Plugin corrige, toujours dans le dossier de test isole. Contre-test : **1 passe**. Aucun changement du code produit pour cet ecart.
2. `tests/test_studio.py::test_changed_image_endpoint_never_receives_new_credentials` : l'ancien test attendait l'erreur d'un changement de serveur d'images, mais le mode reel refuse maintenant toute generation d'image automatisee avant cette verification. Test adapte pour verifier le refus manuel en mode reel ET la protection des identifiants en simulation. Aucune connexion n'est creee. Contre-tests : **2 passes**.
3. `tests/test_studio_repair_values.py::test_unlocated_error_stops_without_arbitrary_whole_episode_repair` : **reste en echec**, reproduit seul. Le test simule un brouillon dont cinq scenes ont disparu et attend 2 appels texte au total ; le planificateur en effectue 4. `_validate_story_realization` classe maintenant l'ecart du nombre de repliques comme `_StoryRealizationError`, avec des chemins vers les listes de plans des scenes restantes. Cette exception herite de `_NativeValidationError` et autorise deux demandes `REPAIR VALUES ONLY`. Le mock retourne encore un episode au lieu des valeurs demandees, donc les deux reparations echouent. Verifier si la recuperation est reellement assez ciblee et justifiee, puis conserver une limite de cout et un arret explicite. Ne pas simplement modifier l'assertion pour masquer la question.

La suite complete n'a pas ete relancee apres l'ajustement du plugin et du test d'images. Les nombres ci-dessus se recouvrent et ne doivent pas etre additionnes. **La suite backend n'est pas entierement verte**. L'echec restant doit etre repris avant de presenter le Studio comme stabilise.

Journaux locaux, hors Git : `work/github-sync-backend-tests.txt`, `work/github-sync-web-check.txt`, `work/github-sync-web-test.txt`, `work/github-sync-web-build.txt`.

## Contenu publie

- Etat local des sources, tests, verrou de dependances et documents de travail.
- Instructions : `docs/studio/REPRISE-AUDIT-EPISODE-02.md`.
- Preuves textuelles : `docs/studio/episode-02-v2-evidence/`. Les 27 fichiers archives correspondent aux SHA-256 des originaux dans `provenance.json`. Les attributs Git preservent leurs octets. Les scripts sont archives en `.py.txt` pour etre lus, pas relances.
- `media-manifest.json` identifie les deux films par taille et SHA-256, sans contenir les videos.
- Aucun fichier video/audio, base locale ou configuration privee n'est ajoute. Le dossier local `publish/` est ignore sans etre supprime. Un controle des motifs de secrets n'a trouve aucun candidat dans les fichiers sortants ; cela ne constitue pas une certification de securite generale.

Lire ce document avant les anciens comptes rendus qui annoncaient un episode pret. La prochaine mission est un diagnostic prouve et des corrections generales, sans nouvelle depense.
