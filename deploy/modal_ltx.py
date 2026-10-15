from pathlib import Path

import modal
from calliope.engines.h3_graph import H3_COMFYUI_REVISION
from calliope.engines.ltx_graph import LTX_MODEL_FILES, LTX_MODEL_REVISION, LTX_VIDEO_CFG, LTX_VOLUME_NAME, build_ltx_graph

app = modal.App("studio-ia-ltx")
models_volume = modal.Volume.from_name(LTX_VOLUME_NAME, create_if_missing=False).with_mount_options(read_only=True)
progress_store = modal.Dict.from_name("studio-ia-ltx-progress", create_if_missing=True)
models_root = Path("/mnt/ltx-models")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ffmpeg", "libgl1", "libglib2.0-0", "libsm6", "libxext6")
    .pip_install("torch==2.8.0", index_url="https://download.pytorch.org/whl/cu128")
    .run_commands(
        "git init /opt/ComfyUI",
        "git -C /opt/ComfyUI remote add origin https://github.com/Comfy-Org/ComfyUI.git",
        f"git -C /opt/ComfyUI fetch --depth 1 origin {H3_COMFYUI_REVISION}",
        f"git -C /opt/ComfyUI checkout --detach {H3_COMFYUI_REVISION}",
        "pip install --no-cache-dir -r /opt/ComfyUI/requirements.txt torch==2.8.0 --extra-index-url https://download.pytorch.org/whl/cu128",
    )
    .env({"NEWSREEL_COMFYUI_DIR": "/opt/ComfyUI"})
    .add_local_python_source("calliope.engines")
    .add_local_file(Path(__file__).with_name("modal_h3.py"), "/root/modal_h3.py")
)


def mount_models():
    folders = {"unet": "diffusion_models", "clip": "text_encoders",
               "video_vae": "vae", "audio_vae": "vae", "upscaler": "latent_upscale_models"}
    for key, folder in folders.items():
        source = models_root / folder / LTX_MODEL_FILES[key]
        if not source.is_file():
            raise RuntimeError(f"Poids LTX absents : {source.name}")
        target = Path("/opt/ComfyUI/models") / folder / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() and target.resolve() == source.resolve():
            continue
        if target.exists() or target.is_symlink():
            raise RuntimeError(f"Refus de remplacer les poids : {target.name}")
        target.symlink_to(source)


@app.function(image=image, gpu="RTX-PRO-6000", cpu=2, memory=32768, timeout=5400,
              min_containers=0, max_containers=1, scaledown_window=2,
              volumes={str(models_root): models_volume})
def render_clip(payload):
    import modal_h3 as runtime
    runtime._mount_volume_models = mount_models
    runtime.build_h3_graph = build_ltx_graph
    runtime.progress_store = progress_store
    result = runtime._run_clip(payload)
    result["metadata"].update(model_revision=LTX_MODEL_REVISION, refinement_steps=3,
                              video_cfg=LTX_VIDEO_CFG, audio_cfg=1.0)
    return result
