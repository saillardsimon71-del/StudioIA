from __future__ import annotations

import base64
import importlib


def _load_modal():
    try:
        return importlib.import_module("modal")
    except ImportError as exc:
        raise ImportError("Modal non installe.") from exc


class ModalTTSEngine:
    def __init__(self, app_name: str, function_name: str):
        self.app_name = app_name
        self.function_name = function_name
        self._function_handle = None

    def _function(self):
        if self._function_handle is None:
            self._function_handle = _load_modal().Function.from_name(
                self.app_name, self.function_name
            )
        return self._function_handle

    def check_available(self) -> dict:
        try:
            self._function().hydrate()
        except ImportError:
            return {"ok": False, "message": "Le client Modal non installe."}
        except Exception:
            return {
                "ok": False,
                "message": "Modal TTS inaccessible: authentification ou deploiement a verifier.",
            }
        return {"ok": True, "message": "Service TTS accessible."}

    def synthesize(self, text, voice, cfg_value=2.0, inference_timesteps=10, reference=None) -> bytes:
        payload = {
            "text": text,
            "voice": voice,
            "cfg_value": cfg_value,
            "inference_timesteps": inference_timesteps,
        }
        if reference:
            payload["reference_base64"] = base64.b64encode(reference).decode("ascii")
        result = self._function().remote(payload)
        if not isinstance(result, dict) or not result.get("audio_base64"):
            raise RuntimeError("Le worker TTS a renvoye un resultat invalide.")
        return base64.b64decode(result["audio_base64"])
