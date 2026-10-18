from __future__ import annotations

import base64
import io

import modal

app = modal.App("studio-ia-voxcpm")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install(
        "torch>=2.5.0",
        "torchaudio>=2.5.0",
        "voxcpm",
        "soundfile",
        "numpy",
        "huggingface-hub",
    )
    .env({"HF_HOME": "/models/hf"})
)

models = modal.Volume.from_name("voxcpm-models", create_if_missing=True)
_MODEL = None


def _load_model():
    global _MODEL
    if _MODEL is None:
        from voxcpm import VoxCPM
        _MODEL = VoxCPM.from_pretrained(
            "openbmb/VoxCPM2", load_denoiser=False, device="cuda"
        )
        models.commit()
    return _MODEL


@app.function(
    image=image,
    gpu="L4",
    cpu=4,
    memory=16384,
    timeout=900,
    scaledown_window=180,
    min_containers=0,
    max_containers=1,
    volumes={"/models": models},
)
def synthesize(payload: dict) -> dict:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("Texte TTS vide.")
    voice = str(payload.get("voice") or "").strip()
    reference = str(payload.get("reference_base64") or "").strip()
    model = _load_model()
    options = {
        "cfg_value": float(payload.get("cfg_value", 2.0)),
        "inference_timesteps": int(payload.get("inference_timesteps", 10)),
        "retry_badcase": True,
    }
    if reference:
        from pathlib import Path
        ref_path = Path("/tmp/tts-reference.wav")
        ref_path.write_bytes(base64.b64decode(reference))
        wav = model.generate(text=text, reference_wav_path=str(ref_path), **options)
    else:
        prompt = f"({voice}){text}" if voice else text
        wav = model.generate(text=prompt, **options)
    import soundfile as sf
    buffer = io.BytesIO()
    sf.write(buffer, wav, model.tts_model.sample_rate, format="WAV")
    return {
        "audio_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "sample_rate": int(model.tts_model.sample_rate),
    }
