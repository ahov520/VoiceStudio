"""
Job-metadata persistence — Phase 2.1 (ROADMAP.md).

Stores `jobs` (state machine) + `job_events` (SSE tail) so tasks survive
restart and SSE clients can reconnect with ?after_seq=N and get a replay.

This module intentionally does NOT persist the work itself — the async queue
+ in-memory dispatcher stay as-is. That's Phase 4.5 ("step-level resumability").
Here we just keep the metadata honest across restarts:

    • server crash mid-job       → next startup marks it failed with a clear message
    • browser reload mid-stream  → reconnect and replay the SSE tail from disk
    • UI post-restart            → queryable `jobs` table fills the batch-queue view

The tables (`jobs`, `job_events`) live in `core/db.py:_BASE_SCHEMA`.

Retention: we cap `job_events` per job (default 500 rows) by trimming the
oldest on every insert above the cap. Keeps the DB bounded.
"""
from __future__ import annotations

import json
import logging
import math
import time
from typing import Optional

from core.db import db_conn

logger = logging.getLogger("omnivoice.jobs")

# Per-job event cap. Above this, the oldest row is dropped on every insert.
_EVENT_CAP_PER_JOB = 500


# ── Lifecycle ──────────────────────────────────────────────────────────────


def create(job_id: str, *, type: str, project_id: Optional[str] = None, meta: Optional[dict] = None) -> None:
    now = time.time()
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO jobs "
            "(id, type, project_id, status, created_at, updated_at, meta_json) "
            "VALUES (?, ?, ?, 'pending', ?, ?, ?)",
            (job_id, type, project_id, now, now, json.dumps(meta or {})),
        )


def mark_running(job_id: str) -> None:
    _update_status(job_id, "running")


def mark_done(job_id: str) -> None:
    _update_status(job_id, "done", finished=True, progress=1.0, stage="done")


def mark_failed(job_id: str, error: str) -> None:
    _update_status(job_id, "failed", finished=True, error=error, stage="failed")


def mark_cancelled(job_id: str) -> None:
    _update_status(job_id, "cancelled", finished=True, stage="cancelled")


def update_progress(job_id: str, progress: float, stage: Optional[str] = None) -> None:
    """Persist progress without changing status.

    ``stage=None`` means the producer only reported a numeric update; retain
    the last known stage so reconnecting clients keep a useful description.
    Pass an empty string explicitly when a producer intentionally clears it.
    """
    try:
        value = max(0.0, min(1.0, float(progress)))
    except (TypeError, ValueError, OverflowError):
        return
    if not math.isfinite(value):
        return
    with db_conn() as conn:
        if stage is None:
            conn.execute(
                "UPDATE jobs SET progress=?, updated_at=? "
                "WHERE id=? AND status NOT IN ('done', 'failed', 'cancelled')",
                (value, time.time(), job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET progress=?, stage=?, updated_at=? "
                "WHERE id=? AND status NOT IN ('done', 'failed', 'cancelled')",
                (value, str(stage)[:200], time.time(), job_id),
            )


def _update_status(
    job_id: str,
    status: str,
    *,
    finished: bool = False,
    error: Optional[str] = None,
    progress: Optional[float] = None,
    stage: Optional[str] = None,
) -> None:
    now = time.time()
    log_context = {
        "operation": "job_status_update",
        "task_id": job_id,
        "task_status": status,
    }
    try:
        with db_conn() as conn:
            if finished:
                if progress is not None:
                    cur = conn.execute(
                        "UPDATE jobs SET status=?, updated_at=?, finished_at=?, error=?, "
                        "progress=?, stage=? WHERE id=?",
                        (status, now, now, error, progress, stage or "", job_id),
                    )
                elif stage is not None:
                    cur = conn.execute(
                        "UPDATE jobs SET status=?, updated_at=?, finished_at=?, error=?, "
                        "stage=? WHERE id=?",
                        (status, now, now, error, stage, job_id),
                    )
                else:
                    cur = conn.execute(
                        "UPDATE jobs SET status=?, updated_at=?, finished_at=?, error=? WHERE id=?",
                        (status, now, now, error, job_id),
                    )
            else:
                cur = conn.execute(
                    "UPDATE jobs SET status=?, updated_at=? WHERE id=?",
                    (status, now, job_id),
                )
            if cur.rowcount != 1:
                logger.error("Task status update affected no row", extra=log_context)
    except Exception:
        logger.exception("Task status persistence failed", extra=log_context)
        raise


# ── Events ─────────────────────────────────────────────────────────────────


def append_event(job_id: str, payload: str) -> int:
    """Persist one SSE event. Returns the new `seq` number.

    `payload` is the raw SSE line (e.g. `data: {...}\\n\\n`). The schema keeps
    it opaque so future event shapes don't require migrations.
    """
    now = time.time()
    with db_conn() as conn:
        # A plain SELECT does not start a SQLite write transaction. Without
        # taking the reservation lock first, concurrent writers can observe
        # the same MAX(seq) and collide on the next sequence number.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS s FROM job_events WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        next_seq = int(row["s"]) + 1
        conn.execute(
            "INSERT INTO job_events (job_id, seq, created_at, payload) VALUES (?, ?, ?, ?)",
            (job_id, next_seq, now, payload),
        )
        # Trim oldest beyond the cap. Cheap: bounded by _EVENT_CAP_PER_JOB.
        cnt = conn.execute(
            "SELECT COUNT(*) AS n FROM job_events WHERE job_id = ?",
            (job_id,),
        ).fetchone()["n"]
        if cnt > _EVENT_CAP_PER_JOB:
            conn.execute(
                "DELETE FROM job_events WHERE job_id = ? AND seq IN "
                "(SELECT seq FROM job_events WHERE job_id = ? ORDER BY seq ASC LIMIT ?)",
                (job_id, job_id, cnt - _EVENT_CAP_PER_JOB),
            )
    return next_seq


def events_since(job_id: str, after_seq: int = 0, limit: int = 1000) -> list[dict]:
    """Return `[{seq, created_at, payload}]` for events with seq > after_seq."""
    # Query parameters arrive as strings and reconnect clients occasionally
    # send a stale/negative cursor. Normalize it here so direct callers and
    # HTTP routes share the same predictable contract instead of relying on
    # SQLite's implicit type coercion.
    try:
        after_seq = max(0, int(after_seq))
    except (TypeError, ValueError, OverflowError):
        after_seq = 0
    # SQLite treats a negative LIMIT as "no limit".  Keep this low-level API
    # bounded even when called outside the HTTP router (which has its own cap).
    try:
        limit = max(1, min(2000, int(limit)))
    except (TypeError, ValueError):
        limit = 1000
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT seq, created_at, payload FROM job_events "
            "WHERE job_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
            (job_id, after_seq, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Queries ────────────────────────────────────────────────────────────────


def get(job_id: str) -> Optional[dict]:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(*, status: Optional[str] = None, project_id: Optional[str] = None, limit: int = 100) -> list[dict]:
    """List jobs, newest first. Filter by status (e.g. `active` = running+pending) or project."""
    # SQLite interprets a negative LIMIT as "no limit"; enforce the API's
    # bounded result contract for direct callers as well as HTTP routes.
    try:
        limit = max(1, min(500, int(limit)))
    except (TypeError, ValueError):
        limit = 100
    where = []
    params = []
    if status == "active":
        where.append("status IN ('pending', 'running')")
    elif status:
        where.append("status = ?")
        params.append(status)
    if project_id:
        where.append("project_id = ?")
        params.append(project_id)
    sql = "SELECT * FROM jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── Startup recovery ──────────────────────────────────────────────────────


def sweep_orphans_on_startup() -> int:
    """Any job marked `pending` or `running` when the server starts is orphaned
    (previous process died before finishing). Flip to `failed` with a clear
    error so the UI shows the right state instead of a fake spinner.

    Returns the number of jobs swept.
    """
    msg = "Job was interrupted by a server restart. Re-run from the task's project to continue."
    now = time.time()
    with db_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status='failed', updated_at=?, finished_at=?, error=?, stage='failed' "
            "WHERE status IN ('pending', 'running')",
            (now, now, msg),
        )
        n = cur.rowcount
    if n:
        logger.info("Job sweep: marked %d orphaned job(s) as failed after restart.", n)
    return n
