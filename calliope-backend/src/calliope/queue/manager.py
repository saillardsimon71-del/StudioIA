"""SQLite-backed job queue manager."""
from __future__ import annotations

import json
import logging
from typing import Any

from calliope import config
from calliope.db import get_db, row_to_dict

logger = logging.getLogger("calliope.queue")


def modal_call_unfinished(take):
    return bool(
        take['modal_call_id']
        and not json.loads(take['render_state_json'] or '{}').get('remote_terminal')
        and not (take['status'] == 'done' and take['output_path'])
    )


class QueueManager:
    def __init__(self) -> None:
        self.paused = False

    def reset_stale_jobs(self) -> int:
        conn = get_db(config.settings.db_path)
        try:
            cur = conn.execute(
                "UPDATE jobs SET status = 'pending', started_at = NULL WHERE status = 'running'"
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def enqueue(
        self,
        *,
        project_id: int,
        kind: str,
        workflow_id: int | None = None,
        scene_id: int | None = None,
        clip_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        conn = get_db(config.settings.db_path)
        try:
            cur = conn.execute(
                """
                INSERT INTO jobs (project_id, scene_id, clip_id, kind, workflow_id, status, payload_json)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (project_id, scene_id, clip_id, kind, workflow_id, json.dumps(payload or {})),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (cur.lastrowid,)).fetchone()
            return row_to_dict(row)
        finally:
            conn.close()

    def claim_next(self) -> dict[str, Any] | None:
        if self.paused:
            return None
        conn = get_db(config.settings.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'pending' "
                "AND json_extract(payload_json,'$.parent_job_id') IS NULL "
                "ORDER BY created_at ASC,id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            job_id = row["id"]
            cur = conn.execute(
                """
                UPDATE jobs SET status = 'running', started_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
                """,
                (job_id,),
            )
            conn.commit()
            if not cur.rowcount:
                return None
            claimed = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return row_to_dict(claimed) if claimed else None
        finally:
            conn.close()

    def mark_done(self, job_id: int, output_paths: list[str]) -> None:
        conn = get_db(config.settings.db_path)
        try:
            conn.execute(
                """
                UPDATE jobs SET status = 'done', output_paths_json = ?, error = NULL,
                completed_at = CURRENT_TIMESTAMP WHERE id = ? AND status IN ('pending','running')
                """,
                (json.dumps(output_paths), job_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_failed(self, job_id: int, error: str) -> None:
        conn = get_db(config.settings.db_path)
        try:
            conn.execute(
                """
                UPDATE jobs SET status = 'failed', error = ?, completed_at = CURRENT_TIMESTAMP,
                retry_count = retry_count + 1 WHERE id = ? AND error IS NOT 'cancelled'
                """,
                (error[:2000], job_id),
            )
            conn.commit()
        finally:
            conn.close()

    def retry(self, job_id: int) -> dict[str, Any] | None:
        conn = get_db(config.settings.db_path)
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row or row['status'] != 'failed' or row['error'] == 'cancelled':
                return None
            conn.execute(
                """
                UPDATE jobs SET status = 'pending', error = NULL, started_at = NULL,
                completed_at = NULL WHERE id = ? AND status='failed' AND error IS NOT 'cancelled'
                """,
                (job_id,),
            )
            conn.commit()
            return row_to_dict(
                conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            )
        finally:
            conn.close()

    def cancel(self, job_id: int) -> bool:
        conn = get_db(config.settings.db_path)
        try:
            conn.execute('BEGIN IMMEDIATE')
            jobs = conn.execute(
                "SELECT * FROM jobs WHERE id=? OR json_extract(payload_json,'$.parent_job_id')=?",
                (job_id, job_id),
            ).fetchall()
            cancelled = []
            for job in jobs:
                take = conn.execute(
                    'SELECT * FROM studio_takes WHERE job_id=?', (job['id'],),
                ).fetchone()
                if job['status'] in ('pending', 'running') or (
                    job['status'] == 'failed' and take and modal_call_unfinished(take)
                ):
                    cancelled.append(job['id'])
            parent = next((job for job in jobs if job['id'] == job_id), None)
            if cancelled and parent and parent['status'] == 'failed' and job_id not in cancelled:
                cancelled.append(job_id)
            for identifier in cancelled:
                conn.execute(
                    "UPDATE jobs SET status='failed',error='cancelled',"
                    "completed_at=CURRENT_TIMESTAMP "
                    'WHERE id=?', (identifier,),
                )
            conn.commit()
            return bool(cancelled)
        finally:
            conn.close()

    def cancel_by_session(self, session_id: int) -> list[int]:
        """Cancel every pending/running job whose payload carries this
        session_id (agent-enqueued jobs stamp it). Returns the cancelled ids."""
        conn = get_db(config.settings.db_path)
        try:
            rows = conn.execute(
                "SELECT id, payload_json FROM jobs WHERE status IN ('pending', 'running')"
            ).fetchall()
            cancelled: list[int] = []
            for r in rows:
                try:
                    payload = json.loads(r["payload_json"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue
                if payload.get("session_id") == session_id:
                    conn.execute(
                        """
                        UPDATE jobs SET status = 'failed', error = 'cancelled',
                        completed_at = CURRENT_TIMESTAMP WHERE id = ?
                        """,
                        (r["id"],),
                    )
                    cancelled.append(r["id"])
            conn.commit()
            return cancelled
        finally:
            conn.close()

    def is_cancelled(self, job_id: int) -> bool:
        conn = get_db(config.settings.db_path)
        try:
            row = conn.execute(
                "SELECT status FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return bool(row) and row["status"] not in ("pending", "running")
        finally:
            conn.close()

    def list_jobs(
        self, project_id: int | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        conn = get_db(config.settings.db_path)
        try:
            clauses: list[str] = []
            params: list[Any] = []
            if project_id is not None:
                clauses.append("project_id = ?")
                params.append(project_id)
            if status:
                clauses.append("status = ?")
                params.append(status)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.append(limit)
            rows = conn.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        conn = get_db(config.settings.db_path)
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return row_to_dict(row) if row else None
        finally:
            conn.close()

    def delete_job(self, job_id: int) -> dict[str, Any] | None:
        """Remove job row. Returns the deleted job dict, or None if missing."""
        conn = get_db(config.settings.db_path)
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return None
            job = row_to_dict(row)
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()
            return job
        finally:
            conn.close()


queue_manager = QueueManager()
