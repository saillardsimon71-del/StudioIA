"""Tests fonctionnels réels de Studio IA V2 (sans GPU payant).

Vérifie de bout en bout :
1. Isolation stricte vis-à-vis de V1 (fichiers Git `c395532` intacts, aucun import `calliope`, ports isolés).
2. Source unique de vérité Pydantic V2 (`Project`, `Character`, `DialogueLine`, `Shot`, `DisplayOverlay`).
3. Appel unique DeepSeek (succès + erreur JSON invalide sans aucun 2e appel caché).
4. Arrêt net avant GPU si prompt LTX > 150 mots ou balise interdite (zéro chaîne de réparation).
5. Estimation de coût fournisseur honnête (`None` si tarif absent, recalcul sur durées observées),
   blocage sans autorisation explicite et respect strict du plafond.
6. Re-génération ciblée d'un seul plan sans écraser les autres plans, et conservation sur erreur GPU.
7. Export réel FFmpeg d'un court métrage vertical 15s (3 plans × 5s, 1080×1920, 24 fps, AAC 48 kHz stéréo)
   avec incrustation horodatée de chiffres fixes (`000 -> 001 -> 002`), raccord `last_frame`, et QC complet.
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

V2_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = V2_ROOT.parent
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))

from api.app import create_app  # noqa: E402
from core.config import V2Settings  # noqa: E402
from core.ltx_prompt import MAX_LTX_PROMPT_WORDS, PromptCapacityError, compile_shot_prompt, validate_ltx_prompt  # noqa: E402
from core.scenario import Character, DialogueLine, DisplayChange, DisplayOverlay, Project, Shot  # noqa: E402
from core.storage import StudioRepository  # noqa: E402
from engines.video import AuthorizationRequiredError, BudgetExceededError, LtxModalEngine  # noqa: E402
from qc.media import ffmpeg_exe, inspect_media  # noqa: E402
from qc.ocr import build_allowed_forms  # noqa: E402
from qc.speech import assess_speech, normalize_speech_tokens  # noqa: E402


def _make_png_bytes(color: tuple[int, int, int] = (240, 190, 40), size: tuple[int, int] = (768, 1344)) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_real_test_mp4(path: Path, duration_s: float = 5.0, color: str = "navy", freq: int = 440) -> Path:
    cmd = [
        ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=768x1344:r=24:d={duration_s}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:sample_rate=48000:duration={duration_s}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(path),
    ]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> V2Settings:
    costs_md = tmp_path / "COSTS.md"
    costs_md.write_text("# Test Costs\n", encoding="utf-8")
    cfg_path = tmp_path / "config.json"
    monkeypatch.setenv("STUDIO_V2_CONFIG", str(cfg_path))
    monkeypatch.setenv("STUDIO_V2_COSTS_MD", str(costs_md))
    settings = V2Settings(
        host="127.0.0.1",
        port=8270,
        data_dir=tmp_path / "data",
        deepseek_api_key="test-deepseek-key",
        ocr_enabled=False,
        whisper_model_or_path="",
    )
    settings.ensure_dirs()
    return settings


def test_1_strict_isolation_from_v1(isolated_settings: V2Settings):
    """Aucun fichier V1 n'est modifié/supprimé et aucun module `calliope` n'est importé."""
    diff = subprocess.run(
        ["git", "diff", "--diff-filter=MD", "--name-only", "c395532"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    assert diff.stdout.strip() == "", f"Fichiers V1 modifiés interdits : {diff.stdout}"
    assert not any(name == "calliope" or name.startswith("calliope.") for name in sys.modules)

    for v1_port in (8247, 8260):
        bad = V2Settings(port=v1_port, data_dir=isolated_settings.data_dir)
        with pytest.raises(ValueError, match="réservé à Studio IA V1"):
            bad.ensure_dirs()


def test_2_single_source_of_truth_and_fiction_anomalies():
    """Le modèle central accepte les anomalies volontaires de fiction mais bloque les IDs cassés."""
    proj = Project(
        id="fruit-compteur",
        idea="Un citron et une fraise voient un compteur monter tout seul sans raison logique.",
        characters=[
            Character(id="zeste", name="Zeste", description="Anthropomorphic yellow lemon in trench coat"),
            Character(id="gariguette", name="Gariguette", description="Anthropomorphic red strawberry in hoodie"),
        ],
        shots=[
            Shot(
                id="s1",
                duration_s=5.0,
                action="Close shot on table counter jumping from zero to nine while nobody moves.",
                dialogue=[DialogueLine(speaker_id="zeste", text="Personne a bougé… pourquoi ça monte ?!")],
                initial_state="Counter at zero",
                final_state="Counter suddenly shows nine for no physical reason",
                display_overlay=DisplayOverlay(
                    initial_text="000",
                    changes=[DisplayChange(at_sec=3.2, text="009")],
                ),
            )
        ],
    )
    assert len(proj.shots) == 1
    prompt = compile_shot_prompt(proj, proj.shots[0])
    count_check = validate_ltx_prompt(prompt, shot_id="s1")
    assert count_check["word_count"] <= MAX_LTX_PROMPT_WORDS
    assert "blank and dark" in prompt

    with pytest.raises(ValueError, match="locuteur inconnu"):
        Project(
            id="bad",
            idea="Test",
            characters=[Character(id="zeste", name="Zeste", description="Lemon")],
            shots=[
                Shot(
                    id="s1",
                    duration_s=5.0,
                    action="Action",
                    dialogue=[DialogueLine(speaker_id="fantome", text="Bonjour")],
                )
            ],
        )


def test_3_deepseek_single_call_and_no_auto_retry_on_error(isolated_settings: V2Settings):
    """Une tentative utilisateur = exactement 1 appel DeepSeek, même en cas de JSON invalide."""
    calls: list[dict] = []

    def bad_llm(req: dict) -> dict:
        calls.append(req)
        return {"choices": [{"message": {"content": "{json cassé non fermé"}}]}

    client_bad = TestClient(create_app(settings=isolated_settings, llm_transport=bad_llm))
    created = client_bad.post(
        "/api/projects",
        json={"title": "Test Erreur", "idea": "Deux fruits braquent un distributeur", "target_duration_s": 15},
    ).json()

    resp_err = client_bad.post(f"/api/projects/{created['id']}/generate-scenario", json={})
    assert resp_err.status_code == 422
    assert len(calls) == 1, "Interdiction absolue de relancer automatiquement le LLM !"

    def good_llm(req: dict) -> dict:
        calls.append(req)
        scenario = {
            "title": "Fruit City : Alerte au Frigo",
            "characters": [
                {"id": "zeste", "name": "Zeste", "description": "Grumpy yellow lemon in trench coat"},
                {"id": "momo", "name": "Momo", "description": "Nervous fuzzy peach wearing glasses"},
            ],
            "shots": [
                {
                    "id": "shot_1",
                    "duration_s": 5.0,
                    "action": "Medium shot, Zeste points at a glowing red box on the kitchen table.",
                    "initial_state": "Zeste and Momo face the closed box.",
                    "final_state": "Momo steps back nervously.",
                    "continue_from_previous": False,
                    "dialogue": [
                        {"speaker_id": "zeste", "text": "Touche pas à ce bouton, Momo !", "start_sec": 0.2},
                        {"speaker_id": "momo", "text": "Trop tard, j'ai déjà appuyé.", "start_sec": 2.4},
                    ],
                },
                {
                    "id": "shot_2",
                    "duration_s": 5.0,
                    "action": "Camera holds steady as kitchen lights flicker once around Zeste and Momo.",
                    "initial_state": "Momo freezes with finger near the box.",
                    "final_state": "Heavy knock sounds behind the fridge door.",
                    "continue_from_previous": True,
                    "dialogue": [
                        {"speaker_id": "zeste", "text": "Attends… qui vient de toquer DANS le frigo ?", "start_sec": 0.5}
                    ],
                },
            ],
        }
        return {
            "model": "deepseek-chat",
            "usage": {"prompt_tokens": 820, "completion_tokens": 410},
            "choices": [{"message": {"content": json.dumps(scenario, ensure_ascii=False)}}],
        }

    client_good = TestClient(create_app(settings=isolated_settings, llm_transport=good_llm))
    resp_ok = client_good.post(f"/api/projects/{created['id']}/generate-scenario", json={})
    assert resp_ok.status_code == 200
    assert len(calls) == 2  # 1er appel erroné + 1 appel explicite réussi
    saved = resp_ok.json()["project"]
    assert len(saved["shots"]) == 2
    assert all(0 < len(s["ltx_prompt"].split()) <= 150 for s in saved["shots"])


def test_4_prompt_over_150_words_stops_cleanly_before_gpu(isolated_settings: V2Settings, tmp_path: Path):
    """Un prompt > 150 mots est bloqué net AVANT tout appel au runner Modal."""
    modal_invocations = 0

    def forbidden_modal(_payload: dict) -> dict:
        nonlocal modal_invocations
        modal_invocations += 1
        raise AssertionError("Modal ne doit JAMAIS être appelé si le prompt dépasse 150 mots !")

    img_file = tmp_path / "ref.png"
    img_file.write_bytes(_make_png_bytes())

    overlong_shot = Shot(
        id="shot_long",
        duration_s=5.0,
        action="Walk forward.",
        image_ref=str(img_file),
        image_validated=True,
        ltx_prompt=" ".join(["word"] * 151),
    )
    engine = LtxModalEngine(isolated_settings, modal_runner=forbidden_modal)
    engine.arm_single_take(
        project_id="p1",
        shot_id="shot_long",
        authorized_by_human=True,
        max_budget_eur=5.0,
    )
    with pytest.raises(PromptCapacityError, match="151 mots"):
        engine.render(overlong_shot)
    assert modal_invocations == 0


def test_5_cost_estimation_honesty_authorization_and_ceiling(isolated_settings: V2Settings, tmp_path: Path):
    """Vérifie None sans tarif, calcul réel Modal, blocage sans autorisation et plafond."""
    repo = StudioRepository(isolated_settings.db_path)
    img_file = tmp_path / "ref.png"
    img_file.write_bytes(_make_png_bytes())

    shot = Shot(
        id="shot_1",
        duration_s=5.0,
        action="Zeste blinks once.",
        image_ref=str(img_file),
        image_validated=True,
        ltx_prompt='Vertical shot. Zeste says in a natural French voice: "Salut."',
    )

    # 1. Tarif inconnu -> estimate_cost DOIT retourner None (jamais un prix inventé)
    no_rate_cfg = isolated_settings.model_copy(update={"gpu_usd_per_sec": None})
    assert LtxModalEngine(no_rate_cfg, repo=repo).estimate_cost(shot) is None

    # 2. Tarif réel Modal configuré -> estimation positive cohérente
    engine = LtxModalEngine(isolated_settings, repo=repo, modal_runner=lambda _: {})
    est = engine.estimate_cost(shot)
    assert est is not None and 0.05 < est < 0.50

    # 3. Sans autorisation explicite -> AuthorizationRequiredError
    with pytest.raises(AuthorizationRequiredError):
        engine.render(shot)

    # 4. Plafond inférieur au coût estimé -> BudgetExceededError avant GPU
    engine.arm_single_take(
        project_id="p1",
        shot_id="shot_1",
        authorized_by_human=True,
        max_budget_eur=0.01,
    )
    with pytest.raises(BudgetExceededError):
        engine.render(shot)


@pytest.mark.parametrize("rate", [None, 1.0])
def test_authorized_render_without_budget_ignores_legacy_default(isolated_settings, tmp_path, rate):
    settings = isolated_settings.model_copy(update={
        "gpu_usd_per_sec": rate, "max_budget_eur_per_take": 0.01,
    })
    image = tmp_path / "reference.png"
    image.write_bytes(_make_png_bytes())
    calls = []

    def fake_modal(payload):
        calls.append(payload)
        return {"video_base64": base64.b64encode(b"fake-video").decode("ascii")}

    engine = LtxModalEngine(settings, modal_runner=fake_modal)
    shot = Shot(
        id="s1", duration_s=3, action="Zeste blinks.", image_ref=str(image),
        image_validated=True, ltx_prompt="Vertical shot. Zeste blinks once.",
    )
    engine.arm_single_take(project_id="p", shot_id="s1", authorized_by_human=True)
    assert Path(engine.render(shot)).read_bytes() == b"fake-video"
    assert len(calls) == 1
    with pytest.raises(AuthorizationRequiredError):
        engine.render(shot)
    assert len(calls) == 1


def test_6_single_shot_retake_preserves_other_shots_and_records_failures(
    isolated_settings: V2Settings, tmp_path: Path
):
    """Refaire un seul plan conserve les autres prises ; un échec GPU conserve tout et logue la cause."""
    clip_sample = _make_real_test_mp4(tmp_path / "sample.mp4", duration_s=5.0)
    b64_video = base64.b64encode(clip_sample.read_bytes()).decode("ascii")
    should_fail = False

    def fake_modal_worker(payload: dict) -> dict:
        if should_fail:
            raise RuntimeError("CUDA OOM simulé sur conteneur distant")
        return {
            "video_base64": b64_video,
            "metadata": {"generation_seconds": 74.5, "frames": payload["frames"]},
        }

    client = TestClient(create_app(settings=isolated_settings, modal_runner=fake_modal_worker))

    # Création d'un projet à 2 plans + import d'image ChatGPT
    p = client.post(
        "/api/projects",
        json={"title": "Pilote 2 plans", "idea": "Deux plans test", "target_duration_s": 10},
    ).json()
    pid = p["id"]
    full = Project(
        id=pid,
        title="Pilote 2 plans",
        idea="Deux plans test",
        characters=[Character(id="zeste", name="Zeste", description="Yellow lemon detective")],
        shots=[
            Shot(
                id="shot_1",
                duration_s=5.0,
                action="Zeste opens a folder on the desk.",
                initial_state="Zeste sits at the desk with the folder closed.",
                final_state="Zeste sits at the desk with the folder open.",
                camera_setup="desk-medium-fixed",
                dialogue=[DialogueLine(speaker_id="zeste", text="Premier plan validé.")],
            ),
            Shot(
                id="shot_2",
                duration_s=5.0,
                action="Zeste looks up surprised at the door.",
                initial_state="Zeste sits at the desk with the folder open.",
                camera_setup="desk-medium-fixed",
                continue_from_previous=True,
                dialogue=[DialogueLine(speaker_id="zeste", text="Qui est là ?")],
            ),
        ],
    )
    client.put(f"/api/projects/{pid}", json=full.model_dump(mode="json"))
    client.post(
        f"/api/projects/{pid}/shots/shot_1/image",
        files={"file": ("zeste.png", _make_png_bytes(), "image/png")},
    )
    client.post(f"/api/projects/{pid}/shots/shot_1/validate-image", json={"validated": True})
    client.post(f"/api/projects/{pid}/prepare-render")

    # Rendu du plan 1 puis du plan 2 (en continuation `last_frame`)
    r1 = client.post(
        f"/api/projects/{pid}/shots/shot_1/render",
        json={"authorized_by_human": True, "max_budget_eur": 1.0},
    )
    assert r1.status_code == 200
    first_shot1_mp4 = r1.json()["project"]["shots"][0]["rendered_video_ref"]

    r2 = client.post(
        f"/api/projects/{pid}/shots/shot_2/render",
        json={"authorized_by_human": True, "max_budget_eur": 1.0},
    )
    assert r2.status_code == 200
    first_shot2_mp4 = r2.json()["project"]["shots"][1]["rendered_video_ref"]

    # Échec sur une tentative de reprise du plan 2 -> les 2 prises existantes restent intactes !
    should_fail = True
    r2_fail = client.post(
        f"/api/projects/{pid}/shots/shot_2/render",
        json={"authorized_by_human": True, "max_budget_eur": 1.0},
    )
    assert r2_fail.status_code == 502
    after_fail = client.get(f"/api/projects/{pid}").json()
    assert after_fail["shots"][0]["rendered_video_ref"] == first_shot1_mp4
    assert after_fail["shots"][1]["rendered_video_ref"] == first_shot2_mp4

    # Reprise réussie uniquement du plan 2 -> plan 1 strictement inchangé !
    should_fail = False
    r2_retry = client.post(
        f"/api/projects/{pid}/shots/shot_2/render",
        json={"authorized_by_human": True, "max_budget_eur": 1.0},
    )
    assert r2_retry.status_code == 200
    after_retry = r2_retry.json()["project"]
    assert after_retry["shots"][0]["rendered_video_ref"] == first_shot1_mp4
    assert after_retry["shots"][1]["rendered_video_ref"] != first_shot2_mp4

    costs = client.get("/api/costs").json()
    assert costs["completed_takes"] == 3
    assert costs["failed_takes"] == 1
    assert costs["total_actual_gpu_eur"] > 0


def test_7_full_15s_vertical_export_with_timed_counter_overlay_and_qc(
    isolated_settings: V2Settings, tmp_path: Path
):
    """Assemble un vrai MP4 vertical 15s (3 × 5s) avec compteur fixe horodaté `000 -> 001 -> 002`."""
    c1 = _make_real_test_mp4(tmp_path / "take1.mp4", duration_s=5.0, color="black", freq=330)
    c2 = _make_real_test_mp4(tmp_path / "take2.mp4", duration_s=5.0, color="black", freq=440)
    c3 = _make_real_test_mp4(tmp_path / "take3.mp4", duration_s=5.0, color="black", freq=550)

    client = TestClient(create_app(settings=isolated_settings))

    # Vérifie aussi que l'UI SvelteKit compilée répond bien sur GET /
    home = client.get("/")
    assert home.status_code == 200
    assert "Studio IA V2" in home.text

    proj = Project(
        id="pilote-15s",
        title="Pilote 15s — Compteur & Fruits",
        idea="Trois plans de 5s avec compteur fixe ajouté au montage FFmpeg.",
        aspect_ratio="9:16",
        target_duration_s=15.0,
        characters=[
            Character(id="zeste", name="Zeste", description="Yellow lemon in trench coat"),
            Character(id="momo", name="Momo", description="Fuzzy peach with glasses"),
        ],
        shots=[
            Shot(
                id="shot_1",
                duration_s=5.0,
                action="Zeste points at blank tabletop screen.",
                dialogue=[DialogueLine(speaker_id="zeste", text="Il affiche zéro.")],
                rendered_video_ref=str(c1),
                display_overlay=DisplayOverlay(
                    initial_text="000",
                    x=0.5,
                    y=0.20,
                    font_size=0.08,
                    color="00FF88",
                    box_bg="101418",
                    changes=[DisplayChange(at_sec=3.0, text="001")],
                ),
            ),
            Shot(
                id="shot_2",
                duration_s=5.0,
                action="Momo steps back staring at the screen.",
                continue_from_previous=True,
                dialogue=[DialogueLine(speaker_id="momo", text="J'ai rien fait !")],
                rendered_video_ref=str(c2),
                display_overlay=DisplayOverlay(initial_text="001", x=0.5, y=0.20, font_size=0.08),
            ),
            Shot(
                id="shot_3",
                duration_s=5.0,
                action="Three knocks ring out and the counter jumps again.",
                continue_from_previous=True,
                dialogue=[DialogueLine(speaker_id="zeste", text="On est combien ici ?")],
                rendered_video_ref=str(c3),
                display_overlay=DisplayOverlay(
                    initial_text="001",
                    x=0.5,
                    y=0.20,
                    font_size=0.08,
                    changes=[DisplayChange(at_sec=2.5, text="002")],
                ),
            ),
        ],
    )
    assert client.post("/api/projects", json={"title": proj.title, "idea": proj.idea, "target_duration_s": 15}).status_code == 200
    assert client.put(f"/api/projects/{proj.id}", json=proj.model_dump(mode="json")).status_code == 200

    exp_resp = client.post(f"/api/projects/{proj.id}/export")
    assert exp_resp.status_code == 200, exp_resp.text
    exported = exp_resp.json()

    out_file = Path(exported["output_path"])
    assert out_file.is_file()
    probe = inspect_media(out_file)
    assert (probe["width"], probe["height"]) == (1080, 1920)
    assert 14.5 <= probe["duration_s"] <= 15.6
    assert probe["has_audio"] is True
    assert probe["sample_rate"] == 48000
    assert probe["channels"] == 2
    assert exported["qc"]["ok"] is True

    # Vérifie dans les pixels décodés que l'incrustation `000` (t=1.0s) diffère bien de `001` (t=4.0s)
    cap = cv2.VideoCapture(str(out_file))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 24)  # t = 1.0s -> "000"
        ok1, frame_t1 = cap.read()
        cap.set(cv2.CAP_PROP_POS_FRAMES, 96)  # t = 4.0s -> "001"
        ok2, frame_t4 = cap.read()
    finally:
        cap.release()
    assert ok1 and ok2
    roi_t1 = frame_t1[250:520, 300:780]
    roi_t4 = frame_t4[250:520, 300:780]
    assert int(roi_t1.max()) > 50, "Le compteur `000` doit être dessiné sur le fond noir"
    assert not np.array_equal(roi_t1, roi_t4), "Le compteur doit basculer visuellement de `000` à `001` à t=3.0s !"

    # Vérifie les utilitaires QC OCR (variantes 7-segments) et Whisper FR
    allowed = build_allowed_forms(["000", "001", "002"])
    assert {"000", "0", "001", "1", "002", "2"} <= allowed
    assert normalize_speech_tokens("J'ai compté douze fruits !") == ["j", "ai", "compte", "12", "fruits"]
    eval_ok = assess_speech("Il affiche un", {"status": "ok", "text": "Il affiche 1", "first_word_start_sec": 0.4})
    assert eval_ok["status"] == "ok"
