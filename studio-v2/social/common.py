import json
import os
import re
import secrets
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PARIS = ZoneInfo("Europe/Paris")


def now_utc():
    return datetime.now(timezone.utc)


def read_config(path=None):
    config = json.loads(Path(path or ROOT / "social" / "funnel.json").read_text(encoding="utf-8"))
    for key, env in (
        ("landing_url", "STUDIO_LANDING_URL"),
        ("checkout_url", "STUDIO_CHECKOUT_URL"),
        ("privacy_url", "STUDIO_PRIVACY_URL"),
    ):
        if os.environ.get(env):
            config[key] = os.environ[env].strip()
    return config


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked_queue(data_dir):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = (data_dir / "x_queue.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            lock.seek(0, 2)
            if not lock.tell():
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        lock.close()
        raise RuntimeError("Un autre processus utilise déjà la file X.") from None
    try:
        path = data_dir / "x_queue.json"
        queue = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "version": 1, "posts": [], "episodes": {},
        }
        yield queue
        atomic_json(path, queue)
    finally:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def allocate_slots(posts, count, now, jitter_minutes=None):
    if now.tzinfo is None:
        raise ValueError("Le planning exige une date avec fuseau horaire.")
    occupied = {
        datetime.fromisoformat(p["scheduled_at"]).astimezone(PARIS).strftime("%Y-%m-%d:%H")
        for p in posts if p.get("scheduled_at") and p.get("status") != "cancelled"
    }
    date = now.astimezone(PARIS).date()
    slots = []
    while len(slots) < count:
        for hour in (9, 14, 18):
            key = f"{date.isoformat()}:{hour:02d}"
            delay = jitter_minutes if jitter_minutes is not None else 5 + secrets.randbelow(11)
            if not 5 <= delay <= 15:
                raise ValueError("Le décalage doit être compris entre 5 et 15 minutes.")
            slot = datetime(date.year, date.month, date.day, hour, delay, tzinfo=PARIS)
            if key not in occupied and slot > now:
                slots.append(slot.isoformat())
                occupied.add(key)
                if len(slots) == count:
                    return slots
        date += timedelta(days=1)
    return slots


def weighted_length(text):
    # X twitter-text: URLs have transformedURLLength=23; these Unicode ranges weigh 1.
    stripped = re.sub(r"https?://[^\s]+", "x" * 23, text)
    return sum(
        1 if ord(c) <= 0x10FF or 0x2000 <= ord(c) <= 0x200D
        or 0x2010 <= ord(c) <= 0x201F or 0x2032 <= ord(c) <= 0x2037 else 2
        for c in stripped
    )


def shorten(text, limit):
    text = " ".join(text.split())
    while text and weighted_length(text) > limit:
        text = text[:-1]
    return text.strip()
