# 2026-10-22 — Service Meta AI isolé, provenance vérifiée et fiabilité de production

Base : `fix/director-first-pass` (`e1dc983`). Branche : `arena/b864fd01-studioia`.

## Images Meta AI

- Nouveau service local `studio-v2/metaai-service/` (FastAPI + Playwright),
  installation et environnement séparés, port 8000.
- Une conversation Meta AI **neuve par image**, vérifiée sur le DOM (0 message
  utilisateur, 0 réponse assistant, composeur prêt). Toute page non vierge ou
  route de réglages est refusée.
- Seuls les médias du nœud de réponse de l'assistant courant sont candidats ;
  avatar, barre latérale et anciennes générations sont exclus structurellement.
- Provenance retournée et revalidée côté Studio IA : `request_id`, prompt exact,
  `conversation_id`, attribution, confirmation du message envoyé.
- Contrainte SQL `UNIQUE(conversation_id)` dans `metaai_generations` : une
  conversation ne peut produire qu'un seul asset, même après redémarrage.
- Bibliothèque : colonnes `provenance_json` et `source` (migration additive,
  visuels existants marqués `legacy`), taille minimale d'image, dédoublonnage
  limité au même octet **et** à la même destination.
- Nouveaux endpoints : `GET /api/assets/audit`, `DELETE /api/assets/{id}`
  (détache les références, conserve les fichiers), `POST
  /api/projects/{id}/generate-images` (lot séquentiel, reprise ciblée, arrêt sur
  service indisponible).
- Suppression de la stratégie de patch de `site-packages` (PR #5) : aucun script
  de remplacement de `metaai-sdk`, aucune dépendance d'exécution au SDK.
- Nouveaux réglages : `metaai_image_hosts`, `metaai_min_image_side`.

## Director et continuité

- Contrat narratif ajouté au prompt système : causalité, hook, rétention,
  continuité physique, payoff final, respect du brief, séries, rythme.
- Contrôles locaux non bloquants (`continuity_warnings`) : rupture d'état entre
  plans continus, action dupliquée, brief « sans paroles » contredit par des
  répliques. Toujours un seul appel DeepSeek.

## Montage et export

- Refus de deux plans partageant le même fichier vidéo (doublon silencieux).
- Refus d'un export entièrement noir et d'un export nettement tronqué.
- Avertissement ciblé pour une image noire isolée.

## Outils

- `studio-v2/scripts/update-studio-ia.ps1` : mise à jour fast-forward avec
  sauvegarde commit/config/base, `-Rollback` réversible, distinction explicite
  entre Studio IA et le service Meta AI.
- `metaai-service/verify-three-generations.ps1` : cube rouge, sphère verte,
  pyramide bleue, preuves JSON, contrôle de couleur, sans clic humain.
- `studio-v2/scripts/verify-metaai-studio.ps1` : même validation à travers
  Studio IA et les associations aux plans.
- Documentation : `docs/studio/STUDIO-IA-RELEASE.md`,
  `docs/studio/META-AI-SERVICE.md`, `studio-v2/metaai-service/README.md`.
