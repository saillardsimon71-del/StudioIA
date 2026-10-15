import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from calliope.config import settings


def _executable():
    installed = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/OpenAI/Codex/bin/codex.exe"
    executable = str(installed) if installed.is_file() else shutil.which("codex")
    if not executable:
        raise ValueError("Installez Codex et connectez-le avec votre compte ChatGPT pour générer les images.")
    return executable


def _environment():
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    env.pop("CODEX_API_KEY", None)
    return env


def check_available():
    try:
        result = subprocess.run(
            [_executable(), "--no-daemon", "login", "status"],
            capture_output=True, text=True, timeout=15, env=_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        message = (result.stdout + result.stderr).lower()
        if result.returncode == 0 and "chatgpt" in message:
            return {"ok": True, "message": "Codex connecté via ChatGPT. Les images utilisent votre abonnement."}
        return {"ok": False, "message": "Connectez Codex avec votre compte ChatGPT, puis réessayez."}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "message": str(exc)}


def _stop(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        process.kill()
    process.wait(timeout=15)


def _generated_image(result, started):
    if result.get("status") != "done":
        raise ValueError("Codex n'a pas généré l'image : " + str(result.get("error") or "aucun résultat"))
    path = Path(result.get("image_path") or "").resolve()
    root = (Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "generated_images").resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Codex n'a pas retourné un fichier de son générateur d'images.")
    stat = path.stat()
    if stat.st_mtime < started - 2 or not 0 < stat.st_size <= 25 * 1024 * 1024:
        raise ValueError("Le fichier retourné par Codex est ancien, vide ou trop volumineux.")
    return path


async def generate(request, *, check_cancel=None):
    if check_cancel:
        await check_cancel()
    available = await asyncio.to_thread(check_available)
    if not available["ok"]:
        raise ValueError(available["message"])
    directory = settings.data_dir / "codex-images" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["status", "image_path", "error"],
        "properties": {
            "status": {"type": "string", "enum": ["done", "failed"]},
            "image_path": {"type": "string"}, "error": {"type": "string"},
        },
    }
    (directory / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
    prompt = (
        "$imagegen\nGenerate exactly one image using the built-in Codex image generation tool "
        "and the signed-in ChatGPT subscription. Do not use an API, SDK, browser, shell drawing, "
        "or a paid API key. Do not edit files or copy the generated image. Return the absolute "
        "local image path returned by the native tool, status done, and an empty error. "
        "If the native tool is unavailable, return status failed and explain the failure. "
        "Follow the supplied image prompt without rewriting the scene. Attached reference images "
        "are ordered exactly as numbered in the prompt.\n"
        f"Aspect ratio: {request['aspect_ratio']}.\nImage prompt:\n{request['prompt']}"
    )
    (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
    command = [
        _executable(), "--no-daemon", "-a", "never", "exec", "--ignore-user-config",
        "--skip-git-repo-check", "--ephemeral", "--json", "--enable", "image_generation",
        "--sandbox", "read-only", "--cd", str(directory),
        "--output-schema", str(directory / "schema.json"),
        "--output-last-message", str(directory / "result.json"),
    ]
    for reference in request.get("reference_paths", []):
        command.extend(["--image", reference])
    command.append("-")
    started = time.time()
    deadline = time.monotonic() + 600
    with (directory / "prompt.txt").open("rb") as stdin, \
         (directory / "events.jsonl").open("wb") as stdout, \
         (directory / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen(
            command, stdin=stdin, stdout=stdout, stderr=stderr, env=_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            while process.poll() is None:
                if check_cancel:
                    await check_cancel()
                if time.monotonic() >= deadline:
                    raise ValueError("Codex n'a pas terminé l'image après 10 minutes. Réessayez cette prise.")
                await asyncio.sleep(1)
            if check_cancel:
                await check_cancel()
            result_path = directory / "result.json"
            if not result_path.is_file():
                raise ValueError("Codex a interrompu la génération. Vérifiez sa connexion ou ses limites d'utilisation.")
            result = json.loads(result_path.read_text(encoding="utf-8"))
            path = _generated_image(result, started)
            return path.read_bytes(), {
                "provider": "codex", "codex_image_path": str(path), "codex_run_dir": str(directory),
            }
        finally:
            await asyncio.to_thread(_stop, process)
