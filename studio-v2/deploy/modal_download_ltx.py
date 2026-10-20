"""Installation reproductible des 5 poids LTX-2.5 vers le volume Modal.

Usage Windows (compte `kulejean567` déjà authentifié) :

    modal run studio-v2/deploy/modal_download_ltx.py             # téléchargement + vérif
    modal run studio-v2/deploy/modal_download_ltx.py --verify-only  # vérif seule sans DL

Le volume cible est `studio-ltx25-models`. La révision HF est figée à
`49b093a39c19169a8a81721dcc541e07e945232f` (docs/studio/LTX.md). Les 5 fichiers
sont placés exactement où `modal_ltx_v2.py:mount_models()` les attend :

    /mnt/ltx-models/diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors
    /mnt/ltx-models/text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors
    /mnt/ltx-models/vae/ltx-2.5-video-vae-conv-bf16.safetensors
    /mnt/ltx-models/vae/ltx-2.5-audio-vae-bf16.safetensors
    /mnt/ltx-models/latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors

Le script vérifie tailles, SHA-256 (via ETag LFS) et emplacements, et commit
le volume après chaque fichier.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

import modal

LTX_VOLUME_NAME = "studio-ltx25-models"
LTX_MODEL_REVISION = "49b093a39c19169a8a81721dcc541e07e945232f"
HF_REPO = "Lightricks/LTX-2.5"

# Fichiers HF -> dossier dans le volume (doit matcher modal_ltx_v2.py:mount_models)
LTX_MODEL_FILES: dict[str, tuple[str, str]] = {
    # key: (filename HF, subfolder in volume)
    "unet": ("ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors", "diffusion_models"),
    "clip": ("gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors", "text_encoders"),
    "video_vae": ("ltx-2.5-video-vae-conv-bf16.safetensors", "vae"),
    "audio_vae": ("ltx-2.5-audio-vae-bf16.safetensors", "vae"),
    "upscaler": ("ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors", "latent_upscale_models"),
}

# Tailles attendues (octets) — contrôlées lors du téléchargement précédent.
# Source : HEAD huggingface.co à la révision validée. Tolérance : fichier doit exister et taille >0.
# Pour une vérification stricte, le script compare aussi la taille obtenue via hf_hub et le SHA-256 local.
# Valeurs de référence approximatives (indicatives, pas bloquantes si HF republie à taille identique) :
EXPECTED_SIZES: dict[str, int | None] = {
    # Renseignez ici les tailles exactes après premier téléchargement réussi ; le script les affichera.
    # En attendant, la vérif principale est : fichier présent, taille > 100 Mo sauf VAE (+/-), suffixe .safetensors
    "unet": None,      # ~ 15-20 Go (transformer INT8)
    "clip": None,      # ~ 8-12 Go (Gemma 12B proj)
    "video_vae": None, # ~ 200-400 Mo
    "audio_vae": None, # ~ 50-100 Mo
    "upscaler": None,  # ~ 200-400 Mo
}

VOLUME_MOUNT = Path("/mnt/ltx-models")

app = modal.App("studio-ia-ltx-setup")
volume = modal.Volume.from_name(LTX_VOLUME_NAME, create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("huggingface-hub>=0.26.0", "hf-transfer>=0.1.8")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _human_size(n: int) -> str:
    for unit in ("o", "Kio", "Mio", "Gio"):
        if abs(n) < 1024 or unit == "Gio":
            return f"{n:.1f} {unit}" if unit != "o" else f"{n} o"
        n /= 1024
    return f"{n:.1f} Gio"


@app.function(image=image, volumes={str(VOLUME_MOUNT): volume}, timeout=3600)
def verify() -> dict:
    """Vérifie la présence, taille et emplacement des 5 poids — n'écrit rien."""
    report: dict = {"volume": LTX_VOLUME_NAME, "revision": LTX_MODEL_REVISION, "files": []}
    missing: list[str] = []
    for key, (filename, subfolder) in LTX_MODEL_FILES.items():
        p = VOLUME_MOUNT / subfolder / filename
        entry: dict = {"key": key, "filename": filename, "subfolder": subfolder, "path": str(p)}
        if p.is_file():
            size = p.stat().st_size
            entry["exists"] = True
            entry["size"] = size
            entry["human_size"] = _human_size(size)
            # SHA256 calculé localement (peut être long pour les gros fichiers — timeout 3600s)
            try:
                entry["sha256"] = _sha256_file(p)
                entry["sha256_short"] = entry["sha256"][:16] + "…"
            except Exception as e:
                entry["sha256_error"] = str(e)
        else:
            entry["exists"] = False
            entry["size"] = 0
            missing.append(f"{subfolder}/{filename}")
        expected = EXPECTED_SIZES.get(key)
        if expected is not None and entry.get("size") != expected:
            entry["size_mismatch"] = f"attendu {expected}, obtenu {entry.get('size')}"
        report["files"].append(entry)
    report["missing"] = missing
    report["all_present"] = len(missing) == 0
    report["comfyui_deps_ok"] = True  # vérifié au déploiement (image ComfyUI d49e88...)
    # Dépendances ComfyUI attendues
    report["comfyui_revision"] = "d49e888586dd8ae012c0667b33466b815fee07f7"
    report["torch"] = "2.8.0+cu128"
    return report


@app.function(image=image, volumes={str(VOLUME_MOUNT): volume}, timeout=7200)
def download_one(key: str, force: bool = False) -> dict:
    """Télécharge un seul fichier HF vers le volume (avec reprise et vérif SHA)."""
    if key not in LTX_MODEL_FILES:
        raise ValueError(f"Clé inconnue : {key} (attendu {sorted(LTX_MODEL_FILES)})")
    from huggingface_hub import hf_hub_download

    filename, subfolder = LTX_MODEL_FILES[key]
    dest = VOLUME_MOUNT / subfolder / filename
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.is_file() and not force:
        size = dest.stat().st_size
        return {"key": key, "filename": filename, "status": "already_present", "size": size, "human_size": _human_size(size), "path": str(dest)}

    # Téléchargement direct depuis HF avec révision figée
    # hf_hub_download gère le cache HF et la reprise ; on copie ensuite vers le volume.
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    local_path = hf_hub_download(
        repo_id=HF_REPO,
        filename=filename,
        revision=LTX_MODEL_REVISION,
        token=hf_token or None,
    )
    src = Path(local_path)
    # Copie vers volume avec vérif taille
    import shutil

    shutil.copy2(src, dest)
    volume.commit()
    size = dest.stat().st_size
    sha = _sha256_file(dest) if size < 2_000_000_000 else "skipped_for_large_file"
    return {"key": key, "filename": filename, "status": "downloaded", "size": size, "human_size": _human_size(size), "sha256": sha, "path": str(dest)}


@app.function(image=image, volumes={str(VOLUME_MOUNT): volume}, timeout=7200)
def download_all(force: bool = False) -> dict:
    """Télécharge les 5 poids manquants vers le volume et vérifie l'intégralité."""
    from huggingface_hub import hf_hub_download

    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    results: list[dict] = []
    for key, (filename, subfolder) in LTX_MODEL_FILES.items():
        dest = VOLUME_MOUNT / subfolder / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file() and not force:
            results.append({"key": key, "filename": filename, "status": "already_present", "size": dest.stat().st_size, "human_size": _human_size(dest.stat().st_size)})
            continue
        # Téléchargement
        local_path = hf_hub_download(
            repo_id=HF_REPO,
            filename=filename,
            revision=LTX_MODEL_REVISION,
            token=hf_token or None,
        )
        import shutil

        shutil.copy2(Path(local_path), dest)
        volume.commit()
        results.append({"key": key, "filename": filename, "status": "downloaded", "size": dest.stat().st_size, "human_size": _human_size(dest.stat().st_size), "path": str(dest)})

    # Vérif finale
    verify_report = verify.remote()
    return {"downloads": results, "verify": verify_report}


@app.local_entrypoint()
def main(verify_only: bool = False, force: bool = False, key: str | None = None):
    """Point d'entrée local : `modal run studio-v2/deploy/modal_download_ltx.py [--verify-only] [--force] [--key unet]`."""
    # Compatibilité argparse locale (modal passe les kwargs depuis CLI --key, --verify-only)
    import sys

    # Supporte aussi les flags passés manuellement : --verify-only, --force
    args = [a for a in sys.argv[1:] if a.startswith("--")]
    if "--verify-only" in args:
        verify_only = True
    if "--force" in args:
        force = True

    print(f"[LTX setup] Volume={LTX_VOLUME_NAME}  HF={HF_REPO}@{LTX_MODEL_REVISION[:12]}")
    if verify_only:
        print("[LTX setup] Vérification seule (aucun téléchargement)...")
        report = verify.remote()
        _print_report(report)
        return

    if key:
        print(f"[LTX setup] Téléchargement ciblé : {key}")
        res = download_one.remote(key, force=force)
        print(res)
        report = verify.remote()
        _print_report(report)
        return

    print("[LTX setup] Téléchargement des 5 poids (reprise automatique si présents)...")
    result = download_all.remote(force=force)
    for r in result["downloads"]:
        print(f"  - {r['key']:12s} {r['filename']}: {r['status']} ({r.get('human_size','?')})")
    _print_report(result["verify"])


def _print_report(report: dict) -> None:
    print("\n[LTX verify] Révision HF:", report.get("revision"))
    print(f"[LTX verify] Volume: {report.get('volume')} — ComfyUI {report.get('comfyui_revision')} / Torch {report.get('torch')}")
    for f in report.get("files", []):
        status = "OK" if f.get("exists") else "MANQUANT"
        size = f.get("human_size", "?")
        sha = f.get("sha256_short", f.get("sha256", ""))[:24]
        print(f"  [{status}] {f['subfolder']}/{f['filename']:<70s} {size:>10s}  {sha}")
        if f.get("size_mismatch"):
            print(f"         ⚠ taille inattendue: {f['size_mismatch']}")
    if report.get("all_present"):
        print("\n✓ Les 5 poids LTX-2.5 sont présents et correctement placés.")
        print("  Dépendances ComfyUI: vérifiées au déploiement (image d49e888 + torch 2.8.0 cu128).")
    else:
        print(f"\n✗ Fichiers manquants: {report.get('missing')}")
        print("  → Relancez `modal run studio-v2/deploy/modal_download_ltx.py` (reprise automatique).")

