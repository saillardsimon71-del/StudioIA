# Studio IA V2 — Pipeline Minimal LTX-2.5

Reconstruction isolée de Studio IA à la racine `studio-v2/` **sans aucune modification ni dépendance envers V1**.

- **Port par défaut** : `8270` (totalement distinct des ports V1 `8247` et `8260`)
- **Base SQLite unique** : `studio-v2/data/v2.db`
- **Source unique de vérité** : `studio-v2/core/scenario.py` (`Project`, `Character`, `Shot`, `DialogueLine`, `DisplayOverlay`)
- **Interface web** : SvelteKit 2 + Svelte 5 compilée dans `studio-v2/web/ui_bundle/` et servie directement par FastAPI sur le port `8270`

---

## Note sur la branche Git

Dans l'environnement Arena AI, le nom de la branche de session distante est verrouillé sur `arena/0170e708-studioia` (issue directement de `c395532` sur `arena/fb4de4c0-studioia`, jamais fusionnée dans la branche principale).  
Sur votre PC Windows, pour créer la branche locale nommée `arena/studio-ia-v2-rebuild` :

```powershell
git fetch origin
git checkout -B arena/studio-ia-v2-rebuild origin/arena/0170e708-studioia
```

---

## Lancement rapide sous Windows

```cmd
cd studio-v2
setup.bat
start.bat
```

Ouvre automatiquement **`http://127.0.0.1:8270`**.

### Prérequis optionnels sur Windows
- **Clé DeepSeek** : renseignez `deepseek_api_key` dans `studio-v2\config.json` (ou via le bouton **Paramètres** de l'interface, ou la variable d'environnement `DEEPSEEK_API_KEY`).
- **FFmpeg** : déjà fourni par `imageio-ffmpeg` dans `studio-v2\.venv`, ou via votre `ffmpeg`/`ffprobe` système s'il est dans le `PATH`.
- **Tesseract OCR** (contrôle des sous-titres parasites) : `winget install UB-Mannheim.TesseractOCR` (si absent, V2 signale proprement un avertissement de revue humaine sans bloquer).
- **Modal GPU** : authentification Modal existante (`modal token info`, app `studio-ia-ltx`, fonction `render_clip`).

---

## Tests automatisés (sans GPU)

```cmd
cd studio-v2
test.bat
```

---

## Exécution de la Phase 3 (Fruit anthropomorphe — 1 prise réelle de 5 s)

### 1. Générer l'image initiale 9:16 dans ChatGPT
Prompt suggéré pour ChatGPT :
> *Vertical 9:16 stylized 3D cinema frame, warm moody kitchen counter under soft neon light at night. An anthropomorphic yellow lemon detective with textured citrus skin, dark expressive eyes, wearing a tiny rumpled beige trench coat, standing behind a wooden counter with arms crossed and a suspicious gaze toward the camera. Zero text, zero letters, zero subtitles anywhere in the image.*

Enregistrez le PNG sous `fruit_zeste.png`.

### 2. Vérifier le préflight et le coût estimé SANS lancer le GPU (0,00 €)
```cmd
cd studio-v2
phase3_prepare.bat "C:\chemin\vers\fruit_zeste.png"
```

### 3. Lancer l'unique prise de 5 s APRÈS votre autorisation explicite
Via l'interface web (`http://127.0.0.1:8270` → **Modèle Phase 3** → glisser l'image → **Valider pour GPU** → cocher l'autorisation unitaire → **Générer shot_1 sur Modal**), ou en ligne de commande :
```cmd
cd studio-v2
phase3_run_authorized.bat "C:\chemin\vers\fruit_zeste.png" 0.50
```
Le script demande confirmation (`OUI`), exécute **une seule prise** sans retry automatique, enregistre le coût réel dans `studio-v2/data/v2.db` et `COSTS.md`, lance l'inspection média + OCR Tesseract + Whisper CPU, et génère l'export vertical `1080×1920` prêt à visionner.

---

## Bible créative permanente (DeepSeek)

Les trois documents `creative-guides/01_attention.md`, `02_retention.md` et `03_narration.md` sont automatiquement lus à **chaque génération de scénario**. Ils s'ajoutent au prompt système existant, sans nouveau modèle, sans agent ni appel API supplémentaire. Les consignes sont des principes éditoriaux adaptables, pas une chronologie imposée.

Les fichiers peuvent être édités manuellement ; la modification sera prise en compte dès la génération suivante (pas de cache). S'il manque un fichier ou s'il est vide, le scénario est arrêté avec une erreur explicite **avant** l'appel DeepSeek.

La réponse de génération expose `llm_meta.creative_guides_version` (empreinte courte des trois fichiers). Cela ne modifie pas le schéma Pydantic `Project` ni les vidéos déjà générées. Il n'y a aucun accès aux analytiques TikTok, Instagram ou YouTube.

Vérification rapide sans coût API ni GPU :

```powershell
cd studio-v2
.\.venv\Scripts\python.exe -m pytest tests/test_creative_guides.py -q
```
