# Migration LTX-2.5 → nouveau compte Modal `kulejean567`

Guide opérationnel pour rendre le compte Modal `kulejean567` productif pour Fruit City.
**Aucune modification de V1 ni du pipeline V2** — seuls les scripts de déploiement sont concernés.
Arena n'a pas accès à votre compte Modal : toutes les commandes ci-dessous s'exécutent **sur votre PC Windows**.

---

## 1. Prérequis PC

- Compte Modal `kulejean567` déjà authentifié :
  ```powershell
  modal token new
  modal token info
  ```
  Doit afficher `kulejean567` et une validité 90 j. Aucun jeton n'est présent dans le code.

- Révision validée (figée) :
  - Modèle HF `Lightricks/LTX-2.5` @ `49b093a39c19169a8a81721dcc541e07e945232f` (docs/studio/LTX.md)
  - ComfyUI `d49e888586dd8ae012c0667b33466b815fee07f7`
  - Torch `2.8.0` `cu128`

- Volume cloud déjà créé mais vide :
  ```powershell
  modal volume ls
  # doit lister studio-ltx25-models (vide)
  ```
  Si absent :
  ```powershell
  modal volume create studio-ltx25-models
  ```

---

## 2. Installer les 5 poids LTX-2.5 sur le volume (reproductible)

Script autonome `studio-v2/deploy/modal_download_ltx.py` — téléchargement **direct HF → Volume** sans passer par votre disque local. Reprise automatique si un fichier existe déjà.

### 2.1 Emplacements exacts attendus par le worker

| Clé V2 | Fichier HF exact | Dossier dans `studio-ltx25-models` |
|---|---|---|
| `unet` | `ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors` | `diffusion_models/` |
| `clip` | `gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors` | `text_encoders/` |
| `video_vae` | `ltx-2.5-video-vae-conv-bf16.safetensors` | `vae/` |
| `audio_vae` | `ltx-2.5-audio-vae-bf16.safetensors` | `vae/` |
| `upscaler` | `ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors` | `latent_upscale_models/` |

Le worker `modal_ltx_v2.py:mount_models()` crée un symlink RO vers `ComfyUI/models/<dossier>/<fichier>` et refuse d'écraser un fichier existant.

### 2.2 Commande Windows unique

Depuis la racine du dépôt (`C:\...\StudioIA`) :

```powershell
modal run studio-v2/deploy/modal_download_ltx.py
```

Options :
```powershell
# Vérif seule, sans télécharger (0 €, 2-5 s)
modal run studio-v2/deploy/modal_download_ltx.py --verify-only

# Re-télécharger un seul fichier (ex. unet corrompu)
modal run studio-v2/deploy/modal_download_ltx.py --key unet --force

# Forcer tout
modal run studio-v2/deploy/modal_download_ltx.py --force
```

Le script affiche pour chaque fichier : `présent/missing`, taille humaine, SHA-256 (16 premiers chars) et commit le volume après chaque copie. Les gros fichiers (>2 Go) sautent le SHA pour rester sous le timeout ; la taille reste vérifiée.

### 2.3 Vérifications attendues (tailles & intégrité)

Après téléchargement, le script doit afficher `✓ Les 5 poids LTX-2.5 sont présents`.

Vous pouvez aussi lister le volume manuellement :
```powershell
modal volume ls studio-ltx25-models
# ou, depuis une fonction :
modal run studio-v2/deploy/modal_download_ltx.py --verify-only
```

Attendu :
- `diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors` — taille Go (INT8)
- `text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors` — taille Go (Gemma 12B)
- `vae/ltx-2.5-video-vae-conv-bf16.safetensors` + `vae/ltx-2.5-audio-vae-bf16.safetensors`
- `latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors`

Si un fichier manque, `mount_models()` lèvera au déploiement :
`Poids LTX absents dans le volume ... — exécutez d'abord modal run ...`

### 2.4 Dépendances ComfyUI

L'image du worker installe automatiquement :
- `PyTorch 2.8.0` `cu128` (conservé depuis LTX.md)
- ComfyUI @ `d49e888586dd8ae012c0667b33466b815fee07f7` (`pip install -r requirements.txt`)
- `ffmpeg`, `libgl1`, `libglib2.0-0` etc.

Aucune installation manuelle n'est nécessaire. La vérif finale du script affiche `ComfyUI d49e88... / Torch 2.8.0+cu128`.

---

## 3. Déployer le worker autonome `studio-ia-ltx.render_clip`

Fichier `studio-v2/deploy/modal_ltx_v2.py` — **100% autonome**, zéro import `calliope` :

```powershell
modal deploy studio-v2/deploy/modal_ltx_v2.py
```

Vérifications post-déploiement :
```powershell
modal app list
# doit lister studio-ia-ltx

modal volume ls studio-ltx25-models
# les 5 fichiers doivent être visibles
```

Caractéristiques du worker :
- App `studio-ia-ltx`, fonction `render_clip(payload) -> {video_base64, metadata}`
- GPU `L40S`, `8 CPU`, `64 Gio RAM` (profil prod docs/studio/LTX.md, 0.001287 $/s)
- `scaledown_window=2` (relâchement immédiat), `timeout=5400`
- Contrat d'entrée/sortie strict = celui de `studio-v2/engines/video.py` (framesForDuration LTX, 8k+1, 768×1344 etc.)

Aucune application Modal ne doit rester en `min_containers >0` — arrêt après 60 s d'inactivité, aucun coût à vide.

---

## 4. Vérification de disponibilité SANS génération payante (0,00 €)

### 4.1 Via l'API V2 (recommandé)

Lancez Studio V2 localement (sans GPU) :
```powershell
cd studio-v2
setup.bat
start.bat
# ouvre http://127.0.0.1:8270
```

Puis :
```powershell
curl http://127.0.0.1:8270/api/modal/status
# ou via l'UI : Réglages → État Modal
```

Réponse attendue :
```json
{"ok": true, "message": "Worker Modal `studio-ia-ltx.render_clip` accessible."}
```

Côté code, c'est `modal.Function.from_name(...).hydrate()` — aucun conteneur GPU n'est alloué, donc **0,00 €**.

### 4.2 Via le script local `check_modal.py`

```powershell
cd studio-v2
.\.venv\Scripts\python.exe scripts\check_modal.py --verbose
```

Sortie attendue `OK — Worker accessible` ou message d'erreur explicite (ex. `Modal non connecté — vérifiez modal token info`).

### 4.3 Via Modal CLI (optionel)

```powershell
modal app list
modal volume ls studio-ltx25-models
```

Si le worker répond à `hydrate`, la génération facturante est prête.

---

## 5. Tester le pilote Fruit City (1 prise 5 s)

Une fois les étapes 2–4 vertes, lancez **uniquement le préflight** (0 €) :

```powershell
cd studio-v2
phase3_prepare.bat "C:\chemin\vers\fruit_zeste.png"
```

Puis, après validation visuelle de l'image et accord humain explicite, **une seule prise** :

```powershell
phase3_run_authorized.bat "C:\chemin\vers\fruit_zeste.png" 0.50
# demande confirmation OUI, 1 prise max, aucun retry, log dans COSTS.md
```

---

## 6. Dépannage

| Symptôme | Cause probable | Action |
|---|---|---|
| `Volume not found` | Volume non créé sur ce compte | `modal volume create studio-ltx25-models` |
| `Poids LTX absents` au déploiement | Un fichier manque dans le volume | `modal run studio-v2/deploy/modal_download_ltx.py --verify-only` puis re-DL le fichier |
| `Modal non connecté` dans /api/modal/status | Token expiré ou mauvais compte | `modal token new` puis `modal token info` |
| `ComfyUI arrêté au démarrage` | Mauvais poids ou ComfyUI révision mismatch | Vérifier `LTX_COMFYUI_REVISION` et les 5 noms de fichiers |
| Coût estimé `None` dans l'UI | Tarif GPU à None dans config.json | Vérifier `gpu_usd_per_sec` dans `studio-v2/config.json` |

---

## 7. Réversibilité

Le nouveau compte `kulejean567` est totalement isolé de `falconapp-cloud` (ancien compte). L'ancien volume et l'ancienne app restent intacts. Pour revenir à l'ancien compte, changez simplement de profil Modal (`modal token --profile`).

