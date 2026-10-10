import asyncio
import hashlib
import json
import subprocess

import pytest

from calliope.queue.manager import queue_manager
from calliope.studio import production, service
from tests.test_studio import png_bytes
from tests.test_studio_series import episode, planned_episode, series
from tests.test_studio_series import isolated_production as isolated_production


def test_image_compilation_binds_every_reference_in_order_and_preserves_identity(tmp_path):
    from calliope.studio.visual_identity import compile_image_request

    entities = []
    for index in range(6):
        path = tmp_path / f"entity-{index}.png"
        path.write_bytes(png_bytes())
        entities.append(
            {
                "key": f"entity-{index}",
                "kind": "character",
                "name": f"Name {index}",
                "description": "Story biography",
                "reference_path": str(path),
                "identity": {
                    "visual_description": f"Immutable species {index}, eyes and mouth",
                    "reference_approved": True,
                    "reference_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                },
            }
        )
    context = {
        "entities": entities,
        "bible": {"visual_style": "Clay animation"},
        "shot": {"character_keys": [e["key"] for e in entities]},
        "directing_profile": {"require_reference_approval": True},
    }
    request = compile_image_request({"image_prompt": "Everyone reacts."}, context)
    assert request["reference_paths"] == [e["reference_path"] for e in entities]
    assert [b["entity_key"] for b in request["reference_bindings"]] == context["shot"][
        "character_keys"
    ]
    for index, entity in enumerate(entities, 1):
        assert f"Reference image {index}: {entity['key']}" in request["prompt"]
        assert entity["identity"]["visual_description"] in request["prompt"]
    assert "Clay animation" in request["prompt"]
    assert request["input_hashes"]["identity_contract"]
    entities[0]["identity"]["reference_approved"] = False
    with pytest.raises(ValueError, match="approuv"):
        compile_image_request({"image_prompt": "Everyone reacts."}, context)


def test_exact_visual_typography_is_removed_without_erasing_story_or_spoken_value():
    from calliope.studio.visual_identity import strip_generated_text

    prompt = (
        "A small badge reads 1 above the hero. His value changes from 1 to 2. "
        "He points at the blank panel."
    )
    labels = [{"key": "hero-value", "entity_key": "hero", "value": 1}]
    cleaned = strip_generated_text(prompt, labels)
    assert "badge reads 1" not in cleaned
    assert "value changes from 1 to 2" in cleaned
    assert "blank panel" in cleaned
    for typography in (
        "A glowing 500 floats above Leo's forehead.",
        "NOAH 1 above the forehead.",
        "Leo wears badge LEO 500.",
    ):
        assert strip_generated_text(typography, labels) == ""
    assert strip_generated_text(
        "Noah reacts as his status rises from 1 to 2. Student1 is 17 years old.", labels
    ) == ("Noah reacts as his status rises from 1 to 2. Student1 is 17 years old.")


def test_age_and_head_motion_survive_no_text_compilation():
    from calliope.studio.visual_identity import strip_generated_text

    assert strip_generated_text("A 17-year-old hero turns his head.", [{"key": "value"}]) == (
        "A 17-year-old hero turns his head."
    )


def test_entity_identity_and_manual_approval_bind_to_actual_reference(client):
    created = series(client)
    project = episode(client, created["id"]).json()["project_id"]
    production.save_episode_plan(project, planned_episode({}))
    entity = client.get(f"/api/studio/series/{created['id']}").json()["entities"][0]
    url = f"/api/studio/series/{created['id']}/entities/{entity['id']}"
    client.post(f"/api/studio/projects/{project}/production/pause")
    with service.database() as conn:
        conn.execute("UPDATE studio_productions SET status='paused' WHERE project_id=?", (project,))
        conn.commit()
    patched = client.patch(
        url,
        json={
            "identity": {
                "visual_description": "Tiny orange robot with a natural mouth",
                "attached_value": 1,
                "label_key": "keeper-value",
            }
        },
    )
    assert patched.status_code == 200, patched.text
    uploaded = client.post(
        url + "/reference", files={"file": ("reference.png", png_bytes(), "image/png")}
    )
    assert uploaded.status_code == 200, uploaded.text
    assert not uploaded.json()["entities"][0]["identity"]["reference_approved"]
    approved = client.patch(url, json={"reference_approved": True})
    assert approved.status_code == 200, approved.text
    identity = approved.json()["entities"][0]["identity"]
    assert identity["reference_approved"] and len(identity["reference_sha256"]) == 64
    changed = client.patch(url, json={"identity": {"visual_description": "Blue robot"}})
    assert changed.status_code == 200, changed.text
    assert not changed.json()["entities"][0]["identity"]["reference_approved"]


def test_canonical_variant_uses_approved_base_and_persists_actual_request(client, monkeypatch):
    created = series(client)
    project = episode(client, created["id"]).json()["project_id"]
    plan = planned_episode({})
    plan["entities"][0]["identity"] = {
        "visual_description": "Orange robot, round eyes, visible mouth"
    }
    plan["entities"].append(
        {
            "key": "keeper_young",
            "kind": "character",
            "name": "Young keeper",
            "description": "Young version",
            "identity": {"visual_description": "Smaller body", "variant_of": "keeper"},
        }
    )
    production.save_episode_plan(project, plan)
    requests = []

    async def image(request):
        requests.append(request)
        return service.normalized_image(png_bytes())

    monkeypatch.setattr(service, "_generate_image", image)
    with service.database() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE project_id=?", (project,)).fetchone()
        job = dict(row)
    asyncio.run(production.ensure_entities(job, lambda: False))
    assert len(requests) == 2
    assert requests[1]["reference_bindings"][0]["entity_key"] == "keeper"
    assert requests[1]["reference_paths"]
    assert "Orange robot" in requests[1]["prompt"]
    with service.database() as conn:
        rows = conn.execute(
            "SELECT reference_request_json FROM studio_entities WHERE series_id=? ORDER BY id",
            (created["id"],),
        ).fetchall()
    assert json.loads(rows[1][0])["prompt"] == requests[1]["prompt"]
    assert json.loads(rows[1][0])["input_hashes"]["identity_contract"]


def test_character_anchor_is_reviewed_on_exact_source_before_video_request(client):
    from tests.test_studio import new_board

    project, board = new_board(client)
    shot_id = board["scenes"][0]["shots"][0]["id"]
    labels = [
        {
            "key": "hero-value",
            "entity_key": "hero",
            "value": 1,
            "x": 0.5,
            "y": 0.1,
            "anchor": "character",
            "anchor_box": [0.1, 0.1, 0.5, 0.5],
        }
    ]
    with service.database() as conn:
        conn.execute(
            "INSERT INTO studio_shot_context(clip_id,context_json) VALUES(?,?)",
            (shot_id, json.dumps({"screen_text": labels, "character_keys": ["hero"]})),
        )
        conn.commit()
    source = service.store_upload(shot_id, png_bytes(), "image")
    service.select_take(source["id"])
    with pytest.raises(Exception, match="approuv"):
        service.enqueue_takes(
            shot_id, {"kind": "video", "request_id": "before-review", "n_takes": 1}
        )
    review = client.post(f"/api/studio/takes/{source['id']}/visual-review", json={"approved": True})
    assert review.status_code == 200, review.text
    queued = service.enqueue_takes(
        shot_id, {"kind": "video", "request_id": "after-review", "n_takes": 1}
    )
    with service.database() as conn:
        request = json.loads(
            conn.execute(
                "SELECT request_json FROM studio_takes WHERE id=?", (queued["takes"][0]["id"],)
            ).fetchone()[0]
        )
    assert request["source_visual_review"]["anchor_bindings"][0]["entity_key"] == "hero"
    assert request["source_sha256"] == request["source_visual_review"]["source_sha256"]


def test_strict_canonical_review_pauses_before_variant_and_any_video(client, monkeypatch):
    created = series(client)
    client.patch(
        f"/api/studio/series/{created['id']}",
        json={
            "directing_profile": {"require_reference_approval": True},
        },
    ).raise_for_status()
    project = episode(client, created["id"]).json()["project_id"]
    plan = planned_episode({})
    plan["entities"][0]["identity"] = {
        "visual_description": "Orange robot with round eyes and mouth"
    }
    plan["entities"].append(
        {
            "key": "keeper_young",
            "kind": "character",
            "name": "Young keeper",
            "description": "Young version",
            "identity": {"visual_description": "Smaller body", "variant_of": "keeper"},
        }
    )
    production.save_episode_plan(project, plan)
    calls = []

    async def image(request):
        calls.append(request)
        return service.normalized_image(png_bytes())

    monkeypatch.setattr(service, "_generate_image", image)
    with service.database() as conn:
        job = dict(conn.execute("SELECT * FROM jobs WHERE project_id=?", (project,)).fetchone())
    with pytest.raises(production.ProductionPausedError):
        asyncio.run(production.ensure_entities(job, lambda: False))
    assert len(calls) == 1
    assert service.board(project)["production"]["status"] == "paused"
    current = client.get(f"/api/studio/series/{created['id']}").json()
    base, variant = current["entities"]
    assert base["url"] and not base["identity"]["reference_approved"]
    assert not variant["url"]
    client.patch(
        f"/api/studio/series/{created['id']}/entities/{base['id']}",
        json={"reference_approved": True},
    ).raise_for_status()
    with pytest.raises(production.ProductionPausedError):
        asyncio.run(production.ensure_entities(job, lambda: False))
    assert len(calls) == 2
    assert calls[1]["reference_bindings"][0]["entity_key"] == "keeper"


def test_visual_contract_refresh_requires_manifest_and_never_dispatches(client, monkeypatch):
    created = series(client)
    project = episode(client, created["id"]).json()["project_id"]
    plan = planned_episode({})
    plan["scenes"][0]["shots"] = plan["scenes"][0]["shots"][:1]
    production.save_episode_plan(project, plan)
    with service.database() as conn:
        conn.execute("UPDATE studio_productions SET status='failed' WHERE project_id=?", (project,))
        original_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        conn.commit()
    entity = client.get(f"/api/studio/series/{created['id']}").json()["entities"][0]
    client.patch(
        f"/api/studio/series/{created['id']}/entities/{entity['id']}",
        json={
            "identity": {
                "visual_description": "Orange robot",
                "attached_value": 1,
                "label_key": "keeper-value",
            }
        },
    ).raise_for_status()
    client.patch(
        f"/api/studio/series/{created['id']}",
        json={
            "directing_profile": {
                "require_reference_approval": True,
                "attached_values": True,
            }
        },
    ).raise_for_status()

    def forbidden(*args):
        pytest.fail("Preparing a contract must dispatch no provider or job")

    monkeypatch.setattr(service, "_generate_image", forbidden)
    missing = client.post(f"/api/studio/projects/{project}/visual-contract")
    assert missing.status_code == 422 and "ancrage" in missing.text
    shot = service.board(project)["scenes"][0]["shots"][0]
    client.patch(
        f"/api/studio/projects/{project}/shots/{shot['id']}",
        json={
            "screen_text": [
                {
                    "key": "keeper-value",
                    "entity_key": "keeper",
                    "value": 1,
                    "anchor": "character",
                    "anchor_box": [0.1, 0.1, 0.5, 0.5],
                    "x": 0.4,
                    "y": 0.1,
                }
            ]
        },
    ).raise_for_status()
    prepared = client.post(f"/api/studio/projects/{project}/visual-contract")
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["production"]["status"] == "paused"
    with service.database() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == original_jobs
        context = service.shot_context(conn, shot["id"])
    assert context["directing_profile"]["attached_values"]
    assert context["entities"][0]["identity"]["visual_description"] == "Orange robot"


def test_stale_unsubmitted_child_is_preserved_but_cannot_be_resumed(client, monkeypatch):
    created = series(client)
    project = episode(client, created["id"]).json()["project_id"]
    production.save_episode_plan(project, planned_episode({}))
    shot = service.board(project)["scenes"][0]["shots"][0]
    with service.database() as conn:
        job = dict(conn.execute("SELECT * FROM jobs WHERE project_id=?", (project,)).fetchone())
    old = service.enqueue_takes(
        shot["id"],
        {"kind": "image", "request_id": "auto-stale", "n_takes": 1, "parent_job_id": job["id"]},
    )["takes"][0]
    with service.database() as conn:
        conn.execute(
            "UPDATE studio_shots SET image_prompt='A different location.' WHERE clip_id=?",
            (shot["id"],),
        )
        old_request = conn.execute(
            "SELECT request_json FROM studio_takes WHERE id=?", (old["id"],)
        ).fetchone()[0]
        conn.commit()
    called = []

    async def child(identifier, should_stop):
        called.append(identifier)
        return []

    monkeypatch.setattr(production, "run_child", child)
    monkeypatch.setattr(service, "select_take", lambda identifier: {"id": identifier})
    asyncio.run(
        production.generate_take(job, production.load_shot(shot["id"]), "image", lambda: False)
    )
    assert called and old["job_id"] not in called
    with service.database() as conn:
        old_job = conn.execute("SELECT error FROM jobs WHERE id=?", (old["job_id"],)).fetchone()[0]
        assert old_job == "cancelled"
        assert (
            conn.execute(
                "SELECT request_json FROM studio_takes WHERE id=?", (old["id"],)
            ).fetchone()[0]
            == old_request
        )


def test_valued_canon_requires_manifest_even_with_toggle_off_and_accepts_unvalued_host():
    from calliope.studio.visual_identity import validate_value_manifest

    context = {
        "entities": [
            {
                "key": "hero",
                "kind": "character",
                "identity": {
                    "attached_value": 1,
                    "label_key": "hero-value",
                },
            },
            {"key": "host", "kind": "character", "identity": {}},
        ],
        "shot": {"character_keys": ["hero", "host"]},
        "directing_profile": {},
    }
    with pytest.raises(ValueError, match="ancrage"):
        validate_value_manifest({"duration_sec": 5}, context)
    label = {
        "key": "hero-value",
        "entity_key": "hero",
        "value": 1,
        "x": 0.5,
        "y": 0.1,
        "anchor": "character",
        "anchor_box": [0.1, 0.1, 0.4, 0.5],
        "event": "change",
        "changes": [{"at_sec": 2, "value": 2}],
    }
    context["shot"]["screen_text"] = [label]
    expected = {}
    validate_value_manifest({"duration_sec": 5}, context, expected)
    assert expected == {"hero-value": 2}
    label.update(value=1, event="none", changes=[])
    with pytest.raises(ValueError, match="rester 2"):
        validate_value_manifest({"duration_sec": 5}, context, expected)
    label["value"] = 2
    assert len(validate_value_manifest({"duration_sec": 5}, context, expected)) == 1


def test_revoked_canonical_approval_blocks_already_queued_image_before_provider(
    client, monkeypatch
):
    created = series(client)
    client.patch(
        f"/api/studio/series/{created['id']}",
        json={
            "directing_profile": {
                "require_reference_approval": True,
            }
        },
    ).raise_for_status()
    project = episode(client, created["id"]).json()["project_id"]
    plan = planned_episode({})
    plan["entities"][0]["identity"] = {"visual_description": "Orange robot with a mouth"}
    production.save_episode_plan(project, plan)
    with service.database() as conn:
        conn.execute("UPDATE studio_productions SET status='paused' WHERE project_id=?", (project,))
        conn.commit()
    entity = client.get(f"/api/studio/series/{created['id']}").json()["entities"][0]
    url = f"/api/studio/series/{created['id']}/entities/{entity['id']}"
    client.post(
        url + "/reference", files={"file": ("ref.png", png_bytes(), "image/png")}
    ).raise_for_status()
    client.patch(url, json={"reference_approved": True}).raise_for_status()
    shot = service.board(project)["scenes"][0]["shots"][0]
    queued = service.enqueue_takes(
        shot["id"], {"kind": "image", "request_id": "strict", "n_takes": 1}
    )
    client.patch(url, json={"reference_approved": False}).raise_for_status()

    def forbidden(*args):
        pytest.fail("A revoked canon must dispatch no provider")

    monkeypatch.setattr(service, "_generate_image", forbidden)
    job = queue_manager.get_job(queued["jobs"][0]["id"])
    with pytest.raises(RuntimeError, match="approuv"):
        asyncio.run(service.run_studio_job(job, json.loads(job["payload_json"]), lambda: False))


def test_continuation_requires_exact_retained_video_frame_and_keeps_previous_video_source(
    client, monkeypatch, tmp_path
):
    from calliope.engines.modal_h3 import ModalH3Engine
    from tests.test_studio import new_board

    project, board = new_board(client)
    first, second = [scene["shots"][0] for scene in board["scenes"]]
    label = {
        "key": "hero-value",
        "entity_key": "hero",
        "value": 1,
        "x": 0.5,
        "y": 0.1,
        "anchor": "character",
        "anchor_box": [0.1, 0.1, 0.5, 0.5],
    }
    with service.database() as conn:
        conn.execute(
            "INSERT INTO studio_shot_context(clip_id,context_json) VALUES(?,?)",
            (second["id"], json.dumps({"character_keys": ["hero"], "screen_text": [label]})),
        )
        conn.execute(
            "UPDATE studio_shots SET continue_from_previous=1 WHERE clip_id=?", (second["id"],)
        )
        conn.execute("UPDATE studio_shots SET trim_end_sec=2.0 WHERE clip_id=?", (first["id"],))
        conn.commit()
    path = tmp_path / "continuity.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=128x72:r=24:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=128x72:r=24:d=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    source = service.store_upload(first["id"], path.read_bytes(), "video")
    service.select_take(source["id"])
    with pytest.raises(service.HTTPException, match="approuv"):
        service.enqueue_takes(
            second["id"], {"kind": "video", "request_id": "missing", "n_takes": 1}
        )
    service.review_take_visuals(source["id"], True, 0, second["id"])
    with pytest.raises(service.HTTPException, match="dernière frame"):
        service.enqueue_takes(second["id"], {"kind": "video", "request_id": "wrong", "n_takes": 1})
    reviewed = client.post(
        f"/api/studio/takes/{source['id']}/visual-review",
        json={
            "approved": True,
            "frame_index": 47,
            "target_shot_id": second["id"],
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    queued = service.enqueue_takes(
        second["id"], {"kind": "video", "request_id": "retained", "n_takes": 1}
    )
    with service.database() as conn:
        take_id = queued["takes"][0]["id"]
        request = json.loads(
            conn.execute("SELECT request_json FROM studio_takes WHERE id=?", (take_id,)).fetchone()[
                0
            ]
        )
        assert request["previous_video_path"] == source["output_path"]
        assert request["previous_video_time"] == 2.0
        assert request["source_visual_review"]["frame_index"] == 47
        assert request["source_frame_sha256"]
        request["source_frame_sha256"] = "0" * 64
        conn.execute(
            "UPDATE studio_takes SET request_json=? WHERE id=?", (json.dumps(request), take_id)
        )
        conn.commit()

    def forbidden(*args):
        pytest.fail("A changed extraction frame must dispatch no provider")

    monkeypatch.setattr(ModalH3Engine, "identity", lambda self: {})
    monkeypatch.setattr(ModalH3Engine, "spawn", forbidden)
    job = queue_manager.get_job(queued["jobs"][0]["id"])
    with pytest.raises(RuntimeError, match="frame préparée"):
        asyncio.run(service.run_studio_job(job, json.loads(job["payload_json"]), lambda: False))
