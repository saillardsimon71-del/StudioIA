# Studio IA V2 — Décisions d'architecture (Diagnostic V1 `c395532`)

**Constat sur V1 (`calliope-backend` / `calliope-web`)** : V1 empile un moteur générique de graphe ComfyUI/canevas, un harnais multi-agents (`agent/harness`), un planificateur multi-passes avec critique narratif LLM autonome (`series_planner.py`, `director.py`) et six représentations concurrentes d'un même plan (`projects`, `scenes`, `clips`, `items`, `takes`, `director_contracts`). Résultat observé le 19–20/10/2026 : plus de 1 700 tests unitaires mais blocages répétés avant ou pendant la génération d'un court métrage de 15 s (auto-réparations divergentes, contradictions entre JSON d'état et prompts libres, mauvais environnement Python pour Whisper).

## 1. Conserver / Extraire (fonctions ciblées réécrites sans dépendance V1)

| Source V1 | Extraction V2 | Justification |
|---|---|---|
| `engines/ltx_graph.py`, `engines/modal_h3.py`, `deploy/modal_ltx.py` | `studio-v2/engines/video.py` + `deploy/modal_ltx_v2.py` | Contrat RPC `studio-ia-ltx.render_clip` déjà validé sur Modal (768×1344, 24 fps, grille `8k+1 ≤ 361` frames, PNG base64, télémétrie `generation_seconds`, relâchement `scaledown_window=2`). |
| `studio/ltx_prompt_v3.py` (règles LTX-2.5) | `studio-v2/core/ltx_prompt.py` | Prose visuelle au présent, répliques françaises entre guillemets, interdiction des balises H3/timings absolus. Plafond dur ramené à **150 mots** avec arrêt net avant GPU (zéro auto-réparation). |
| `studio/generated_text.py` | `studio-v2/qc/ocr.py` | Échantillonnage d'images + OCR Tesseract, baseline frame 0, liste blanche de chiffres déclarés (`0/O`, `1/l/I`), distinction avertissement (`unverified`) / refus (`unexpected_text`) + dérogation humaine signée SHA-256. |
| `studio/transcription.py` | `studio-v2/qc/speech.py` | Transcription Whisper CPU exécutée dans le `.venv` de V2, détection du début de parole (`first_word_start_sec`), normalisation des nombres FR/EN et calcul WER/couverture. |
| `studio/export.py`, `studio/screen_text.py` | `studio-v2/core/export.py`, `studio-v2/qc/media.py` | Assemblage FFmpeg MP4 vertical 1080×1920 H.264 `yuv420p` + AAC 48 kHz stéréo (`loudnorm`), extraction de dernière frame pour continuité, incrustation ASS fixe horodatée pour chiffres exacts, et vérification des flux livrés. |

## 2. Reconstruire simplement

- **Source unique de vérité (`studio-v2/core/scenario.py` + `data/v2.db`)** : un seul modèle Pydantic V2 (`Project` → `Character`, `Shot`, `DialogueLine`, `DisplayOverlay`) persisté tel quel dans SQLite, accompagné de `library_assets`, `takes` et `exports`.
- **Appel DeepSeek unique (`studio-v2/llm/deepseek.py`)** : 1 clic utilisateur = 1 requête HTTP JSON vers `api.deepseek.com`, validée par Pydantic V2. Toute erreur affiche le diagnostic à l'humain sans second agent.
- **Contrôle des coûts (`LtxModalEngine`)** : estimation calculée depuis le tarif réel Modal (`€/s` ou `$/s` GPU+CPU/RAM) et la durée mesurée/calibrée, avec affichage des incertitudes, plafond configurable et autorisation humaine explicite prise par prise.
- **Frontend SvelteKit 2 + Svelte 5 (`studio-v2/web/`)** : SPA statique (`adapter-static`) servie directement par FastAPI sur le port **8270** (un seul processus à lancer sous Windows, même écosystème Svelte 5 que V1 sans la dette du canevas/multi-agents, bien plus léger à maintenir que Next.js).

## 3. Abandonner définitivement

- Harnais multi-agents, sous-agents et outils shell (`calliope/agent/*`).
- Critique narratif LLM autonome, boucles d'auto-réparation et `director_contracts.py` redondants.
- Éditeur nodal/canevas ComfyUI générique, Three.js / `mannequin-js`, et profils MiniMax H3.
- File d'attente à retries GPU automatiques et génération d'images par API (remplacée par l'import glisser-déposer d'images ChatGPT validées par l'opérateur).
- Suivi optique complexe de texte sur objets mobiles (`calcOpticalFlowPyrLK`) pour le prototype : zone d'affichage fixe sur écran vierge.
