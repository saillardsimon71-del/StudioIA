from __future__ import annotations

import base64
import math
import re

H3_COMFYUI_REVISION = "d49e888586dd8ae012c0667b33466b815fee07f7"
H3_VOLUME_NAME = "fasth3-models"
H3_MODEL_FILES = {
    "unet": "fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors",
    "clip": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "video_vae": "minimax_h3_video_vae_int8_convrot.safetensors",
    "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
}
H3_SIZES = {"16:9": (832, 480), "9:16": (480, 832), "1:1": (640, 640)}
H3_FPS = 24
H3_STEPS = 8
H3_QUALITY_MODEL_FILE = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
H3_PROFILES = {
    "fast": {"label": "MiniMax H3 / FastH3 V2", "model_file": H3_MODEL_FILES["unet"],
             "steps": H3_STEPS, "sizes": H3_SIZES},
    "quality": {"label": "MiniMax H3 / H3-Base FL2VA", "model_file": H3_QUALITY_MODEL_FILE,
                "steps": 25, "sizes": {"16:9": (1344, 768), "9:16": (768, 1344), "1:1": (992, 992)}},
    "ltx": {"label": "LTX-2.5 Distilled", "model_file": "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
            "steps": 8, "sizes": {"16:9": (1344, 768), "9:16": (768, 1344), "1:1": (1024, 1024)}},
}
H3_PROGRESS_TIMEOUT_SEC = 3
H3_MIN_FRAMES = 124
H3_MAX_FRAMES = 362


def h3_profile(name="fast"):
    if name not in H3_PROFILES:
        raise ValueError("Mode vidéo H3 invalide.")
    return H3_PROFILES[name]


def frames_for_duration(seconds: float, video_profile="fast") -> int:
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("La durée H3 doit être positive et finie.")
    if video_profile == "ltx":
        requested = max(25, math.ceil(seconds * H3_FPS))
        frames = requested + (1 - requested) % 8
        if frames > 361:
            raise ValueError("La durée LTX dépasse la plage prise en charge (15,041 s).")
        return frames
    requested = max(H3_MIN_FRAMES, math.ceil(seconds * H3_FPS))
    frames = requested + (5 - requested) % 17
    if frames > H3_MAX_FRAMES:
        raise ValueError("La durée H3 dépasse la plage prise en charge (15,083 s).")
    return frames


def validate_request(payload: dict) -> None:
    required = {
        "contract_version",
        "prompt",
        "requested_duration_sec",
        "duration_sec",
        "aspect_ratio",
        "width",
        "height",
        "fps",
        "frames",
        "steps",
        "seed",
        "audio_mode",
        "continuity_mode",
    }
    if not isinstance(payload, dict) or not required <= payload.keys():
        raise ValueError("Requête H3 incomplète.")
    if payload.keys() - required - {"image_base64", "trace_id", "video_profile"}:
        raise ValueError("La requête H3 contient des paramètres inconnus.")
    if "trace_id" in payload and (
        not isinstance(payload["trace_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", payload["trace_id"])
    ):
        raise ValueError("Identifiant de trace H3 invalide.")
    if payload["contract_version"] != 1:
        raise ValueError("Version du contrat H3 invalide.")
    if not isinstance(payload["prompt"], str) or not payload["prompt"].strip():
        raise ValueError("Le prompt H3 est vide.")
    profile = h3_profile(payload.get("video_profile", "fast"))
    aspect = payload["aspect_ratio"]
    if aspect not in profile["sizes"] or (payload["width"], payload["height"]) != profile["sizes"][aspect]:
        raise ValueError("Format H3 invalide.")
    if payload["fps"] != H3_FPS or payload["steps"] != profile["steps"]:
        raise ValueError("Les étapes et la cadence ne correspondent pas au modèle H3 choisi.")
    frames = payload["frames"]
    if type(frames) is not int or frames != frames_for_duration(payload["requested_duration_sec"], payload.get("video_profile", "fast")):
        raise ValueError("Frames invalides pour le modèle vidéo choisi.")
    duration = payload["duration_sec"]
    if type(duration) not in (int, float) or not math.isclose(
        duration, frames / H3_FPS, rel_tol=0, abs_tol=1e-6
    ):
        raise ValueError("La durée H3 doit correspondre aux frames à 24 fps.")
    if type(payload["seed"]) is not int or not 0 <= payload["seed"] < 2**64:
        raise ValueError("La seed H3 doit être un entier non signé sur 64 bits.")
    if payload["audio_mode"] not in ("native", "silent"):
        raise ValueError("Mode audio H3 invalide.")
    image = payload.get("image_base64")
    if image is None:
        if payload["continuity_mode"] != "none":
            raise ValueError("Une référence H3 est requise pour ce mode.")
    else:
        if not isinstance(image, str) or not image:
            raise ValueError("Image H3 base64 invalide.")
        try:
            content = base64.b64decode(image, validate=True)
        except ValueError as exc:
            raise ValueError("Image H3 base64 invalide.") from exc
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("L'image H3 doit être au format PNG.")
        if payload["continuity_mode"] not in ("image", "last_frame"):
            raise ValueError("Mode de continuité H3 invalide.")


def build_h3_graph(payload: dict, *, output_prefix: str, uploaded_image: str | None = None) -> dict:
    validate_request(payload)
    if payload.get("video_profile") == "ltx":
        raise ValueError("Cette requête nécessite le worker LTX.")
    profile = h3_profile(payload.get("video_profile", "fast"))
    if not output_prefix or output_prefix.startswith("/") or ".." in output_prefix.split("/"):
        raise ValueError("Préfixe de sortie H3 invalide.")
    if bool(payload.get("image_base64")) != bool(uploaded_image):
        raise ValueError("La référence H3 doit correspondre à l'image chargée.")
    graph = {
        "6": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": profile["model_file"], "weight_dtype": "default"},
        },
        "13": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": H3_MODEL_FILES["clip"], "type": "minimax", "device": "default"},
        },
        "11": {"class_type": "VAELoader", "inputs": {"vae_name": H3_MODEL_FILES["video_vae"]}},
        "143": {
            "class_type": "MiniMaxH3SigmaShift",
            "inputs": {"model": ["6", 0], "shift_video": 10, "shift_audio": 3},
        },
        "128": {
            "class_type": "ModelAttentionBackend",
            "inputs": {"model": ["143", 0], "attention": "comfy kitchen attention"},
        },
        "127": {
            "class_type": "BlockSparseAttention",
            "inputs": {
                "model": ["128", 0],
                "selection": "vsa",
                "selection.keep_percent": 10,
                "start_percent": 0.2,
                "end_percent": 1,
                "dense_blocks": "",
                "min_tokens": 12288,
                "extra_tokens": 256,
                "sink_conditioning": "exact_kv_and_rows",
                "verbose": False,
            },
        },
        "104": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "clip": ["13", 0],
                "vae": ["11", 0],
                "prompt": payload["prompt"],
                "width": payload["width"],
                "height": payload["height"],
                "length": payload["frames"],
            },
        },
        "9": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["127", 0],
                "scheduler": "simple",
                "steps": profile["steps"],
                "denoise": 1.0,
            },
        },
        "15": {"class_type": "RandomNoise", "inputs": {"noise_seed": payload["seed"]}},
        "17": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "16": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["127", 0], "conditioning": ["104", 0]},
        },
        "14": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["15", 0],
                "guider": ["16", 0],
                "sampler": ["17", 0],
                "sigmas": ["9", 0],
                "latent_image": ["104", 1],
            },
        },
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["11", 0]}},
        "91": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["10", 0],
                "fps": H3_FPS,
                "bit_depth": "auto",
                "color_space": "sRGB",
                "codec": "none",
            },
        },
        "92": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["91", 0],
                "filename_prefix": output_prefix,
                "format": "auto",
                "codec": "auto",
            },
        },
    }
    if payload.get("video_profile") == "quality":
        for key in ("143", "128", "127"):
            graph.pop(key)
        graph["9"]["inputs"]["model"] = ["6", 0]
        graph["16"]["inputs"]["model"] = ["6", 0]
    if uploaded_image:
        graph["1"] = {"class_type": "LoadImage", "inputs": {"image": uploaded_image}}
        graph["104"]["inputs"]["first_frame"] = ["1", 0]
    if payload["audio_mode"] == "native":
        graph["24"] = {
            "class_type": "VAELoader",
            "inputs": {"vae_name": H3_MODEL_FILES["audio_vae"]},
        }
        graph["23"] = {
            "class_type": "VAEDecodeAudio",
            "inputs": {"samples": ["14", 0], "vae": ["24", 0]},
        }
        graph["91"]["inputs"]["audio"] = ["23", 0]
    return graph
