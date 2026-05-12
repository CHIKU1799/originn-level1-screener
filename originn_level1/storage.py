"""
SQLite persistence for the Originn Level 1 screener. Two tables:

  level1_screenings  — final screening results (full payload as JSON blob)
  level1_jobs        — async job queue state for /screen submissions

Schema is intentionally close to what a Postgres migration would need so
that future port stays mechanical.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config
from .models import (
    JobStatus,
    ScreeningJob,
    ScreeningResult,
    TriageOutcome,
)

logger = logging.getLogger(__name__)


_DB_PATH = (
    Path(config.DB_PATH)
    if config.DB_PATH
    else Path(__file__).parent / "screenings.db"
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS level1_screenings (
                id              TEXT PRIMARY KEY,
                startup_name    TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                composite_score REAL NOT NULL,
                triage          TEXT NOT NULL,
                deck_attached   INTEGER NOT NULL DEFAULT 0,
                data            TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_lv1_startup ON level1_screenings(startup_name);
            CREATE INDEX IF NOT EXISTS idx_lv1_created ON level1_screenings(created_at);
            CREATE INDEX IF NOT EXISTS idx_lv1_triage  ON level1_screenings(triage);
            CREATE INDEX IF NOT EXISTS idx_lv1_score   ON level1_screenings(composite_score);

            CREATE TABLE IF NOT EXISTS level1_jobs (
                job_id          TEXT PRIMARY KEY,
                submitted_at    TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                status          TEXT NOT NULL,
                deck_attached   INTEGER NOT NULL DEFAULT 0,
                form_json       TEXT NOT NULL,
                deck_text       TEXT,
                result_id       TEXT,
                error           TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_lv1_job_status  ON level1_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_lv1_job_updated ON level1_jobs(updated_at);
        """)


_init_db()


# ─────────────────────────────────────────────
# Screenings
# ─────────────────────────────────────────────

def save_screening(result: ScreeningResult) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO level1_screenings
              (id, startup_name, created_at, composite_score, triage,
               deck_attached, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.id,
                result.startup_name,
                result.created_at.isoformat(),
                result.composite_score,
                result.triage.value,
                1 if result.deck_attached else 0,
                result.model_dump_json(),
            ),
        )


def get_screening(screening_id: str) -> Optional[ScreeningResult]:
    if not screening_id:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT data FROM level1_screenings WHERE id = ?",
            (screening_id,),
        ).fetchone()
    return ScreeningResult.model_validate_json(row["data"]) if row else None


def list_screenings(
    *,
    startup_name: Optional[str] = None,
    triage: Optional[TriageOutcome] = None,
    min_score: Optional[float] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Lightweight list (does not deserialise the full payload)."""
    clauses, params = [], []
    if startup_name:
        clauses.append("startup_name LIKE ?")
        params.append(f"%{startup_name}%")
    if triage:
        clauses.append("triage = ?")
        params.append(triage.value)
    if min_score is not None:
        clauses.append("composite_score >= ?")
        params.append(min_score)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.extend([limit, offset])
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, startup_name, created_at, composite_score, triage,
                   deck_attached
            FROM level1_screenings
            {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# Jobs
# ─────────────────────────────────────────────

def save_job(job: ScreeningJob, *, form_json: str, deck_text: Optional[str]) -> None:
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO level1_jobs
              (job_id, submitted_at, updated_at, status, deck_attached,
               form_json, deck_text, result_id, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.job_id,
                job.submitted_at.isoformat(),
                now,
                job.status.value,
                1 if job.deck_attached else 0,
                form_json,
                deck_text,
                job.result_id,
                job.error,
            ),
        )


def update_job_status(
    job_id: str,
    status: JobStatus,
    *,
    result_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE level1_jobs
            SET status = ?, updated_at = ?,
                result_id = COALESCE(?, result_id),
                error     = COALESCE(?, error)
            WHERE job_id = ?
            """,
            (status.value, now, result_id, error, job_id),
        )


def get_job(job_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM level1_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return dict(row) if row else None


def list_jobs(
    *,
    status: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT job_id, submitted_at, updated_at, status,
                   deck_attached, result_id, error
            FROM level1_jobs
            {where}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]
