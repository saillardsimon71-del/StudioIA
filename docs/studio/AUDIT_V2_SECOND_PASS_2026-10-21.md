# Studio IA V2 - Second passage du 21 octobre 2026

Base relue : `a32b623`, branche `test-director-v2`. Aucun agent, service ou dependance ajoute. Aucun appel externe de generation ni redeploiement.

## Defauts reproduits et corriges

| Defaut | Correction |
| --- | --- |
| Un preflight pouvait reecrire un ancien instantane et effacer un resultat ou une modification enregistree pendant son controle local. | Preflight en lecture seule. Il retourne les prompts recompiles ; seuls la generation du scenario et le rendu persistent leurs resultats. |
| Apres interruption, une prise pouvait etre affectee a un plan dont la graine ou la duree avait change avant la reprise. | Conservation du plan effectivement soumis dans le travail persistant, controle des entrees d'origine avant association. La prise obsolete reste dans l'historique, sans etre affectee au plan modifie. |
| Une panne disque apres reception du resultat, ou une erreur SQLite en sauvegardant l'identifiant Modal, pouvait liberer une nouvelle generation payante. | Ces erreurs conservent la prise en attente. Une reprise ne lance aucun nouveau GPU. Si l'identifiant n'a pas pu etre persiste, il figure dans l'erreur et la reconciliation reste necessaire. |
| Une reprise reutilisait aveuglement un MP4 local deja declare termine, meme disparu ou corrompu. | Recontrole du fichier et recuperation depuis le meme appel si necessaire, sans dupliquer l'historique des prises. |
| Un plan/projet retire pendant le rendu pouvait laisser le travail en attente et bloquer les autres prises. L'expiration distante d'un resultat deja enregistre provoquait aussi une collision de cle SQLite. | Resultat conserve et travail acquitte si sa cible n'existe plus, y compris apres redemarrage. Une expiration terminale libere le travail en erreur, sans re-inserer la meme prise. |
| Un MP4 de 193 images a 12 fps pouvait etre accepte pour une demande de 193 images a 24 fps. | Verification de la cadence en plus des dimensions, frames et audio, pour les fichiers recus comme pour ceux remis en cache. |
| Un identifiant court comme `cam` correspondait au mot `camera` et ajoutait un personnage absent au prompt. | Reconnaissance des identifiants et noms aux frontieres des mots. |

## Verification

- Les nouveaux tests ont d'abord reproduit les defauts, puis passe apres correction.
- **128 tests Python reussis**, dont **14 nouvelles regressions** et le parcours complet local du premier audit.
- **16 tests frontend reussis**. Aucun changement frontend ni recompilation du bundle necessaire.
- Serveur teste sur base isolee : sources chargees identifiees, 12 assets compiles servis, seconde instance refusee. Serveur de test arrete.
- Cas Lucas/Emma : prompt de 166 mots toujours transmis, payload de 193 frames a 24 fps. Aucun ajustement artistique du projet.
- Un avertissement de deprecation Starlette/httpx, aucun echec de test.

Les 9 projets, 62 assets, 42 prises, 5 exports et 12 appels LLM d'origine sont inchanges. Les empreintes de `COSTS.md`, `config.json` et `modal_download_ltx.py.bak` correspondent a celles relevees avant l'audit.

## Verdict et limites

Pret pour un essai reel controle apres autorisation. Aucune vraie video LTX n'a ete generee ou validee pendant ce passage. Les limites du premier bilan restent applicables : synchronisation et jeu probabilistes, references Meta AI textuelles, lot pilote par l'onglet, reconciliation necessaire si l'envoi Modal est incertain et resultat irrecoverable si copie locale et sortie distante ont toutes deux disparu.

Les changements concernent le serveur local et le compilateur ; redemarrer Studio IA V2 pour les charger. Le correctif voix off du worker provenant du premier audit reste a redeployer, sans generation payante pendant cette mission.
