# Service Meta AI de Studio IA V2 — analyse, stratégie et limites

Document de référence pour la génération d'images automatiques. Il explique
pourquoi l'intégration précédente échouait, ce qui a été reconstruit et ce qui
reste à valider sur une machine Windows réelle avec un compte Meta connecté.

Date : 2026-10-22 — branche `arena/b864fd01-studioia`, base `fix/director-first-pass` (`e1dc983`).

---

## 1. Critère de validation retenu

Trois générations consécutives, **sans clic humain**, avec un prompt différent
par image (cube rouge, sphère verte, pyramide bleue), chacune dans une
conversation Meta AI distincte, et une provenance vérifiable de bout en bout :
requête envoyée → message confirmé → réponse du tour courant → fichier image
enregistré → association au bon plan.

Le service fournit les preuves ; Studio IA les revalide et refuse tout ce qui ne
concorde pas. Aucun des deux côtés ne « fait confiance » à l'autre.

---

## 2. Ce qui existait

### 2.1 Service d'origine

`metaai-api` (github.com/mir-ashiq/metaai-api), exposé via `metaai-sdk`
(dernière version publiée : **5.3.0**, dépendances `requests`, `websockets`,
extras `playwright`, `api`). Le serveur FastAPI d'origine expose `/image` avec un
modèle `ImageRequest(prompt, new_conversation, media_ids, orientation, num_images)`
et appelle `MetaAI.generate_image_new(...)`.

### 2.2 Défauts constatés dans le code upstream 5.3.0

| Emplacement | Comportement | Conséquence observée |
|---|---|---|
| `api_server.py` → `generate_image` | `new_conversation` est accepté mais **jamais transmis** | prompts envoyés dans la conversation existante |
| `main.py` → `generate_image_new` | appelle `generation_api.generate_image(prompt, timeout)` sans ouvrir de chat | deuxième appel refusé (identifiant de conversation déjà consommé), images mélangées |
| `playwright_backend.py` → `send_message` | `_switch_mode` clique le premier bouton contenant « Instant »/« Thinking » | clics sur des contrôles non prévus, navigation accidentelle |
| `playwright_backend.py` → `_dismiss_overlays` | clique des boutons par texte : « Connect », « Accept », « Close »… | ouverture de **Paramètres → Applications** ou d'un écran de connexion |
| `playwright_backend.py` → `_wait_for_response` | collecte **toute** URL `fbcdn` nouvelle de la page, hors du nœud assistant | avatar de profil, vignette de la barre latérale, ancienne génération enregistrés comme image |
| `playwright_backend.py` → `new_chat` | cherche `a:has-text("New chat")`, échoue silencieusement | conversation réutilisée sans erreur |
| `playwright_backend.py` → `_get_conversation_id` | dépend uniquement de l'URL `/prompt/<uuid>` | identifiant vide non détecté |

Le point commun : la provenance n'est **pas structurelle**. Aucune étape du SDK
ne garantit qu'une URL d'image appartient à la réponse générée par la requête
courante.

### 2.3 Correctifs V1 → V4 (PR #5, branche `arena/55be887f-studioia`)

La PR #5 contient un patch de **853 lignes** (`scripts/patch_metaai_sdk_5_3.py`)
qui réécrit cinq modules de `site-packages` (`browser.py`,
`playwright_backend.py`, `generation.py`, `main.py`, `api_server.py`) en
remplaçant des fonctions par du code source injecté, avec marqueurs de version
`STUDIOIA_ISOLATED_GENERATION_V2/V3/V4`, sauvegarde `.studioia-backup`, script de
restauration et tests du patcheur.

Ce choix corrige partiellement les symptômes, mais :

- il dépend du contenu exact des fichiers d'un paquet tiers (toute réinstallation
  ou mise à jour casse le patch) ;
- la restauration remplace des modules entiers, avec un risque réel sur
  l'environnement de l'utilisateur ;
- la garantie de provenance repose sur des clics de libellés localisés et sur
  l'URL, pas sur la structure du DOM ;
- les scripts `patch_metaai_sdk_5_3.py` / `.ps1` deviennent eux-mêmes un composant
  à maintenir et à tester (442 lignes de tests dédiés).

C'est exactement le type d'accumulation que la mission demande d'arrêter.

---

## 3. Stratégie retenue

### 3.1 Principe

Un **service isolé et minimal** (`studio-v2/metaai-service/`) qui pilote Chromium
avec Playwright, sans modifier aucun paquet. Il ne dépend plus de `metaai-sdk` à
l'exécution. Trois idées suffisent à rendre la provenance démontrable :

1. **Conversation neuve par requête** : navigation vers l'accueil Meta AI, puis
   vérification que la page contient 0 message utilisateur et 0 réponse assistant.
   Aucune réutilisation d'identifiant possible ; une page non vierge est refusée.
2. **Périmètre structurel** : seuls les médias contenus dans le nœud de réponse de
   l'assistant sont candidats. Les URL déjà présentes avant l'envoi (avatar, barre
   latérale, historique) sont exclues, et seuls les CDN Meta autorisés passent.
3. **Mémoire persistante** : conversations et requêtes déjà consommées sont
   enregistrées dans `data/state.json` (service) et dans la table
   `metaai_generations` (Studio IA, contrainte SQL `UNIQUE(conversation_id)`).

### 3.2 Pourquoi ne pas utiliser le SDK « nativement »

Le SDK a été examiné en priorité. Sa couche navigateur est précisément la partie
défaillante (§2.2) : `generate_image_new` ne peut pas recevoir d'ordre d'ouverture
de conversation, et la collecte d'images est globale à la page. Utiliser le SDK
tel quel ne permet donc pas de satisfaire le critère de validation, et le corriger
impose de réécrire ses méthodes — ce que faisait la PR #5. La seule alternative
honnête est de ne pas dépendre de cette couche.

Le contrat DOM utilisé (composeur, nœuds `assistant-message` / `user-message`,
URL `/prompt/<uuid>`) est celui que le SDK et la PR #5 ciblaient déjà. Il est
isolé dans un seul module (`dom.py` + `browser.py`) et un outil de diagnostic
permet de constater une rupture d'interface en quelques secondes.

### 3.3 Ce qui a été supprimé

- Aucun script de patch de `site-packages`, aucune sauvegarde `.studioia-backup`,
  aucun marqueur de version de patch.
- Aucune dépendance d'exécution à `metaai-sdk` ni à `agent-browser`.
- Aucun clic sur un contrôle non identifié ; aucun clic « Connect ».
- Aucune relance automatique d'une génération échouée.

---

## 4. Chaîne de vérification

```
Studio IA (core/metaai.py)
  │  1. génère un request_id unique
  │  2. POST /image {prompt, request_id, new_conversation:true}
  ▼
Service Meta AI
  │  3. accueil vierge vérifié (0 message, composeur prêt, pas /settings)
  │  4. saisie relue à l'identique avant Envoi
  │  5. attente d'UNE réponse assistant ; médias du nœud courant uniquement
  │  6. identifiant de conversation neuf, absent de state.json
  │  7. retourne image_urls + prompt + request_id + conversation_id + attribution
  ▼
Studio IA
  │  8. request_id et prompt strictement identiques à l'envoi
  │  9. attribution == fresh_assistant_response et prompt_echo_verified == true
  │ 10. hôtes des médias dans metaai_image_hosts (fbcdn.net, metaaiusercontent.com)
  │ 11. téléchargement : ≤ 25 Mo, image décodable, côté ≥ metaai_min_image_side
  │ 12. insertion atomique asset + preuve ; conversation unique en base
  ▼
Bibliothèque Studio IA (asset source=metaai, verified=true, provenance JSON)
```

Toute étape qui échoue produit une erreur explicite et **aucune écriture** dans
la bibliothèque. Le fichier temporaire est supprimé si l'insertion échoue.

---

## 5. Exploitation

### 5.1 Endpoints

- `GET /healthz` — état sans secret (version, navigateur prêt, cookies présents,
  conversations connues, dernière erreur).
- `POST /image` — génération vérifiée (voir `metaai-service/README.md`).
- `POST /reset` — ferme Chromium, conserve cookies et état.

### 5.2 Diagnostic en cas de changement d'interface

```powershell
cd studio-v2\metaai-service
.\start.ps1 -Diagnose
```

Écrit `data\diagnose-<horodatage>\{state.json,page.html,home.png}`. Le fichier
`state.json` indique si le composeur est trouvé, si la conversation est vierge,
les boutons visibles et les médias présents. C'est la première chose à joindre à
un rapport d'incident.

### 5.3 Test des trois générations

```powershell
.\verify-three-generations.ps1        # service seul, preuves + couleurs
cd ..\..\studio-v2
.\scripts\verify-metaai-studio.ps1    # via Studio IA, associations aux plans
```

Ces scripts n'écrivent aucun asset de production en mode service ; en mode Studio
ils créent un projet explicitement nommé `TEST —` et le laissent en place comme
preuve.

---

## 6. Limites connues (à ne pas masquer)

- **Interface non contractuelle** : Meta AI peut modifier ses classes CSS, son
  composeur ou son routage. Le service échoue alors fermé avec un message
  explicite ; il ne fabrique pas d'image.
- **Cookies** : expiration et conditions d'utilisation Meta hors du contrôle du
  projet. Aucun cookie n'est versionné.
- **Continuité visuelle** : le service transmet du texte uniquement. Il n'envoie
  pas les portraits/décors comme références pixel. La continuité repose sur les
  descriptions du Director et sur les images initiales générées ; elle reste
  approximative, comme documenté dans le README principal.
- **Colorimétrie** : le contrôle automatique du test vérifie une dominance de
  teinte, pas la conformité artistique (un cube rouge très désaturé peut échouer
  alors que l'image reste acceptable pour un humain).
- **Windows** : les scripts PowerShell n'ont pas pu être exécutés dans
  l'environnement de développement (Linux, sans navigateur Meta). Ils sont
  volontairement simples et échouent avec un message clair ; leur exécution réelle
  fait partie du test A de `STUDIO-IA-RELEASE.md`.

---

## 7. Retour arrière

1. Arrêter le service (`Ctrl+C`).
2. Dans Studio IA : vider l'URL du service (les images manuelles restent
   disponibles) ou renseigner l'ancien service.
3. Renommer `studio-v2\metaai-service` — aucun autre composant ne l'importe.
4. Le code Studio IA reste utilisable sans ce service : l'import manuel d'images
   (`/api/assets/upload`, glisser-déposer) est inchangé.

Aucune base, aucun média et aucun projet existant n'est modifié par cette
procédure. Le service n'écrit que dans `studio-v2/data/...` via Studio IA et dans
son propre `data/state.json`.
