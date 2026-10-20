import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_settings
from core.storage import StudioRepository
from qc.media import PROCESS_FLAGS, ffmpeg_exe
from social.common import allocate_slots, locked_queue, now_utc, read_config, shorten, weighted_length


def build_teaser(source, destination):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        ffmpeg_exe(), "-v", "error", "-nostdin", "-y", "-i", str(source),
        "-t", "5", "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:a", "aac", "-movflags", "+faststart", str(destination),
    ], check=True, capture_output=True, timeout=180, **PROCESS_FLAGS)
    return destination


def _eligible_export(repo, project, data_dir):
    if not project.shots or not all(s.human_validated for s in project.shots):
        return None
    root = Path(data_dir).resolve()
    for shot in project.shots:
        path = Path(shot.rendered_video_ref or "").resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
    exports = repo.list_exports(project.id)
    if not exports:
        return None
    latest = exports[0]
    video = Path(latest["output_path"]).resolve()
    fingerprint = hashlib.sha256(project.model_dump_json().encode("utf-8")).hexdigest()
    if (
        latest["qc"].get("ok") is not True
        or latest["qc"].get("project_sha256") != fingerprint
        or not video.is_relative_to(root) or not video.is_file()
        or latest["duration_s"] < 5
    ):
        return None
    return latest


def _cost_label(repo, project):
    takes = repo.list_takes(project.id)
    if not takes or any(
        t["status"] == "imported" or t["actual_cost_eur"] is None
        or not math.isfinite(t["actual_cost_eur"]) or t["actual_cost_eur"] < 0
        for t in takes
    ):
        return "Coût du rendu : non mesuré."
    cost = sum(t["actual_cost_eur"] for t in takes)
    return f"Coût enregistré : {cost:.2f} EUR, essais inclus.".replace(".", ",", 1)


def enqueue_validated_episodes(repo, data_dir, *, now=None, jitter_minutes=None, config=None):
    now = now or now_utc()
    config = config or read_config()
    added = 0
    with locked_queue(data_dir) as queue:
        for item in repo.list_projects():
            project = repo.get_project(item["id"])
            export = _eligible_export(repo, project, data_dir)
            if not export or export["id"] in queue["episodes"]:
                continue
            for previous in queue["posts"]:
                if previous["episode_id"] == project.id and previous["status"] == "pending":
                    previous.update(status="cancelled", reason="Nouvel export validé.")
            title = shorten(project.title or project.idea, 55)
            character = shorten(", ".join(c.name for c in project.characters) or title, 35)
            prompt = project.shots[0].ltx_prompt
            full_prompt = prompt
            prefix = f"Épisode validé : {character}.\n{shorten(project.idea, 70)}\nPrompt LTX : "
            hook = prefix + shorten(prompt, 280 - weighted_length(prefix))
            cost = _cost_label(repo, project)
            opt_in = config.get("keyword_campaign_enabled", False) and not any(
                p.get("keyword_opt_in") and p.get("campaign_id") == config["campaign_id"]
                for p in queue["posts"]
            )
            if opt_in:
                cta = (
                    "La bible Studio IA : showrunner + attention + rétention + narration.\n"
                    "Réponds STUDIO ici pour recevoir un seul DM automatique avec le lien du PDF gratuit. "
                    "STOP en DM pour arrêter."
                )
            else:
                cta = (
                    f"Pour reprendre la méthode de « {title} » : "
                    "un enjeu clair, une action par plan, une conséquence.\n"
                    f"Les 4 bibles sont ici : {config['landing_url']}"
                )
            entries = [
                {"kind": "hook", "text": hook, "prompt_full": full_prompt},
                {"kind": "behind", "text": f"Les coulisses de « {title} ».\nTeaser de 5 s, généré avec LTX-2.5.\n{cost}\nAlternative sans API : exécution locale, GPU et électricité à compter.",
                 "video_source": export["output_path"], "requires_media": True},
                {"kind": "lesson", "text": f"3 règles appliquées à « {title} » :\n1. Un hook visuel immédiat.\n2. Un enjeu qui progresse entre les plans.\n3. Un personnage qui agit.\nCe sont des choix éditoriaux, pas une promesse de vues."},
                {"kind": "funnel", "text": cta, "keyword_opt_in": opt_in},
            ]
            slots = allocate_slots(queue["posts"], 4, now, jitter_minutes)
            for entry, slot in zip(entries, slots):
                if weighted_length(entry["text"]) > 280:
                    raise ValueError(f"Post {entry['kind']} trop long pour X.")
                entry.update(
                    id=hashlib.sha256(f"{export['id']}:{entry['kind']}".encode()).hexdigest()[:24],
                    episode_id=project.id, export_id=export["id"],
                    project_sha256=export["qc"]["project_sha256"],
                    campaign_id=config["campaign_id"], scheduled_at=slot,
                    created_at=now.isoformat(), status="pending", made_with_ai=True,
                )
                queue["posts"].append(entry)
            queue["episodes"][export["id"]] = {"episode_id": project.id, "queued_at": now.isoformat()}
            added += 4
    return added


def main():
    parser = argparse.ArgumentParser(description="Préparer les posts des exports Studio IA V2 validés.")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    data = args.data_dir or Path(load_settings().data_dir)
    count = enqueue_validated_episodes(StudioRepository(data / "v2.db"), data)
    print(json.dumps({"queued": count, "queue": str(data / "x_queue.json")}))


if __name__ == "__main__":
    main()
