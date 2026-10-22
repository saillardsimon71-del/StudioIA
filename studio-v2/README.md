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

Studio IA v2 appelle un serveur `metaai-api` séparé. **L'ancien client GraphQL n'est plus compatible avec Meta** : les erreurs `Unknown type RewriteOptionsInput`, `Unknown type AttachmentInput` et `Cannot query field sendMessageStream` viennent de cette implémentation serveur, pas du contrat Studio IA. Le contrat navigateur/DGW expose toujours `POST /image`. Le correctif Studio IA exige une conversation fraîche confirmée, une réponse stable du tour courant, un identifiant de conversation distinct, un `request_id` correspondant et un CDN Meta autorisé avant de télécharger et d'ajouter un asset. Les images restent non validées visuellement et ne deviennent pas automatiquement une approbation GPU.

StudioIA n'embarque pas `metaai-sdk` dans `studio-v2/requirements.txt` : ce service doit rester séparé. Au 22 octobre 2026, la distribution publiée `metaai-sdk 5.3.0` est compatible Python 3.11 et contient le contrat navigateur/DGW attendu. Attention : le tag Git `v3.0.0` actuellement présent pointe encore vers une révision `__version__ = 2.0.0` qui utilise l'ancien GraphQL ; ne pas installer ce tag pour corriger cette panne. Références auditées : [dépôt upstream](https://github.com/mir-ashiq/metaai-api), [migration et erreurs GraphQL supprimées](https://github.com/mir-ashiq/metaai-api/blob/main/CHANGELOG.md), [contrat REST `/image`](https://github.com/mir-ashiq/metaai-api/blob/main/src/metaai_api/api_server.py), [paquet PyPI](https://pypi.org/project/metaai-sdk/5.3.0/).

#### Mise à jour isolée sur Windows / PowerShell

Ces commandes installent le service dans un nouvel environnement Python 3.11, sans modifier l'ancien `site-packages`, l'installation StudioIA, ni aucun fichier `.env` existant. Le navigateur Chromium Playwright est installé dans le cache utilisateur Playwright. Exécutez-les dans une nouvelle fenêtre PowerShell :

```powershell
$Service = Join-Path $HOME 'MetaAIServiceV3'
py -3.11 -m venv "$Service\.venv"
& "$Service\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$Service\.venv\Scripts\python.exe" -m pip install 'metaai-sdk[api,playwright]==5.3.0'
& "$Service\.venv\Scripts\python.exe" -m playwright install chromium
& "$Service\.venv\Scripts\python.exe" -c "import metaai_api; print(metaai_api.__version__); print(metaai_api.__file__)"
```

Après l'installation, appliquez le patch ciblé et réversible au venv séparé :

```powershell
& ".\studio-v2\scripts\patch_metaai_sdk_5_3.ps1" -ServiceRoot $Service
```

Le patcher V4 exige `metaai-sdk==5.3.0` et une installation Studio IA V3 complète. Il sauvegarde les cinq modules V3 sous `site-packages\metaai_api\.studioia-backup\<horodatage>` afin de rester compatible avec le script de restauration existant, mais ne modifie que la sélection du champ de saisie dans `PlaywrightBackend.send_message()`. Les correspondances CSS sont réunies puis filtrées par visibilité, activation et éditabilité; un champ utilisable unique est sélectionné, zéro ou plusieurs champs valides provoquent une erreur diagnostique, sans choix arbitraire. Le correctif V3 accepte directement la page d’accueil si elle est déjà vierge; sinon, `new_chat()` retrouve le contrôle latéral par rôle, structure et nom accessible anglais/français, même si ce nom inclut un raccourci clavier, puis confirme le fil vierge. Les IDs des générations acceptées sont suivis pendant la vie du backend et un ID déjà utilisé est rejeté après la génération. Le flux V2 conserve la sérialisation du navigateur, la vérification de saisie/soumission et l’attente des médias du dernier conteneur assistant. `_dismiss_overlays` est désormais en lecture seule : aucun bouton générique « Connecter », « Accept » ou équivalent n'est cliqué. Les logs nomment l'étape (navigation, inspection overlay, mode, conversation, saisie, soumission, attente d'image) et signalent explicitement une route inattendue, dont Paramètres/Applications. Les appels image ne changent pas de mode implicitement. V4 refuse une installation partielle ou une version antérieure à V3. Il ne lit ni ne touche les cookies, les fichiers `.env`, les profils navigateur, les projets ou les images. En cas d'échec, seul `playwright_backend.py` est remis dans son état V3; la sauvegarde complète permet aussi d'utiliser le script de restauration existant. Pour revenir manuellement à l'original, arrêtez le service et passez le chemin de sauvegarde affiché par le patcher à `restore_metaai_sdk_5_3.ps1`; ce script sauvegarde d'abord l'état patché avant restauration : `& ".\studio-v2\scripts\restore_metaai_sdk_5_3.ps1" -ServiceRoot $Service -BackupPath 'C:\Users\saill\MetaAIServiceV3\.venv\Lib\site-packages\metaai_api\.studioia-backup\HORODATAGE'`. Redémarrez ensuite MetaAIServiceV3.

La dernière commande doit afficher `5.3.0` et un chemin sous `MetaAIServiceV3\.venv\Lib\site-packages`, pas l'ancien répertoire `Python311\Lib\site-packages`. Les cookies requis par la nouvelle version sont `datr` et `ecto_1_sess` (l'ancien `abra_sess` n'est pas le cookie de session attendu par ce backend). Pour les garder hors disque et hors dépôt, saisissez-les masqués dans la fenêtre PowerShell courante :

```powershell
$env:HOST = '127.0.0.1'
$env:PORT = '8000'
$env:META_AI_DATR = [System.Net.NetworkCredential]::new('', (Read-Host 'Cookie Meta datr' -AsSecureString)).Password
$env:META_AI_ECTO_1_SESS = [System.Net.NetworkCredential]::new('', (Read-Host 'Cookie Meta ecto_1_sess' -AsSecureString)).Password
& "$Service\.venv\Scripts\python.exe" -m metaai_api.api_server
```

Ces variables ne vivent que dans cette session PowerShell et ses processus enfants. Aucun cookie n'est écrit dans Git ou dans le `.env` existant. Gardez le service lié à `127.0.0.1`; StudioIA utilise par défaut `http://127.0.0.1:8000`. Si `META_AI_API_KEY` est défini côté service, renseignez la même clé dans **Paramètres → Clé du service Meta AI** de StudioIA ; sinon laissez la clé StudioIA vide. Avant le lancement, vérifiez que le port n'est pas occupé par un ancien serveur :

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
```

N'arrêtez que le processus MetaAI ancien que vous avez identifié.

#### Validation Windows obligatoire (à effectuer, non exécutée depuis Arena)

Le test d'acceptation doit être effectué sur le PC Windows avec le service patché. Créez un projet de test jetable comportant trois plans sans image déjà affectée, puis notez leurs identifiants. Les appels suivent sans pause humaine et créent trois entrées d'asset marquées `TEST —` ; aucune entrée existante n'est remplacée ou effacée :

```powershell
& ".\studio-v2\scripts\verify_metaai_three_generations.ps1" `
  -ProjectId 'ID_DU_PROJET_TEST' `
  -RedCubeShotId 'ID_PLAN_CUBE' `
  -GreenSphereShotId 'ID_PLAN_SPHERE' `
  -BluePyramidShotId 'ID_PLAN_PYRAMIDE'
```

Le script exige trois prompts exacts (cube rouge, sphère verte, pyramide bleue), trois `request_id`/`conversation_id` concordants et distincts, télécharge les images, puis vérifie que chaque asset est associé au bon plan du bon projet. Inspectez visuellement les trois fichiers indiqués par le script et l'interface Studio IA : le code ne prétend pas reconnaître le sujet de l'image par analyse visuelle. Échec, conversation répétée, preuve absente, image hors CDN autorisé ou association incohérente = validation refusée. Le test ne doit pas être remplacé par les tests simulés.

Pour auditer les références actuelles sans modification : `GET /api/assets/audit` (les assets et références sont listés, sans classification automatique). Dans la bibliothèque, **Retirer cette référence…** demande une confirmation, détache uniquement l'asset choisi de ses références et conserve le fichier sur disque ; aucun asset n'est retiré automatiquement. Vérifiez les références visuellement avant toute action.

#### Vérification réelle sans modifier les projets StudioIA

Avec le service ci-dessus lancé, cette requête déclenche une vraie génération Meta, attend jusqu'à 180 secondes, récupère le premier fichier dans `%TEMP%` et vérifie une signature image courante. Elle ne crée ni asset ni modification de projet StudioIA :

```powershell
$RequestId = [guid]::NewGuid().ToString('N')
$Body = @{ prompt = 'A simple red apple on a plain white background'; orientation = 'VERTICAL'; new_conversation = $true; num_images = 1; request_id = $RequestId } | ConvertTo-Json -Compress
$Result = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/image' -Method Post -ContentType 'application/json' -Body $Body -TimeoutSec 180
if (-not $Result.success -or $Result.request_id -ne $RequestId -or -not $Result.conversation_id -or -not $Result.image_urls -or $Result.image_urls.Count -lt 1) { throw "Le serveur n'a pas confirmé une génération liée à une conversation fraîche." }
$ImagePath = Join-Path $env:TEMP 'metaai-studio-smoke-test.bin'
Invoke-WebRequest -Uri $Result.image_urls[0] -OutFile $ImagePath -TimeoutSec 60
$Bytes = [System.IO.File]::ReadAllBytes($ImagePath)
if ($Bytes.Length -lt 12) { throw "Fichier image vide ou trop court ($($Bytes.Length) octets)." }
$Hex = [System.BitConverter]::ToString($Bytes[0..11])
$IsKnownImage = $Hex.StartsWith('89-50-4E-47') -or $Hex.StartsWith('FF-D8') -or $Hex.StartsWith('47-49-46-38') -or $Hex.StartsWith('42-4D') -or ($Hex.StartsWith('52-49-46-46') -and $Hex.Contains('57-45-42-50'))
if (-not $IsKnownImage) { throw "Le téléchargement n'est pas une image connue : $Hex" }
"Image récupérée : $ImagePath ($($Bytes.Length) octets), URL présente dans image_urls."
```

Pour vérifier ensuite l'intégration storyboard, relancez uniquement un plan en échec depuis la bibliothèque du projet. Chaque image réussie est enregistrée indépendamment ; les plans déjà associés ne sont pas remplacés. Une génération `/image` réelle n'a pas été exécutée depuis Arena : le service local Windows et les cookies ne sont pas accessibles ici.

Depuis le scénario, la génération du lot prépare les personnages, les décors puis les images initiales. Les associations sont enregistrées après chaque image. Les visuels présents sont conservés ; les fichiers manquants peuvent être repris. Un refus individuel reste relançable et une indisponibilité arrête le lot.

Cette intégration reste expérimentale : `metaai-api` est non officiel, repose sur l'automatisation du navigateur et des cookies susceptibles d'expirer ; disponibilité, conditions d'utilisation Meta et stabilité ne sont pas garanties. La génération prend typiquement 30–40 secondes. Cela ne génère pas les vidéos.

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
