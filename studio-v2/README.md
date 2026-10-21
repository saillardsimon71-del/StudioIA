# Studio IA V2 — Pipeline Minimal LTX-2.5

Reconstruction isolée de Studio IA à la racine `studio-v2/` **sans aucune modification ni dépendance envers V1**.

- **Port par défaut** : `8270` (totalement distinct des ports V1 `8247` et `8260`)
- **Base SQLite unique** : `studio-v2/data/v2.db`
- **Source unique de vérité** : `studio-v2/core/scenario.py` (`Project`, `Character`, `Shot`, `DialogueLine`, `DisplayOverlay`)
- **Interface web** : SvelteKit 2 + Svelte 5 compilée dans `studio-v2/web/ui_bundle/` et servie directement par FastAPI sur le port `8270`

---

## Note sur la branche Git

Branche de travail : `test-director-v2`. Le refspec d'origin peut etre restrictif. Pour recuperer explicitement cette branche sans reinitialiser les modifications locales :

```powershell
git fetch origin refs/heads/test-director-v2:refs/remotes/origin/test-director-v2
```

---

## Lancement rapide sous Windows

```cmd
cd studio-v2
setup.bat
start.bat
```

Interface : **`http://127.0.0.1:8270`**. Si le port est deja occupe, le lancement est refuse avant toute ouverture de navigateur. Arretez l'ancienne instance puis relancez. `/api/health` expose l'empreinte des sources chargees et `restart_required`.

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

## Génération d'images Meta AI (optionnelle / expérimentale)

StudioIA v2 peut appeler un serveur séparé `metaai-api` depuis la bibliothèque visuelle. Installez et lancez ce projet selon sa documentation, configurez-y `META_AI_DATR` et `META_AI_ECTO_1_SESS` (et `META_AI_API_KEY` si souhaité), puis renseignez son URL dans **Paramètres → URL du service metaai-api**. Si une clé serveur est activée, saisissez-la également dans les paramètres StudioIA. Les cookies Meta restent côté serveur `metaai-api`.

Depuis le scenario, la generation du lot prepare les personnages, les decors puis les images initiales. Les associations sont enregistrees apres chaque image. Les visuels presents sont conserves ; les fichiers manquants peuvent etre repris. Un refus individuel reste relancable, et une indisponibilite du service arrete le lot.

Le serveur attendu expose `POST /image` avec `prompt` et renvoie `image_urls`. Cette integration transmet du texte uniquement : les portraits et decors sauvegardes ne sont pas des references pixel utilisees pour generer les plans. La continuite visuelle repose sur les descriptions du Director et reste approximative.

Cette intégration est expérimentale : `metaai-api` est non officiel, repose sur l'automatisation du navigateur et des cookies susceptibles d'expirer ; disponibilité, conditions d'utilisation Meta et stabilité ne sont pas garanties. La génération prend typiquement 30–40 secondes. Cela ne génère pas les vidéos.

## Bible créative permanente (DeepSeek)

Les trois documents `creative-guides/01_attention.md`, `02_retention.md` et `03_narration.md` sont automatiquement lus à **chaque génération de scénario**. Ils s'ajoutent au prompt système existant, sans nouveau modèle, sans agent ni appel API supplémentaire. Les consignes sont des principes éditoriaux adaptables, pas une chronologie imposée.

Les fichiers peuvent être édités manuellement ; la modification sera prise en compte dès la génération suivante (pas de cache). S'il manque un fichier ou s'il est vide, le scénario est arrêté avec une erreur explicite **avant** l'appel DeepSeek.

La réponse de génération expose `llm_meta.creative_guides_version` (empreinte courte des trois fichiers). Cela ne modifie pas le schéma Pydantic `Project` ni les vidéos déjà générées. Il n'y a aucun accès aux analytiques TikTok, Instagram ou YouTube.

Vérification rapide sans coût API ni GPU :

```powershell
cd studio-v2
.\.venv\Scripts\python.exe -m pytest tests/test_creative_guides.py -q
```

## Audit de production du 21 octobre 2026

Voir [le bilan technique et le test reel controle](../docs/studio/AUDIT_V2_2026-10-21.md). Les prompts LTX sont toujours recompiles depuis le scenario avant envoi ; le garde-fou local est de 300 mots, sans troncature des apparences. Les prises Modal en attente sont conservees dans SQLite et recuperees par leur identifiant, sans nouvelle generation. Les lots restent pilotes par l'onglet ouvert.

Le modele par defaut est `deepseek-flash`. L'ancien nom `deepseek-chat` est resolu vers ce modele au chargement sur `api.deepseek.com`, sans reecrire la configuration existante ni affecter les services compatibles configures sur un autre domaine.
