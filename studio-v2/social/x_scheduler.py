import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_settings
from core.storage import StudioRepository
from social.common import PARIS, allocate_slots, atomic_json, locked_queue, now_utc, read_config
from social.x_automation import _eligible_export, build_teaser, enqueue_validated_episodes
from social.x_client import XClient, XError


def export_today(data_dir, queue, now):
    today = now.astimezone(PARIS).date()
    entries = [
        p for p in queue["posts"]
        if p.get("scheduled_at") and p["status"] not in {"posted", "cancelled"}
        and datetime.fromisoformat(p["scheduled_at"]).astimezone(PARIS).date() <= today
    ]
    text = "\n\n".join(
        f"{p['scheduled_at']} [{p['status']}] {p.get('episode_id', '')}\n{p['text']}"
        + (f"\nPrompt complet : {p['prompt_full']}" if p.get("prompt_full") else "")
        for p in entries
    )
    Path(data_dir, "today_posts.txt").write_text(text + "\n", encoding="utf-8")


def run_once(data_dir, client, *, now=None, config=None):
    now = now or now_utc()
    data_dir = Path(data_dir)
    config = config or read_config()
    result = {"posted": 0, "blocked": None}
    repo = StudioRepository(data_dir / "v2.db")
    with locked_queue(data_dir) as queue:
        for post in queue["posts"]:
            if post["status"] == "posting":
                post.update(status="uncertain", error="Interruption après intention de publication.")
            if post["status"] != "pending":
                continue
            project = repo.get_project(post["episode_id"])
            export = _eligible_export(repo, project, data_dir) if project else None
            if not export or export["id"] != post["export_id"]:
                post.update(status="cancelled", reason="Validation révoquée ou export remplacé.")
        export_today(data_dir, queue, now)
        today = now.astimezone(PARIS).date()
        posted_today = [
            p for p in queue["posts"] if p["status"] in {"posted", "uncertain"}
            and p.get("posted_at", p.get("attempted_at"))
            and datetime.fromisoformat(p.get("posted_at") or p["attempted_at"]).astimezone(PARIS).date() == today
        ]
        if len(posted_today) >= 4:
            result["blocked"] = "Plafond de quatre publications atteint pour cette date de Paris."
            return result
        if client is None:
            result["blocked"] = "Identifiants X absents. Export de secours disponible."
            return result
        pending = sorted(
            (p for p in queue["posts"] if p["status"] == "pending"),
            key=lambda p: datetime.fromisoformat(p["scheduled_at"]),
        )
        due = [p for p in pending if datetime.fromisoformat(p.get("retry_at") or p["scheduled_at"]) <= now]
        if not due:
            return result
        post = due[0]
        if now - datetime.fromisoformat(post["scheduled_at"]) > timedelta(minutes=30):
            remaining = [p for p in queue["posts"] if p is not post]
            post["scheduled_at"] = allocate_slots(remaining, 1, now)[0]
            return result
        if posted_today and any(
            now - datetime.fromisoformat(p.get("posted_at") or p["attempted_at"]) < timedelta(minutes=10)
            for p in posted_today
        ):
            return result
        if post["kind"] == "funnel":
            from social.funnel import readiness
            ready = readiness(data_dir, config=config, probe_public=True)
            if not ready["ready"]:
                result["blocked"] = "Funnel indisponible : " + ", ".join(ready["missing"])
                return result
        try:
            client.verify_account(config["account"])
        except XError as exc:
            result["blocked"] = str(exc)
            return result
        try:
            media_id = None
            if post.get("requires_media"):
                filename = hashlib.sha256(post["id"].encode()).hexdigest()[:24] + ".mp4"
                teaser = data_dir / "social" / "teasers" / filename
                if not teaser.is_file():
                    build_teaser(post["video_source"], teaser)
                media_id = client.upload_video(teaser)
        except Exception as exc:
            post.update(error=f"Préparation média interrompue : {type(exc).__name__}")
            post["retry_at"] = (now + timedelta(minutes=10)).isoformat()
            result["blocked"] = post["error"]
            return result
        post.update(status="posting", attempted_at=now.isoformat())
        atomic_json(data_dir / "x_queue.json", queue)
        try:
            post_id = client.create_post(
                post["text"], made_with_ai=post.get("made_with_ai", True), media_id=media_id,
            )
        except XError as exc:
            post.update(error=str(exc))
            if exc.uncertain:
                post["status"] = "uncertain"
            elif exc.status in (401, 403, 429):
                post.update(
                    status="pending",
                    retry_at=(now + timedelta(seconds=exc.retry_after)).isoformat(),
                )
            else:
                post["status"] = "failed"
            result["blocked"] = str(exc)
        else:
            post.update(status="posted", remote_id=post_id, posted_at=now.isoformat())
            post.pop("error", None)
            post.pop("retry_at", None)
            result["posted"] = 1
        export_today(data_dir, queue, now)
    return result


def main():
    parser = argparse.ArgumentParser(description="Un passage du planificateur X Studio IA V2.")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--publish", action="store_true", help="Publier les éléments dus via le transport configuré.")
    parser.add_argument("--backend", choices=["api", "browser"], default=os.environ.get("X_POST_BACKEND", "api"))
    parser.add_argument("--doctor", action="store_true", help="Afficher les prérequis, sans publier.")
    args = parser.parse_args()
    data = args.data_dir or Path(load_settings().data_dir)
    if args.doctor:
        from social.funnel import readiness
        report = readiness(data, probe_public=True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["ready"]:
            raise SystemExit(2)
        return
    repo = StudioRepository(data / "v2.db")
    added = enqueue_validated_episodes(repo, data)
    client = None
    if args.publish:
        if args.backend == "browser":
            from social.x_browser import BrowserXClient
            client = BrowserXClient(data)
        else:
            client = XClient.from_env()
    result = run_once(data, client)
    result["queued"] = added
    if args.publish and args.backend == "api" and client is not None:
        from social.funnel import poll_funnel
        result["funnel"] = poll_funnel(data, client)
    print(json.dumps(result, ensure_ascii=False))
    if args.publish and result["blocked"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
