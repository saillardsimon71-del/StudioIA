import hashlib
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]


def source_revision():
    digest = hashlib.sha256()
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        digest.update(path.relative_to(SOURCE_ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


LOADED_REVISION = source_revision()
