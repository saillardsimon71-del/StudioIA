import argparse
import html
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import httpx

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from social.common import ROOT, locked_queue, now_utc, read_config
from social.x_client import XClient, XError


class ProviderError(RuntimeError):
    pass


class FunnelStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS social_deliveries (
                    user_id TEXT NOT NULL, campaign_id TEXT NOT NULL,
                    source_comment_id TEXT NOT NULL, root_id TEXT NOT NULL,
                    status TEXT NOT NULL, remote_id TEXT, error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, campaign_id)
                );
                CREATE TABLE IF NOT EXISTS social_opt_outs (
                    user_id TEXT PRIMARY KEY, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS social_leads (
                    email TEXT PRIMARY KEY, subscriber_id TEXT NOT NULL,
                    status TEXT NOT NULL, consent_at TEXT NOT NULL,
                    consent_version TEXT NOT NULL DEFAULT 'studio-bibles-2026-10-20'
                );
                CREATE TABLE IF NOT EXISTS social_state (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
            """)

    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def delivery(self, user_id, campaign):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM social_deliveries WHERE user_id=? AND campaign_id=?",
                (user_id, campaign),
            ).fetchone()
        return dict(row) if row else None

    def opt_out(self, user_id):
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO social_opt_outs VALUES (?,?)", (user_id, now_utc().isoformat()))
            conn.execute(
                "UPDATE social_deliveries SET status='opted_out' WHERE user_id=? AND status='pending'",
                (user_id,),
            )

    def opted_out(self, user_id):
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM social_opt_outs WHERE user_id=?", (user_id,)).fetchone() is not None

    def claim_delivery(self, user_id, campaign, comment_id, root_id):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM social_opt_outs WHERE user_id=?", (user_id,)).fetchone():
                return False
            row = conn.execute(
                "SELECT status FROM social_deliveries WHERE user_id=? AND campaign_id=?",
                (user_id, campaign),
            ).fetchone()
            if row and row["status"] != "pending":
                return False
            conn.execute("""
                INSERT INTO social_deliveries
                (user_id,campaign_id,source_comment_id,root_id,status,updated_at)
                VALUES (?,?,?,?,'sending',?)
                ON CONFLICT(user_id,campaign_id) DO UPDATE SET status='sending',updated_at=excluded.updated_at
            """, (user_id, campaign, comment_id, root_id, now_utc().isoformat()))
        return True

    def finish_delivery(self, user_id, campaign, status, *, remote_id=None, error=None):
        with self.connect() as conn:
            conn.execute("""
                UPDATE social_deliveries SET status=?,remote_id=?,error=?,updated_at=?
                WHERE user_id=? AND campaign_id=?
            """, (status, remote_id, error, now_utc().isoformat(), user_id, campaign))

    def get_state(self, key, default=None):
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM social_state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_state(self, key, value):
        with self.connect() as conn:
            conn.execute("""
                INSERT INTO social_state VALUES (?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (key, json.dumps(value)))

    def save_lead(self, email, subscriber_id, status):
        with self.connect() as conn:
            conn.execute("""
                INSERT INTO social_leads (email,subscriber_id,status,consent_at) VALUES (?,?,?,?)
                ON CONFLICT(email) DO UPDATE SET
                subscriber_id=excluded.subscriber_id,status=excluded.status,consent_at=excluded.consent_at
            """, (email, str(subscriber_id), status, now_utc().isoformat()))

    def lead(self, email):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM social_leads WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None


def process_comment(comment, allowed_posts, client, store, config):
    author = str(comment.get("author_id", ""))
    roots = [str(r.get("id")) for r in comment.get("referenced_tweets", []) if r.get("type") == "replied_to"]
    text = re.sub(r"@\w+", "", comment.get("text", "")).strip()
    if (
        not author.isdigit() or author == client.user_id
        or text.casefold() != config["keyword"].casefold()
        or not roots or roots[0] not in allowed_posts or not str(comment.get("id", "")).isdigit()
    ):
        return "ignored"
    if store.opted_out(author):
        return "opted_out"
    campaign = config["campaign_id"]
    if not store.claim_delivery(author, campaign, str(comment["id"]), roots[0]):
        return "duplicate"
    message = config["dm_template"].format(landing_url=config["landing_url"])
    try:
        remote_id = client.send_dm(author, message)
    except XError as exc:
        status = "uncertain" if exc.uncertain else "pending" if exc.status == 429 else "failed"
        store.finish_delivery(author, campaign, status, error=str(exc))
        return status
    store.finish_delivery(author, campaign, "sent", remote_id=remote_id)
    return "sent"


class KitClient:
    def __init__(self, api_key, *, form_id, sequence_id, transport=None):
        self.form_id, self.sequence_id = str(form_id), str(sequence_id)
        self.client = httpx.Client(
            base_url="https://api.kit.com/v4",
            headers={"X-Kit-Api-Key": api_key},
            timeout=30, follow_redirects=False, transport=transport,
        )

    @classmethod
    def from_env(cls, store):
        key, form = os.environ.get("KIT_API_KEY"), os.environ.get("KIT_FORM_ID")
        sequence = os.environ.get("KIT_SEQUENCE_ID")
        if not sequence and store.get_state("kit_setup_complete"):
            sequence = store.get_state("kit_sequence_id")
        if not all((key, form, sequence)):
            return None
        return cls(key, form_id=form, sequence_id=sequence)

    def request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderError("Le service email n'a pas confirmé l'opération.") from None


def subscribe(email, consent, kit, store, config):
    email = email.strip().lower()
    if consent is not True:
        raise ValueError("Le consentement aux emails est obligatoire.")
    if not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", email) or len(email) > 254:
        raise ValueError("Adresse email invalide.")
    current = store.lead(email)
    if current and current["status"] in {"pending_confirmation", "enrolled"}:
        return {"status": current["status"]}
    subscriber = kit.request("POST", "subscribers", json={
        "email_address": email, "state": "inactive",
    })["subscriber"]
    subscriber_id = str(subscriber["id"])
    response = kit.request("POST", f"forms/{kit.form_id}/subscribers/{subscriber_id}", json={
        "referrer": config["landing_url"],
    })
    status = "pending_confirmation"
    if response["subscriber"].get("state") == "active":
        kit.request("POST", f"sequences/{kit.sequence_id}/subscribers/{subscriber_id}", json={})
        status = "enrolled"
    store.save_lead(email, subscriber_id, status)
    return {"status": status}


def sync_confirmed_leads(kit, store):
    with store.connect() as conn:
        leads = conn.execute("SELECT * FROM social_leads WHERE status='pending_confirmation' LIMIT 100").fetchall()
    enrolled = 0
    for row in leads:
        state = kit.request("GET", f"subscribers/{row['subscriber_id']}")["subscriber"]["state"]
        if state == "active":
            kit.request("POST", f"sequences/{kit.sequence_id}/subscribers/{row['subscriber_id']}", json={})
            with store.connect() as conn:
                conn.execute("UPDATE social_leads SET status='enrolled' WHERE email=?", (row["email"],))
            enrolled += 1
        elif state in {"cancelled", "bounced", "complained"}:
            with store.connect() as conn:
                conn.execute("UPDATE social_leads SET status=? WHERE email=?", (state, row["email"]))
    return enrolled


def configure_kit(store, config):
    key = os.environ.get("KIT_API_KEY")
    form = os.environ.get("KIT_FORM_ID")
    sender = os.environ.get("KIT_SENDER_EMAIL")
    if not all((key, form, sender, config["checkout_url"])):
        raise ProviderError("Configurer KIT_API_KEY, KIT_FORM_ID, KIT_SENDER_EMAIL et STUDIO_CHECKOUT_URL.")
    kit = KitClient(key, form_id=form, sequence_id=store.get_state("kit_sequence_id", ""))
    if not kit.sequence_id:
        sequence = kit.request("POST", "sequences", json={
            "name": "Studio IA 197 EUR - " + config["campaign_id"],
            "email_address": sender, "time_zone": "Europe/Paris",
            "active": True, "repeat": False, "hold": False,
        })["sequence"]
        kit.sequence_id = str(sequence["id"])
        store.set_state("kit_sequence_id", kit.sequence_id)
    for index, email in enumerate(config["email_sequence"]):
        state_key = f"kit_email_{kit.sequence_id}_{index}"
        if store.get_state(state_key):
            continue
        text = email["body"].format(
            pdf_url=config["landing_url"].rstrip("/") + "/bible.pdf",
            checkout_url=config["checkout_url"], landing_url=config["landing_url"],
        )
        content = "".join(
            "<p>" + re.sub(r"https://[^\s<]+", lambda m: f'<a href="{m[0]}">{m[0]}</a>', html.escape(p)) + "</p>"
            for p in text.split("\n") if p.strip()
        )
        delay = 0 if index == 0 else email["day"] - config["email_sequence"][index - 1]["day"]
        created = kit.request("POST", f"sequences/{kit.sequence_id}/emails", json={
            "subject": email["subject"], "content": content,
            "delay_value": delay, "delay_unit": "days",
            "position": index, "published": True,
        })["email"]
        store.set_state(state_key, created["id"])
    store.set_state("kit_setup_complete", True)
    return {"sequence_id": kit.sequence_id, "emails": len(config["email_sequence"])}


def readiness(data_dir, *, config=None, probe_public=False):
    store = FunnelStore(Path(data_dir) / "v2.db")
    config = config or read_config()
    checks = {
        "X_API_KEYS": XClient.from_env() is not None,
        "KIT_API_KEY": bool(os.environ.get("KIT_API_KEY")),
        "KIT_FORM_ID": bool(os.environ.get("KIT_FORM_ID")),
        "KIT_SEQUENCE_ID": bool(os.environ.get("KIT_SEQUENCE_ID") or (
            store.get_state("kit_setup_complete") and store.get_state("kit_sequence_id")
        )),
        "STUDIO_CHECKOUT_URL": config["checkout_url"].startswith("https://"),
        "STUDIO_LANDING_URL": config["landing_url"].startswith("https://"),
        "LEAD_MAGNET_PDF": (ROOT / "web" / "funnel" / "bible-studio-ia.pdf").is_file(),
    }
    if probe_public and all(checks.values()):
        from urllib.parse import urlsplit
        parsed = urlsplit(config["landing_url"])
        origin = f"{parsed.scheme}://{parsed.netloc}"
        try:
            with httpx.Client(timeout=15, follow_redirects=False) as public:
                page = public.get(config["landing_url"])
                settings = public.get(origin + "/api/funnel/config")
                pdf = public.get(config["landing_url"].rstrip("/") + "/bible.pdf")
                checks["PUBLIC_FUNNEL"] = (
                    page.status_code == settings.status_code == pdf.status_code == 200
                    and settings.json().get("signup_enabled") is True
                    and settings.json().get("checkout_url") == config["checkout_url"]
                    and pdf.content.startswith(b"%PDF-")
                )
        except (httpx.HTTPError, ValueError):
            checks["PUBLIC_FUNNEL"] = False
    return {
        "ready": all(checks.values()), "checks": checks,
        "missing": [key for key, ok in checks.items() if not ok],
        "monetization_guaranteed": False,
    }


def poll_funnel(data_dir, client):
    config = read_config()
    store = FunnelStore(Path(data_dir) / "v2.db")
    kit = KitClient.from_env(store)
    result = {"sent": 0, "enrolled": 0}
    if kit:
        try:
            result["enrolled"] = sync_confirmed_leads(kit, store)
        except ProviderError as exc:
            result["email_error"] = str(exc)
    with locked_queue(data_dir) as queue:
        roots = {
            str(p["remote_id"]) for p in queue["posts"]
            if p.get("keyword_opt_in") and p.get("remote_id") and p["status"] == "posted"
            and p.get("campaign_id") == config["campaign_id"]
        }
    if not roots:
        return result
    if not readiness(data_dir, config=config, probe_public=True)["ready"]:
        result["blocked"] = "Funnel incomplet : activation requise avant les DM."
        return result
    try:
        client.verify_account(config["account"])
        for event in client.dm_events():
            sender = str(event.get("sender_id", ""))
            if sender != client.user_id and event.get("text", "").strip().casefold() == "stop":
                store.opt_out(sender)
        with store.connect() as conn:
            conn.execute("UPDATE social_deliveries SET status='uncertain' WHERE status='sending'")
            pending = [dict(r) for r in conn.execute(
                "SELECT * FROM social_deliveries WHERE status='pending' LIMIT 20"
            ).fetchall()]
        for delivery in pending:
            item = {
                "id": delivery["source_comment_id"], "author_id": delivery["user_id"],
                "text": config["keyword"],
                "referenced_tweets": [{"type": "replied_to", "id": delivery["root_id"]}],
            }
            result["sent"] += process_comment(item, roots, client, store, config) == "sent"
        since = store.get_state("x_mentions_since_id")
        newest = int(since or 0)
        for item in client.mentions(since):
            result["sent"] += process_comment(item, roots, client, store, config) == "sent"
            newest = max(newest, int(item["id"]))
        if newest:
            store.set_state("x_mentions_since_id", str(newest))
    except XError as exc:
        result["blocked"] = str(exc)
    return result


def main():
    parser = argparse.ArgumentParser(description="Configurer la séquence email Studio IA.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--configure-kit", action="store_true")
    args = parser.parse_args()
    if args.configure_kit:
        print(json.dumps(configure_kit(FunnelStore(args.data_dir / "v2.db"), read_config()), ensure_ascii=False))
    else:
        print(json.dumps(readiness(args.data_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
