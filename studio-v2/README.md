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

L'API officielle DeepSeek utilise `/responses` avec une sortie `json_schema` issue des modèles Pydantic V2, sans champs de fichiers ni d'états de rendu. La validation locale reste active et aucun échec ne déclenche de seconde requête. Les URL personnalisées conservent leur contrat Chat Completions.

Pour les nouveaux scénarios, DeepSeek fournit les états initial et final et le cadrage de chaque plan. Le moteur construit `image_prompt` depuis l'état initial, les apparences et le décor, puis compile les instructions vidéo. Les prompts image déjà fournis restent compatibles. Les inserts sans personnage n'héritent plus du casting global. Les guides demandent une timeline cumulée, le respect des textes et sons du brief et la continuité des accessoires ; ces consignes améliorent la préparation sans garantir la qualité artistique du modèle.

Le Director vise généralement 6 à 8 générations pour 60 secondes, sauf nombre de plans explicitement demandé. Il regroupe les actions et réactions qui peuvent être jouées dans une même prise. C'est une consigne de préparation, sans découpage automatique ni fusion des projets existants ; le modèle reste responsable du storyboard et les coupes internes d'un clip ne sont pas garanties.

L'appel officiel utilise l'effort de réflexion `high` avec le même modèle et 65 536 tokens maximum, partagés entre la préparation interne et le JSON. Cela peut augmenter le délai et les tokens consommés ; la qualité artistique reste à vérifier en réel. Un essai de 30 plans a atteint l'ancien plafond de 16 384 avant de terminer. Une réponse tronquée reste une erreur explicite, sans seconde requête automatique. Le délai de lecture de l'API officielle est d'au moins 300 secondes pour laisser terminer les storyboards longs ; une valeur configurée plus élevée est conservée. Les autres délais et les URL personnalisées gardent `deepseek_timeout_s`.

Une nouvelle génération reçoit les descriptions fixes des personnages, décors et style déjà définis dans le projet, sans les anciens plans ni chemins de fichiers. Le nouveau brief reste prioritaire. Les portraits et décors sélectionnés sont réassociés si leurs identités, descriptions et style restent inchangés ; les anciennes images de plans et prises vidéo ne sont pas réutilisées.

Les futures réponses de scénario rejetées sont conservées intégralement dans `llm_runs.raw_content` pour un diagnostic local. La migration ajoute cette colonne sans modifier l'historique existant ; les anciens extraits tronqués ne permettent pas de reconstituer les réponses passées.

Des contrôles locaux de continuité avertissent sans bloquer : état initial qui ne reprend pas l'état final du plan précédent en temps continu, action dupliquée entre deux plans, brief « sans paroles » alors que des répliques existent. Ces messages apparaissent dans `llm_meta.warnings` et dans la préparation du rendu.

L'export refuse désormais deux plans partageant le même fichier vidéo, un montage entièrement noir et un fichier nettement plus court que la durée attendue ; le rapport QC reste consultable après l'export.

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

## Génération d'images Meta AI (service local isolé)

Les images automatiques passent par un service local séparé, livré dans
`studio-v2/metaai-service/` et documenté dans
[`docs/studio/META-AI-SERVICE.md`](../docs/studio/META-AI-SERVICE.md).

```powershell
cd studio-v2\metaai-service
.\install.ps1
.\start.ps1
```

Puis dans Studio IA : **Paramètres → URL du service metaai-api**
(`http://127.0.0.1:8000`) et la clé `META_AI_API_KEY` si elle est activée.
Les cookies Meta restent exclusivement dans `metaai-service\.env` (ignoré par Git).

Chaque génération ouvre une conversation Meta AI neuve et renvoie des preuves
(`request_id`, prompt exact, `conversation_id`, attribution). Studio IA refuse
avant enregistrement : requête ou prompt différents, conversation réutilisée,
média hors CDN Meta, fichier illisible ou inférieur à `metaai_min_image_side`.
La bibliothèque conserve la provenance de chaque visuel et ne présente jamais un
import ou un visuel historique comme une génération vérifiée.

Le lot `POST /api/projects/{id}/generate-images` prépare les personnages, les
décors puis les images initiales des plans. Les visuels déjà présents sont ignorés,
un refus individuel reste relançable, et une indisponibilité du service arrête le
lot (aucune boucle d'appels). L'import manuel d'une image reste disponible
(`/api/assets/upload`, glisser-déposer) et fonctionne sans ce service.

Contrôles utiles : `GET /api/assets/audit` (fichier présent, empreinte, provenance,
références projet) et `DELETE /api/assets/{id}` (retire un visuel non fiable de la
bibliothèque, détache ses références et **conserve le fichier**).

Le service est non officiel : l'interface Meta AI peut évoluer, les cookies
expirent et les conditions d'utilisation de Meta s'appliquent. Le transfert est
textuel : les portraits et décors ne sont pas envoyés comme références pixel, la
continuité visuelle reste approximative.

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
