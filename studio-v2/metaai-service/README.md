# Service Meta AI — Studio IA V2

Service HTTP local qui produit **une image par requête, dans une conversation Meta AI neuve**, et renvoie les preuves permettant à Studio IA de refuser tout visuel non attribuable à la demande courante.

- Port par défaut : **8000** (Studio IA V2 reste sur 8270)
- Installation **séparée** de Studio IA : son propre `.venv`, ses cookies, son état
- Aucun fichier de `site-packages` n'est modifié, aucun paquet n'est « patché »
- Contrat public : `GET /healthz`, `POST /image`, `POST /reset`

Pour l'analyse détaillée de l'ancienne intégration, des défauts du SDK upstream et des raisons de cette architecture : [`docs/studio/META-AI-SERVICE.md`](../../../docs/studio/META-AI-SERVICE.md).

---

## Installation (Windows)

Prérequis : Python 3.11 ou 3.12 (python.org), accès réseau pour installer les paquets et Chromium.

```powershell
cd studio-v2\metaai-service
.\install.ps1
.\install.ps1 -Force          # recrée le .venv local si besoin
.\install.ps1 -SkipBrowser    # si Chromium est déjà installé pour Playwright
```

`install.ps1` crée `.venv`, installe les dépendances épinglées, télécharge Chromium et exécute les tests hors ligne. Il ne modifie ni les cookies, ni Studio IA, ni le paquet `metaai-sdk`.

## Cookies

Les cookies Meta restent **uniquement** sur cette machine, dans un fichier ignoré par Git :

`studio-v2\metaai-service\.env`

```
META_AI_DATR=...
META_AI_ECTO_1_SESS=...
META_AI_API_KEY=une-cle-locale-longue
META_AI_PORT=8000
```

Comment les obtenir : se connecter sur <https://www.meta.ai/>, DevTools (F12) → Application → Cookies → copier `datr` et `ecto_1_sess`. Ne jamais coller ces valeurs dans GitHub, une issue ou une conversation.

`META_AI_API_KEY` est facultative mais recommandée : Studio IA l'envoie en en-tête `Authorization: Bearer …`.

## Démarrer

```powershell
cd studio-v2\metaai-service
.\start.ps1                 # http://127.0.0.1:8000
.\start.ps1 -Diagnose       # ouvre Chromium et écrit data\diagnose-*/ (capture + HTML + état)
.\start.ps1 -Headed         # affiche la fenêtre du navigateur pendant les générations
```

Dans Studio IA : **Paramètres → URL du service metaai-api = `http://127.0.0.1:8000`**, puis la même clé que `META_AI_API_KEY`.

## Validation : trois générations automatiques

Service démarré, sans clic dans Meta AI :

```powershell
.\verify-three-generations.ps1
```

Le script enchaîne cube rouge → sphère verte → pyramide bleue et vérifie :

- trois `request_id` distincts et écho du prompt exact ;
- trois `conversation_id` distincts (aucune conversation réutilisée) ;
- attribution `fresh_assistant_response` et message envoyé confirmé ;
- trois fichiers binairement différents, au moins 256 px de côté ;
- couleur dominante conforme (rouge / vert / bleu).

Il écrit `verification.json` et les trois images dans `%TEMP%\StudioIA-MetaAI-<horodatage>` et sort en erreur si un critère échoue. Ce test ne crée aucun asset de production.

Le test côté Studio IA (création d'un projet de test, lot de trois images, associations aux bons plans, provenance en base) :

```powershell
cd studio-v2
.\scripts\verify-metaai-studio.ps1
```

## Contrat HTTP

### `POST /image`

```json
{
  "prompt": "A single red cube on a plain white background...",
  "request_id": "uuid-fourni-par-le-client",
  "new_conversation": true
}
```

Réponse 200 :

```json
{
  "success": true,
  "image_urls": ["https://scontent...fbcdn.net/..."],
  "request_id": "uuid-fourni-par-le-client",
  "prompt": "prompt exact reçu",
  "conversation_id": "uuid-de-la-conversation-neuve",
  "previous_conversation_id": "",
  "attribution": "fresh_assistant_response",
  "assistant_turn_index": 0,
  "prompt_echo_verified": true,
  "service_version": "1.0.0",
  "elapsed_seconds": 38.4
}
```

Erreurs : `401` clé API, `409` requête déjà exécutée, `422` refus explicite de Meta AI, `429` génération déjà en cours, `502` provenance non prouvée, `503` service non configuré, `504` délai dépassé. Dans tous les cas, **aucune image n'est enregistrée** et aucune relance automatique n'est faite.

### `GET /healthz`

État sans secret : version, navigateur initialisé, cookies présents, nombre de conversations connues, dernière erreur.

### `POST /reset`

Ferme Chromium ; les cookies et l'état des conversations (fichier `data\state.json`) sont conservés.

## Garanties et limites

Le service ouvre **une page d'accueil vierge avant chaque image**, refuse une page qui n'est pas une conversation vide, n'accepte que les médias du **nœud de réponse de l'assistant du tour courant**, et rejette toute conversation déjà utilisée (mémoire persistante). Aucun contrôle de l'interface n'est cliqué à l'aveugle ; seuls des libellés exacts de consentement ou de nouvelle conversation sont utilisés en secours.

Limites réelles :

- L'interface Meta AI peut changer : sélecteurs, libellés et structure DOM ne sont pas contractuels. `-Diagnose` sert à constater rapidement une rupture.
- Les cookies expirent et les conditions d'utilisation de Meta s'appliquent ; ce service est non officiel.
- Aucune image de référence (portrait/décor) n'est envoyée : la continuité repose sur les descriptions du Director, pas sur un transfert pixel.
- La vérification de couleur du script de validation est un contrôle technique, pas un jugement artistique.

## Retour arrière

1. `Ctrl+C` dans la fenêtre du service.
2. Dans Studio IA : Paramètres → URL du service metaai-api → revenir à l'ancienne valeur, ou laisser vide pour désactiver les images automatiques.
3. Supprimer (ou renommer) le dossier `studio-v2\metaai-service` : aucun autre composant n'en dépend.
4. `metaai-sdk`, Chromium et les cookies de l'ancien service ne sont pas modifiés par cette installation ; l'ancien `MetaAIServiceV3` reste utilisable tel quel si nécessaire.

Aucune donnée de Studio IA (projets, médias, base `v2.db`) n'est touchée par cette procédure.
