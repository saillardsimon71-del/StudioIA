"""Définition autonome du worker Modal LTX-2.5 pour Studio IA V2 (sans aucune dépendance à V1).

Note : si l'application Modal `studio-ia-ltx` (`render_clip`) est déjà déployée sur le compte
Modal de l'opérateur, Studio IA V2 l'utilise directement sans redéploiement nécessaire.
Ce fichier permet de redéployer `studio-ia-ltx` depuis `studio-v2/` en totale autonomie
(`modal deploy studio-v2/deploy/modal_ltx_v2.py`) si souhaité.
"""

from __future__ import annotations

from pathlib import Path

LTX_VOLUME_NAME = "studio-ltx25-models"
LTX_MODEL_REVISION = "49b093a39c19169a8a81721dcc541e07e945232f"
LTX_COMFYUI_REVISION = "d49e888586dd8ae012c0667b33466b815fee07f7"
LTX_VIDEO_CFG = 1.0
LTX_NEGATIVE_PROMPT = "background music, subtitles, text overlays, voiceover narration"
LTX_MODEL_FILES = {
    "unet": "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
    "clip": "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
    "video_vae": "ltx-2.5-video-vae-conv-bf16.safetensors",
    "audio_vae": "ltx-2.5-audio-vae-bf16.safetensors",
    "upscaler": "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
}


def build_ltx_comfy_graph(payload: dict, *, output_prefix: str, uploaded_image: str | None = None) -> dict:
    """Construit le graphe ComfyUI LTX-2.5 deux passes (distilled 8 steps + spatial upsampler x2)."""
    def node(kind: str, **inputs):
        return {"class_type": kind, "inputs": inputs}

    graph = {
        "6": node("UNETLoader", unet_name=LTX_MODEL_FILES["unet"], weight_dtype="default"),
        "11": node("VAELoader", vae_name=LTX_MODEL_FILES["video_vae"]),
        "24": node("VAELoader", vae_name=LTX_MODEL_FILES["audio_vae"]),
        "13": node("CLIPLoader", clip_name=LTX_MODEL_FILES["clip"], type="ltxv", device="default"),
        "20": node("CLIPTextEncode", clip=["13", 0], text=payload["prompt"]),
        "21": node("CLIPTextEncode", clip=["13", 0], text=LTX_NEGATIVE_PROMPT),
        "22": node("LTXVConditioning", positive=["20", 0], negative=["21", 0], frame_rate=payload["fps"]),
        "30": node(
            "EmptyLTXVLatentVideo",
            width=payload["width"] // 2,
            height=payload["height"] // 2,
            length=payload["frames"],
            batch_size=1,
        ),
        "32": node(
            "LTXVEmptyLatentAudio",
            audio_vae=["24", 0],
            frames_number=payload["frames"],
            frame_rate=payload["fps"],
            batch_size=1,
        ),
        "33": node("LTXVConcatAVLatent", video_latent=["30", 0], audio_latent=["32", 0]),
        "15": node("RandomNoise", noise_seed=payload["seed"]),
        "16": node(
            "LTXVDualCFGGuider",
            model=["6", 0],
            positive=["22", 0],
            negative=["22", 1],
            video_cfg=LTX_VIDEO_CFG,
            audio_cfg=1.0,
        ),
        "17": node("KSamplerSelect", sampler_name="euler_ancestral"),
        "9": node("ManualSigmas", sigmas="1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"),
        "14": node(
            "SamplerCustomAdvanced",
            noise=["15", 0],
            guider=["16", 0],
            sampler=["17", 0],
            sigmas=["9", 0],
            latent_image=["33", 0],
        ),
        "34": node("LTXVSeparateAVLatent", av_latent=["14", 0]),
        "35": node("LatentUpscaleModelLoader", model_name=LTX_MODEL_FILES["upscaler"]),
        "36": node("LTXVLatentUpsampler", samples=["34", 0], upscale_model=["35", 0], vae=["11", 0]),
        "38": node("LTXVConcatAVLatent", video_latent=["36", 0], audio_latent=["34", 1]),
        "39": node("RandomNoise", noise_seed=42),
        "40": node("ManualSigmas", sigmas="0.85, 0.7250, 0.4219, 0.0"),
        "41": node(
            "SamplerCustomAdvanced",
            noise=["39", 0],
            guider=["16", 0],
            sampler=["17", 0],
            sigmas=["40", 0],
            latent_image=["38", 0],
        ),
        "42": node("LTXVSeparateAVLatent", av_latent=["41", 0]),
        "10": node(
            "VAEDecodeTiled",
            samples=["42", 0],
            vae=["11", 0],
            tile_size=512,
            overlap=64,
            temporal_size=64,
            temporal_overlap=16,
        ),
        "91": node("CreateVideo", images=["10", 0], fps=payload["fps"]),
        "92": node("SaveVideo", video=["91", 0], filename_prefix=output_prefix, format="auto", codec="auto"),
    }
    if uploaded_image:
        graph["1"] = node("LoadImage", image=uploaded_image)
        graph["2"] = node("LTXVPreprocess", image=["1", 0], img_compression=18)
        graph["31"] = node("LTXVImgToVideoInplace", vae=["11", 0], image=["2", 0], latent=["30", 0], strength=0.7, bypass=False)
        graph["33"]["inputs"]["video_latent"] = ["31", 0]
        graph["37"] = node("LTXVImgToVideoInplace", vae=["11", 0], image=["2", 0], latent=["36", 0], strength=1.0, bypass=False)
        graph["38"]["inputs"]["video_latent"] = ["37", 0]
    if payload.get("audio_mode") == "native":
        graph["23"] = node("LTXVAudioVAEDecode", samples=["42", 1], audio_vae=["24", 0])
        graph["91"]["inputs"]["audio"] = ["23", 0]
    return graph


MODELS_MOUNT = Path("/mnt/ltx-models")
