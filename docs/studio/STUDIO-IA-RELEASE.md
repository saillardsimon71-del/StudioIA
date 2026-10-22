# Studio IA V2 — note de livraison

**Date** : 2026-10-22
**Branche** : `arena/b864fd01-studioia` (PR ouverte vers `fix/director-first-pass`, commit de base `e1dc983`)
**Périmètre** : fiabilisation des images Meta AI, provenance, continuité narrative, montage/export, outillage de validation et retour arrière.

Ce document distingue strictement :

1. ce qui a été **exécuté et vérifié** dans l'environnement de développement ;
2. ce qui **reste à valider sur Windows** avec un compte Meta et un GPU Modal ;
3. les **limites connues**.

Aucune génération réelle Meta AI, aucun rendu GPU payant et aucun appel DeepSeek
payant n'ont été exécutés. Rien dans ce document ne prétend le contraire.

---

## 1. Résultat

| Priorité | Élément | État livré |
|---|---|---|
| P0 | Pipeline image fiable | Nouveau service isolé, une conversation neuve par image, preuves vérifiables des deux côtés. Non exécuté contre Meta réel ici. |
| P0 | Protection de la bibliothèque | Provenance en base, contrainte SQL d'unicité de conversation, audit, retrait sans suppression de fichier, refus des avatars/vignettes/fichiers corrompus. Testé. |
| P0 | Continuité des références | Comportement existant conservé ; contrôles locaux de rupture d'état ajoutés. Testé. |
| P0 | Non-régression Director | Un seul appel DeepSeek conservé ; 194 tests Studio passent. Testé. |
| P0 | Vidéos et exports exploitables | Refus des doublons de prise, des exports noirs et tronqués ; validation locale d'un montage 60 s réussi. Testé. |
| P1 | Intelligence narrative | Contrat narratif ajouté au prompt (causalité, hook, rétention, payoff, brief, séries, rythme). Aucun second appel. |
| P1 | Continuité physique | Avertissements objectifs sur les remises à zéro d'état entre plans continus. Testé. |
| P1 | Montage et son | Ordre, durée, audio 48 kHz stéréo et incrustation vérifiés sur un film 60 s synthétique. Testé. |
| P1 | Reprise autonome | Lot d'images reprenable, arrêt sur indisponibilité, reprise du rendu Modal existante conservée. Testé (images) ; rendu Modal non exécuté. |
| P2 | Simplicité | Messages d'erreur explicites, audit de bibliothèque, mise à jour scriptée réversible. |
| P2 | Vidéo à refaire | **Reporté** : absent de V2 (il existe dans l'ancienne couche `calliope-backend`/V1, non modifiée). Voir §7. |

---

## 2. Ce qui a été réellement exécuté et vérifié

Environnement : Linux, Python 3.11, Node 22, FFmpeg fourni par `imageio-ffmpeg`.
Aucun navigateur Meta, aucun GPU.

### 2.1 Tests Python Studio IA V2

```bash
cd studio-v2
.venv/bin/python -m pytest tests -q
# 194 passed
```

### 2.2 Tests Python du service Meta AI

```bash
cd studio-v2
.venv/bin/python -m pytest metaai-service/tests -q
# 23 passed
```

Ces tests couvrent : conversation neuve, refus d'une page non vierge, refus d'une
route de paramètres, exclusion des avatars et anciennes images, conversation
réutilisée, refus explicite de Meta, absence d'image, prompt non relu, écho du
prompt, matrice des statuts HTTP, clé API, état persistant.

### 2.3 Frontend

```bash
# copie propre de studio-v2/web (le bundle livré n'a pas été écrasé)
npm ci
npm run check   # 0 erreur, 0 avertissement
npm run build   # succès (adapter-static)
npm test        # 16 passed (node --test tests/*.test.mjs)
```

### 2.4 Export réel de 60 secondes, sans GPU

```bash
cd studio-v2
.venv/bin/python scripts/verify_export_local.py
```

Résultat obtenu : **VALIDATION RÉUSSIE**. Six prises synthétiques distinctes,
export 1080×1920, 24 fps, AAC 48 kHz stéréo, durée 60 s, ordre des plans vérifié
par échantillonnage de couleur au centre de chaque segment, incrustation
`000 → 001` sur le troisième plan, rapport QC sans erreur. Durée d'exécution :
environ 26 s. Aucun fichier existant n'est touché (dossier temporaire).

### 2.5 Scénarios de non-régression conservés

- Un seul appel DeepSeek par génération, y compris sur réponse invalide.
- Les images existantes ne sont pas écrasées ; un refus individuel reste relançable.
- Les reprises Modal en attente et les prises conservées gardent leur comportement.
- Découpage, overlays, compteurs et QC OCR/Whisper inchangés (non exécutés avec
  modèles réels : Whisper est désactivé dans les tests locaux).

---

## 3. Ce qui reste à valider sur Windows (tests A, B, C)

Ces trois tests nécessitent votre machine, vos cookies Meta et, pour le GPU,
votre autorisation explicite. **Ne les considérez pas comme validés avant de les
avoir exécutés.**

### Test A — Meta AI : trois images automatiques

```powershell
cd studio-v2\metaai-service
.\install.ps1
# .env : META_AI_DATR, META_AI_ECTO_1_SESS, META_AI_API_KEY
.\start.ps1
# seconde fenêtre :
.\verify-three-generations.ps1
```

Attendu : `validation.json` avec `ok: true`, trois conversations distinctes, trois
images distinctes, couleurs rouge/vert/bleu, aucune image d'avatar. En cas
d'échec du DOM : `.\start.ps1 -Diagnose` puis transmettre `data\diagnose-*\state.json`.

Puis, côté Studio IA (service toujours actif, Studio sur 8270) :

```powershell
cd studio-v2
.\scripts\verify-metaai-studio.ps1
```

Attendu : un projet `TEST — Meta AI 3 générations`, trois plans associés à trois
assets `source=metaai`, `verified=true`, conversations distinctes.

### Test B — Production vidéo d'un plan

1. Ouvrir un projet existant **sans le modifier**, choisir un plan dont l'image
   est validée.
2. `Préparer le rendu` → vérifier le prompt et l'estimation.
3. Cocher l'autorisation de dépense, lancer **une seule** prise.
4. Contrôler : fichier présent, dimensions 768×1344 (LTX), durée affichée, audio
   présent, prise enregistrée dans l'historique, coût réel inscrit dans `COSTS.md`.

Aucun rendu existant n'est supprimé : une nouvelle prise est ajoutée à l'historique.

### Test C — Film vertical complet d'environ 60 secondes

Option 1, sans GPU (déjà validée techniquement ici, à refaire sur votre machine) :

```powershell
cd studio-v2
.\.venv\Scripts\python.exe scripts\verify_export_local.py
```

Option 2, production réelle : storyboard validé → images → 6 à 8 prises GPU
autorisées → export. Vérifier : durée finale, ordre des plans, absence d'écran
noir, compteur synchronisé, dialogues corrects, MP4 lisible sur téléphone.

---

## 4. Limites connues

- **Meta AI reste non officiel.** L'interface peut changer sans préavis ; le
  service échoue alors fermé. Les cookies expirent. Les conditions d'utilisation
  de Meta s'appliquent.
- **Pas de référence pixel vers Meta AI.** Les portraits et décors ne sont pas
  envoyés comme images de référence ; la continuité repose sur les descriptions
  et sur les images initiales. Elle reste approximative et ne peut pas être
  garantie au pixel par du texte.
- **Le service Meta AI ne fait pas de vidéo.** Meta AI ne génère pas de vidéo.
- **LTX-2.5** : plans de 5 à 15 s, pas de coupes internes garanties, continuité
  par dernière image expérimentale. Les dialogues sont portés par l'audio natif
  du modèle, sans synchronisation labiale garantie.
- **QC** : OCR et Whisper restent des avertissements soumis à revue humaine ;
  ce ne sont pas des jugements artistiques. Aucune note chiffrée n'est attribuée.
- **Scripts PowerShell** : non exécutés dans l'environnement de développement
  (pas de Windows). Ils vérifient les prérequis et échouent avec un message clair.
- **`metaai-sdk` n'est plus utilisé à l'exécution.** Voir
  `docs/studio/META-AI-SERVICE.md` §2–3 pour la justification et les défauts
  constatés dans la version 5.3.0.

---

## 5. Déploiement exact

### 5.1 Studio IA V2 (port 8270)

```powershell
cd <dépôt>\studio-v2
# si première installation :
.\setup.bat
# sinon, mise à jour conservatrice et réversible :
.\scripts\update-studio-ia.ps1
.\start.bat
```

Le script de mise à jour :

- refuse de s'exécuter si le serveur écoute encore sur 8270 ou si des modifications
  locales non commitées existent ;
- sauvegarde le commit courant, `config.json` et la base `data\v2.db` (+ WAL) dans
  `backups\studio-v2-<horodatage>\` ;
- avance en **fast-forward uniquement** (aucun historique réécrit) ;
- réinstalle les dépendances et ne recompile l'interface que si nécessaire
  (`-RebuildUi` pour forcer) ;
- **ne touche jamais** `studio-v2\metaai-service`.

Options utiles : `-SourceRef origin/fix/director-first-pass`, `-SkipDependencies`,
`-RebuildUi`.

### 5.2 Service Meta AI (port 8000, séparé)

```powershell
cd <dépôt>\studio-v2\metaai-service
.\install.ps1
.\start.ps1
```

Puis dans Studio IA : **Paramètres → URL du service metaai-api** =
`http://127.0.0.1:8000`, clé identique à `META_AI_API_KEY`. Réglages optionnels :
`metaai_image_hosts` (CDN autorisés) et `metaai_min_image_side` (taille minimale,
256 px par défaut).

### 5.3 Base de données

Migration **additive et automatique** au démarrage :

- `library_assets.provenance_json`, `library_assets.source` (les visuels existants
  sont marqués `legacy` : utilisables, mais jamais présentés comme vérifiés) ;
- table `metaai_generations` (`request_id` unique, `conversation_id` unique).

Aucune ligne existante n'est supprimée, aucun fichier média n'est déplacé.
Sauvegardez malgré tout `studio-v2\data\` avant une mise à jour.

---

## 6. Retour arrière

### Studio IA

```powershell
cd <dépôt>\studio-v2
.\scripts\update-studio-ia.ps1 -Rollback
# ou vers une sauvegarde précise, avec restauration de la base :
.\scripts\update-studio-ia.ps1 -Rollback -BackupPath "backups\studio-v2-20261022-101500" -RestoreDatabase
```

Le retour arrière crée lui-même une sauvegarde de l'état courant avant d'agir :
il reste réversible.

### Service Meta AI

1. `Ctrl+C` dans la fenêtre du service.
2. Studio IA → Paramètres → vider l'URL du service (les imports manuels restent
   disponibles) ou renseigner l'ancien service.
3. Renommer/supprimer `studio-v2\metaai-service` : aucun autre composant ne
   l'importe. L'ancien `MetaAIServiceV3` et `metaai-sdk` n'ont pas été modifiés.

Aucune production, aucun cookie et aucun asset existant n'est modifié par ces
procédures.

---

## 7. Décisions et arbitrages

- **Pas de patch V5 du SDK.** Les correctifs successifs (V1–V4, PR #5) réécrivaient
  cinq modules de `site-packages` (script de 853 lignes, sauvegarde/restauration).
  La cause racine est que le SDK ne rattache pas structurellement une image à la
  requête. Le service isolé supprime cette dépendance fragile ; détail et tableau
  des défauts dans `docs/studio/META-AI-SERVICE.md`.
- **Un seul appel DeepSeek conservé.** Le contrat narratif est ajouté au prompt
  existant ; les contrôles de continuité sont locaux et non bloquants. Aucun agent
  critique, aucune boucle de réécriture.
- **Contrôles utiles uniquement.** Les ajouts empêchent des erreurs coûteuses
  (mauvais plan, avatar en bibliothèque, doublon de prise, export noir/tronqué,
  remise à zéro d'état). Aucun système de notation, aucun score « 10/10 ».
- **Aucune automatisation de publication sociale.** Hors périmètre demandé.
- **Vidéo à refaire reportée.** L'ancienne implémentation vit dans
  `calliope-backend`/V1 (`source_video`, `series_planner`), qu'il est interdit de
  modifier ici. Porter cette fonctionnalité dans V2 suppose une couche
  séries/import/adaptation qui n'existe pas encore ; l'ajouter aurait mis en
  risque les priorités P0. La documentation existante
  (`docs/studio/VIDEO-REMAKE-2026-10-19.md`) reste la référence, et la fonction
  est explicitement reportée après validation des tests A/B/C.

---

## 8. Fichiers principaux

Nouveaux :

- `studio-v2/metaai-service/` — service `studioia_metaai` (browser, dom, service,
  server, diagnose), `install.ps1`, `start.ps1`, `verify-three-generations.ps1`,
  tests hors ligne.
- `studio-v2/core/metaai.py` — client et validation de provenance.
- `studio-v2/scripts/verify-metaai-studio.ps1`, `verify_export_local.py`,
  `update-studio-ia.ps1`.
- `studio-v2/tests/test_metaai_provenance_and_library.py`,
  `test_export_integrity.py`, `test_director_continuity.py`.
- `docs/studio/META-AI-SERVICE.md`, `docs/studio/STUDIO-IA-RELEASE.md`,
  changelog du 2026-10-22.

Modifiés :

- `studio-v2/api/app.py` — génération d'images via `core/metaai`, lot de visuels
  manquants, audit/retrait d'assets, bloc Meta AI dans `/api/health`.
- `studio-v2/core/storage.py` — migration de provenance, `add_verified_asset`,
  audit, détachement, `metaai_generations`.
- `studio-v2/core/config.py`, `config.example.json` — hôtes CDN et taille minimale.
- `studio-v2/core/storyboard.py`, `llm/deepseek.py` — continuité et contrat narratif.
- `studio-v2/core/export.py`, `qc/media.py`, `qc/runner.py` — doublons, export noir,
  export tronqué.
- `studio-v2/web/package.json` — script `npm test`.
- `studio-v2/README.md`, `.gitignore`.

Non modifiés : V1 (`calliope-backend`, `calliope-web`), `docs/studio` existants,
cookies, assets et rendus présents.
