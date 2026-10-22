"""Persistance SQLite minimale (`studio-v2/data/v2.db`).

Quatre tables sans duplication du scénario :
- `projects`       : JSON canonique validé par `Project` (`core/scenario.py`)
- `library_assets` : bibliothèque visuelle persistante (personnages, décors, images initiales)
- `takes`          : historique des rendus, autorisations, coûts estimés/réels, erreurs et QC
- `exports`        : exports MP4 assemblés et leurs rapports QC
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.scenario import Project

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    data_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS library_assets (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('character', 'decor', 'frame')),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'legacy'
);

-- Une conversation Meta AI ne peut produire qu'une seule image de bibliothèque :
-- la contrainte est en base, donc elle survit aux redémarrages du service.
CREATE TABLE IF NOT EXISTS metaai_generations (
    request_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_asset_per_metaai_conversation
    ON metaai_generations (conversation_id);

CREATE TABLE IF NOT EXISTS takes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    shot_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed', 'imported')),
    seed INTEGER NOT NULL DEFAULT 42,
    frames INTEGER NOT NULL DEFAULT 0,
    ltx_prompt TEXT NOT NULL DEFAULT '',
    estimated_cost_eur REAL,
    max_budget_eur REAL,
    authorized_by_human INTEGER NOT NULL DEFAULT 0,
    observed_compute_s REAL,
    actual_cost_eur REAL,
    video_path TEXT,
    error_message TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    qc_json TEXT NOT NULL DEFAULT '{}',
    human_approved INTEGER NOT NULL DEFAULT 0,
    human_notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_eur REAL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    raw_content TEXT
);

CREATE TABLE IF NOT EXISTS render_jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    shot_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    modal_call_id TEXT,
    request_json TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_render ON render_jobs ((1)) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    output_path TEXT NOT NULL,
    duration_s REAL NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    qc_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ConversationReuseError(ValueError):
    """La conversation Meta AI a déjà produit une image : provenance non fiable."""


class StudioRepository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            if "raw_content" not in {row["name"] for row in conn.execute("PRAGMA table_info(llm_runs)")}:
                conn.execute("ALTER TABLE llm_runs ADD COLUMN raw_content TEXT")
            self._migrate_library_assets(conn)

    @staticmethod
    def _migrate_library_assets(conn: sqlite3.Connection) -> None:
        """Ajoute les colonnes de provenance sans réécrire les images existantes.

        Les visuels antérieurs sont marqués ``legacy`` : ils restent utilisables
        et référencés, mais aucun n'est présenté comme provenant d'une requête
        vérifiée.
        """
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(library_assets)")}
        if "provenance_json" not in columns:
            conn.execute("ALTER TABLE library_assets ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'")
        if "source" not in columns:
            conn.execute("ALTER TABLE library_assets ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Projects (source unique de vérité) ───────────────────────────────────

    def save_project(self, project: Project) -> Project:
        validated = Project.model_validate(project.model_dump())
        now = utc_now()
        payload = json.dumps(validated.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM projects WHERE id = ?", (validated.id,)
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO projects (id, title, created_at, updated_at, data_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    updated_at = excluded.updated_at,
                    data_json = excluded.data_json
                """,
                (validated.id, validated.title or validated.idea[:80], created_at, now, payload),
            )
            for shot in validated.shots:
                if shot.rendered_video_ref:
                    conn.execute(
                        """UPDATE render_jobs SET status = 'completed'
                           WHERE project_id = ? AND shot_id = ? AND status = 'pending'
                             AND id IN (SELECT id FROM takes WHERE status = 'completed' AND video_path = ?)""",
                        (validated.id, shot.id, shot.rendered_video_ref),
                    )
        return validated

    def get_project(self, project_id: str) -> Project | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data_json FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if not row:
            return None
        return Project.model_validate_json(row["data_json"])

    def list_projects(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at, data_json FROM projects ORDER BY updated_at DESC"
            ).fetchall()
        items = []
        for row in rows:
            proj = Project.model_validate_json(row["data_json"])
            items.append(
                {
                    "id": proj.id,
                    "title": proj.title or proj.idea[:60],
                    "idea": proj.idea,
                    "shot_count": len(proj.shots),
                    "character_count": len(proj.characters),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return items

    def delete_project(self, project_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cur.rowcount > 0

    # ── Bibliothèque visuelle persistante (Personnages & Décors) ─────────────

    def add_asset(
        self,
        *,
        kind: str,
        name: str,
        file_path: Path,
        width: int,
        height: int,
        description: str = "",
        asset_id: str | None = None,
        provenance: dict | None = None,
        source: str = "import",
    ) -> dict:
        aid = asset_id or f"asset-{uuid.uuid4().hex[:10]}"
        digest = sha256_file(file_path)
        now = utc_now()
        record = {
            "id": aid,
            "kind": kind,
            "name": name,
            "description": description,
            "file_path": str(Path(file_path).resolve()),
            "sha256": digest,
            "width": width,
            "height": height,
            "created_at": now,
            "provenance_json": json.dumps(provenance or {}, ensure_ascii=False),
            "source": source,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO library_assets
                (id, kind, name, description, file_path, sha256, width, height, created_at,
                 provenance_json, source)
                VALUES (:id, :kind, :name, :description, :file_path, :sha256, :width, :height,
                        :created_at, :provenance_json, :source)
                """,
                record,
            )
        return self._asset_dict(record)

    def add_verified_asset(
        self,
        *,
        kind: str,
        name: str,
        description: str,
        file_path: Path,
        width: int,
        height: int,
        provenance: dict,
        asset_id: str | None = None,
    ) -> dict:
        """Enregistre une image Meta AI et sa preuve dans une seule transaction.

        Si la conversation a déjà servi, aucune ligne n'est créée : la bibliothèque
        reste protégée contre les références non vérifiées.
        """
        request_id = str(provenance.get("request_id") or "").strip()
        conversation_id = str(provenance.get("conversation_id") or "").strip()
        if not request_id or not conversation_id:
            raise ConversationReuseError("Provenance incomplète : requête ou conversation absente.")
        aid = asset_id or f"asset-{uuid.uuid4().hex[:10]}"
        digest = sha256_file(file_path)
        now = utc_now()
        record = {
            "id": aid,
            "kind": kind,
            "name": name,
            "description": description,
            "file_path": str(Path(file_path).resolve()),
            "sha256": digest,
            "width": width,
            "height": height,
            "created_at": now,
            "provenance_json": json.dumps(provenance, ensure_ascii=False),
            "source": "metaai",
        }
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO metaai_generations (request_id, conversation_id, asset_id, prompt, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (request_id, conversation_id, aid, str(provenance.get("prompt") or ""), now),
                )
            except sqlite3.IntegrityError as exc:
                raise ConversationReuseError(
                    "Cette conversation Meta AI a déjà produit une image : référence non fiable refusée."
                ) from exc
            conn.execute(
                """
                INSERT INTO library_assets
                (id, kind, name, description, file_path, sha256, width, height, created_at,
                 provenance_json, source)
                VALUES (:id, :kind, :name, :description, :file_path, :sha256, :width, :height,
                        :created_at, :provenance_json, :source)
                """,
                record,
            )
        return self._asset_dict(record)

    @staticmethod
    def _asset_dict(record: dict) -> dict:
        item = dict(record)
        try:
            item["provenance"] = json.loads(item.pop("provenance_json", "{}") or "{}")
        except ValueError:
            item["provenance"] = {}
        item["verified"] = bool(
            item["provenance"].get("attribution") == "fresh_assistant_response"
            and item["provenance"].get("request_id")
            and item["provenance"].get("conversation_id")
        )
        return item

    def find_asset_by_sha256(self, digest: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM library_assets WHERE sha256 = ? ORDER BY created_at LIMIT 1", (digest,)
            ).fetchone()
        return self._asset_dict(dict(row)) if row else None

    def list_assets(self, kind: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if kind:
                rows = conn.execute(
                    "SELECT * FROM library_assets WHERE kind = ? ORDER BY created_at DESC",
                    (kind,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM library_assets ORDER BY created_at DESC"
                ).fetchall()
        return [self._asset_dict(dict(r)) for r in rows]

    def get_asset(self, asset_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM library_assets WHERE id = ?", (asset_id,)
            ).fetchone()
        return self._asset_dict(dict(row)) if row else None

    def asset_references(self, asset_id: str) -> list[dict]:
        """Recense les références projet à un asset (aucune écriture)."""
        asset = self.get_asset(asset_id)
        if asset is None:
            return []
        target = asset["file_path"]
        with self._connect() as conn:
            rows = conn.execute("SELECT id, data_json FROM projects").fetchall()
        references: list[dict] = []
        for row in rows:
            project = Project.model_validate_json(row["data_json"])
            for char in project.characters:
                if char.image_ref == target:
                    references.append({"project_id": project.id, "entity_type": "character",
                                       "entity_id": char.id, "label": char.name})
            for decor in project.decors:
                if decor.asset_id == asset_id:
                    references.append({"project_id": project.id, "entity_type": "decor",
                                       "entity_id": decor.id, "label": decor.name})
            for shot in project.shots:
                if shot.image_ref == target:
                    references.append({"project_id": project.id, "entity_type": "shot",
                                       "entity_id": shot.id, "label": shot.id})
                if shot.decor_asset_id == asset_id:
                    references.append({"project_id": project.id, "entity_type": "shot_decor",
                                       "entity_id": shot.id, "label": shot.id})
        return references

    def audit_assets(self) -> dict:
        """Contrôle en lecture seule : fichier présent, empreinte stable, provenance."""
        assets = self.list_assets()
        items: list[dict] = []
        for asset in assets:
            path = Path(asset["file_path"])
            exists = path.is_file()
            digest_ok = exists and sha256_file(path) == asset["sha256"]
            references = self.asset_references(asset["id"])
            issues: list[str] = []
            if not exists:
                issues.append("fichier manquant sur le disque")
            elif not digest_ok:
                issues.append("empreinte différente du fichier enregistré")
            if not asset["verified"]:
                issues.append("provenance Meta AI non vérifiée (import ou visuel historique)")
            items.append({**asset, "file_exists": exists, "sha256_matches": bool(digest_ok),
                          "references": references, "issues": issues})
        return {
            "assets": items,
            "unverified": sum(1 for item in items if not item["verified"]),
            "missing_files": sum(1 for item in items if not item["file_exists"]),
            "altered_files": sum(1 for item in items if item["file_exists"] and not item["sha256_matches"]),
        }

    def detach_asset_references(self, asset_id: str) -> int:
        """Retire les références d'un asset sans supprimer aucun fichier."""
        asset = self.get_asset(asset_id)
        if asset is None:
            return 0
        target = asset["file_path"]
        detached = 0
        with self._connect() as conn:
            rows = conn.execute("SELECT id, data_json FROM projects").fetchall()
            for row in rows:
                project = Project.model_validate_json(row["data_json"])
                changed = False
                for char in project.characters:
                    if char.image_ref == target:
                        char.image_ref = None
                        detached += 1
                        changed = True
                for decor in project.decors:
                    if decor.asset_id == asset_id:
                        decor.asset_id = None
                        detached += 1
                        changed = True
                for shot in project.shots:
                    if shot.image_ref == target:
                        shot.image_ref = None
                        shot.image_validated = False
                        detached += 1
                        changed = True
                    if shot.decor_asset_id == asset_id:
                        shot.decor_asset_id = None
                        detached += 1
                        changed = True
                if changed:
                    now = utc_now()
                    conn.execute(
                        "UPDATE projects SET updated_at = ?, data_json = ? WHERE id = ?",
                        (now, json.dumps(project.model_dump(mode="json"), ensure_ascii=False), project.id),
                    )
        return detached

    def delete_asset(self, asset_id: str) -> dict:
        """Retire un asset de la bibliothèque, détache ses références, conserve le fichier."""
        asset = self.get_asset(asset_id)
        if asset is None:
            return {"deleted": False, "detached_references": 0, "file_preserved": True}
        detached = self.detach_asset_references(asset_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM library_assets WHERE id = ?", (asset_id,))
        return {"deleted": True, "detached_references": detached, "file_preserved": True}

    def list_metaai_generations(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM metaai_generations ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    # ── Prises GPU / Imports & Coûts réels ───────────────────────────────────

    def pending_render(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM render_jobs WHERE status = 'pending'").fetchone()
        if not row:
            return None
        job = dict(row)
        job["request"] = json.loads(job.pop("request_json"))
        return job

    def start_render(self, take_id: str, project_id: str, shot_id: str, request: dict) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO render_jobs (id, project_id, shot_id, request_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (take_id, project_id, shot_id, json.dumps(request, ensure_ascii=False), utc_now()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Un rendu GPU est deja en cours. Recuperez cette prise avant d'en lancer une autre.") from exc

    def update_render(self, take_id: str, *, call_id: str | None = None, status: str = "pending",
                      error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE render_jobs SET modal_call_id = COALESCE(?, modal_call_id), status = ?, error_message = ? WHERE id = ?",
                (call_id, status, error, take_id),
            )

    def record_take(
        self,
        *,
        project_id: str,
        shot_id: str,
        status: str,
        seed: int = 42,
        frames: int = 0,
        ltx_prompt: str = "",
        estimated_cost_eur: float | None = None,
        max_budget_eur: float | None = None,
        authorized_by_human: bool = False,
        observed_compute_s: float | None = None,
        actual_cost_eur: float | None = None,
        video_path: str | None = None,
        error_message: str | None = None,
        metadata: dict | None = None,
        qc: dict | None = None,
        take_id: str | None = None,
    ) -> dict:
        tid = take_id or f"take-{uuid.uuid4().hex[:10]}"
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO takes (
                    id, project_id, shot_id, status, seed, frames, ltx_prompt,
                    estimated_cost_eur, max_budget_eur, authorized_by_human,
                    observed_compute_s, actual_cost_eur, video_path, error_message,
                    metadata_json, qc_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid,
                    project_id,
                    shot_id,
                    status,
                    seed,
                    frames,
                    ltx_prompt,
                    estimated_cost_eur,
                    max_budget_eur,
                    int(authorized_by_human),
                    observed_compute_s,
                    actual_cost_eur,
                    video_path,
                    error_message,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    json.dumps(qc or {}, ensure_ascii=False),
                    now,
                ),
            )
        return self.get_take(tid)  # type: ignore[return-value]

    def update_take_qc(
        self,
        take_id: str,
        *,
        qc: dict | None = None,
        human_approved: bool | None = None,
        human_notes: str | None = None,
        actual_cost_eur: float | None = None,
    ) -> dict | None:
        current = self.get_take(take_id)
        if not current:
            return None
        new_qc = qc if qc is not None else current["qc"]
        approved = (
            int(human_approved)
            if human_approved is not None
            else int(current["human_approved"])
        )
        notes = human_notes if human_notes is not None else current["human_notes"]
        cost = (
            actual_cost_eur
            if actual_cost_eur is not None
            else current["actual_cost_eur"]
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE takes
                SET qc_json = ?, human_approved = ?, human_notes = ?, actual_cost_eur = ?
                WHERE id = ?
                """,
                (json.dumps(new_qc, ensure_ascii=False), approved, notes, cost, take_id),
            )
        return self.get_take(take_id)

    def get_take(self, take_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM takes WHERE id = ?", (take_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["authorized_by_human"] = bool(data["authorized_by_human"])
        data["human_approved"] = bool(data["human_approved"])
        data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
        data["qc"] = json.loads(data.pop("qc_json") or "{}")
        return data

    def list_takes(self, project_id: str, shot_id: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if shot_id:
                rows = conn.execute(
                    "SELECT id FROM takes WHERE project_id = ? AND shot_id = ? ORDER BY created_at DESC",
                    (project_id, shot_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM takes WHERE project_id = ? ORDER BY created_at DESC",
                    (project_id,),
                ).fetchall()
        return [self.get_take(r["id"]) for r in rows if r["id"]]  # type: ignore[misc]

    def observed_gpu_stats(self) -> dict:
        """Retourne les durées GPU réellement observées sur Modal si disponibles."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT frames, observed_compute_s, actual_cost_eur
                FROM takes
                WHERE status = 'completed' AND observed_compute_s IS NOT NULL AND frames > 0
                ORDER BY created_at DESC
                LIMIT 20
                """
            ).fetchall()
        if not rows:
            return {"count": 0, "avg_sec_per_frame": None, "avg_compute_s": None}
        sec_per_frame = [r["observed_compute_s"] / r["frames"] for r in rows]
        compute = [r["observed_compute_s"] for r in rows]
        return {
            "count": len(rows),
            "avg_sec_per_frame": sum(sec_per_frame) / len(sec_per_frame),
            "avg_compute_s": sum(compute) / len(compute),
        }

    # ── Appels DeepSeek & Exports MP4 ────────────────────────────────────────

    def record_llm_run(
        self,
        *,
        project_id: str,
        model: str,
        status: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        estimated_cost_eur: float | None = None,
        error_message: str | None = None,
        raw_content: str | None = None,
    ) -> dict:
        rid = f"llm-{uuid.uuid4().hex[:10]}"
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_runs
                (id, project_id, model, status, prompt_tokens, completion_tokens,
                 estimated_cost_eur, error_message, created_at, raw_content)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rid,
                    project_id,
                    model,
                    status,
                    prompt_tokens,
                    completion_tokens,
                    estimated_cost_eur,
                    error_message,
                    now,
                    raw_content,
                ),
            )
        return {
            "id": rid,
            "project_id": project_id,
            "model": model,
            "status": status,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost_eur": estimated_cost_eur,
            "error_message": error_message,
            "created_at": now,
        }

    def record_export(
        self,
        *,
        project_id: str,
        output_path: Path,
        duration_s: float,
        width: int,
        height: int,
        qc: dict,
    ) -> dict:
        eid = f"export-{uuid.uuid4().hex[:10]}"
        now = utc_now()
        resolved = str(Path(output_path).resolve())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO exports (id, project_id, output_path, duration_s, width, height, qc_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eid,
                    project_id,
                    resolved,
                    duration_s,
                    width,
                    height,
                    json.dumps(qc, ensure_ascii=False),
                    now,
                ),
            )
        return {
            "id": eid,
            "project_id": project_id,
            "output_path": resolved,
            "duration_s": duration_s,
            "width": width,
            "height": height,
            "qc": qc,
            "created_at": now,
        }

    def list_exports(self, project_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM exports WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["qc"] = json.loads(item.pop("qc_json") or "{}")
            out.append(item)
        return out

    def cost_ledger(self) -> dict:
        with self._connect() as conn:
            takes = [dict(r) for r in conn.execute("SELECT * FROM takes ORDER BY created_at DESC").fetchall()]
            llms = [dict(r) for r in conn.execute("SELECT * FROM llm_runs ORDER BY created_at DESC").fetchall()]
        total_gpu_eur = sum(t["actual_cost_eur"] or 0.0 for t in takes)
        total_llm_eur = sum(l["estimated_cost_eur"] or 0.0 for l in llms)
        return {
            "total_actual_gpu_eur": round(total_gpu_eur, 4),
            "total_estimated_llm_eur": round(total_llm_eur, 5),
            "completed_takes": sum(1 for t in takes if t["status"] == "completed"),
            "failed_takes": sum(1 for t in takes if t["status"] == "failed"),
            "llm_calls": len(llms),
            "takes": takes,
            "llm_runs": llms,
        }
