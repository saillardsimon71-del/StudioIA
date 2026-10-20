# Studio IA V2 — Suivi transparent des coûts (`COSTS.md`)

**Règle d'or V2** : aucun prix inventé. Chaque estimation repose uniquement sur les tarifs horaires/seconde publics des fournisseurs et sur les durées réelles mesurées sur le worker Modal `studio-ia-ltx`. Si le tarif GPU est mis à `null` dans `studio-v2/config.json`, `estimate_cost()` renvoie strictement `None`.

---

## 1. Paramètres réels des fournisseurs (référence octobre 2026)

### A. LLM — DeepSeek API (`deepseek-chat`, mode JSON)
- **Tarif public DeepSeek** : `$0.27 / 1M` tokens d'entrée (hors cache) · `$1.10 / 1M` tokens de sortie.
- **Consommation réelle d'un scénario complet 15 s (1 appel unique)** : ~900 tokens d'entrée + ~700 tokens de sortie ≈ **`$0.0010` (~`0.0009 €` par génération)**.
- **Politique V2** : **1 clic = 1 appel HTTP**. Zéro critique narratif secondaire, zéro boucle de réécriture automatique.

### B. Vidéo — Modal GPU (`studio-ia-ltx.render_clip`)
- **Tarifs publics Modal (`https://modal.com/pricing`)** :
  - **Profil production (`docs/studio/LTX.md`) — L40S + 8 CPU + 64 GiB RAM** :
    - GPU `L40S` : `$0.000542 / s` (`$1.95 / h`)
    - CPU (`8 × $0.0000394 / s`) : `$0.0003152 / s`
    - RAM (`64 GiB × $0.00000672 / s`) : `$0.0004301 / s`
    - **Total conteneur actif** : **`$0.001287 / s`** (soit **`0.001184 € / s`** au taux indicatif `0.92 €/USD`)
  - **Profil alternatif (`deploy/modal_ltx.py`) — RTX PRO 6000 + 2 CPU + 32 GiB RAM** :
    - GPU : `$0.000850 / s` + CPU `$0.0000788 / s` + RAM `$0.0002150 / s` = **`$0.001144 / s`** (**`0.001052 € / s`**)

### C. Durées de calcul observées sur LTX-2.5 (`768×1344`, 24 fps, 8+3 étapes)
Source mesurée dans `docs/studio/LTX.md` (tirages réels du 13/10/2026 sur `studio-ia-ltx`) :
- **Conteneur déjà chaud** : `78.17 s` pour `145` frames (`0.539 s / frame`), relâchement `scaledown_window = 2 s`.
- **Conteneur à froid** (montage des poids `studio-ltx25-models` + démarrage ComfyUI) : `151.25 s` à `197.25 s` (+73 à +119 s d'amorçage).

### D. Estimation Phase 3 — 1 prise de 5,0 s (`121` frames @ 24 fps, profil L40S)
- **Cas chaud minimal (`~67.3 s`)** : **`~0.0797 €`** (`~$0.0867`)
- **Cas attendu à froid (`~152.3 s`)** : **`~0.1804 €`** (`~$0.1961`)
- **Pire cas (`~247.3 s`, amorçage lent + 60 s d'inactivité)** : **`~0.2929 €`** (`~$0.3184`)
- **Incertitudes explicites** :
  1. État du conteneur Modal au moment de l'appel (chaud vs froid : facteur ×2,2).
  2. Délai effectif de `scaledown_window` si la coupure réseau empêche sa réduction à 2 s.
  3. Taux de change USD/EUR appliqué par la banque lors de la facturation Modal.

---

## 2. Temps humain estimé par étape (workflow V2)

| Étape | Action humaine | Durée estimée | Coût API / GPU |
|---|---|---|---|
| Phase 1–2 | Diagnostic V1 + reconstruction `studio-v2/` + tests hors GPU | Réalisé dans cette session | **0,00 €** |
| Phase 3 — Image | Génération manuelle de l'image 9:16 dans ChatGPT + glisser-déposer + validation visuelle | ~3 à 5 min | Abonnement ChatGPT existant |
| Phase 3 — Rendu | Vérification du préflight + autorisation explicite + 1 prise LTX de 5 s | ~3 min d'attente | Estimé **0,08 € – 0,30 €** (non lancé — en attente d'autorisation) |
| Phase 3 — Revue | Visionnage du MP4 1080×1920 + lecture du rapport OCR/Whisper | ~2 min | **0,00 €** |

---

## 3. Journal chronologique des dépenses réelles et échecs

*alimenté automatiquement par `LtxModalEngine.render()` lors de chaque exécution autorisée*

| Date (UTC) | Projet / Plan | Statut | Coût estimé | Coût réel mesuré | Temps GPU | Notes / Cause éventuelle |
|---|---|---|---|---|---|---|
| 2026-10-20 | Phases 1 & 2 + Préparation Phase 3 (Arena sandbox) | PRÉPARÉ (HORS GPU) | 0.1804 € (Phase 3 si autorisé) | **0.0000 €** | **0.0 s** | Arrêt obligatoire avant GPU respecté ; sandbox Arena sans accès sortant vers Modal/DeepSeek |
