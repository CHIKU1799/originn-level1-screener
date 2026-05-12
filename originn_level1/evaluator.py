"""
Orchestrator for the Originn Level 1 screener.

Public surface:
  screen_sync(form, deck_text=None)        — inline run, returns ScreeningResult
  submit_screening(form, deck_text=None)   — queues a ScreeningJob, returns it
  start_workers(app=None)                  — spawn background workers

Level 1 is light enough that the queue is mostly a uniformity convenience —
the actual run is two LLM calls and some graph math. We still queue so the
HTTP endpoint can return 202 immediately and clients can poll for status.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from datetime import datetime

from . import config
from .agent import run_level1
from .models import (
    DimensionScore,
    JobStatus,
    KGDump,
    Level1Form,
    ScreeningJob,
    ScreeningResult,
    TriageOutcome,
)
from .storage import (
    get_job,
    get_screening,
    save_job,
    save_screening,
    update_job_status,
)

logger = logging.getLogger(__name__)


class QueueFullError(Exception):
    pass


# ─────────────────────────────────────────────
# Worker queue
# ─────────────────────────────────────────────

_queue: asyncio.Queue[tuple[ScreeningJob, Level1Form, str | None]] = asyncio.Queue(
    maxsize=config.MAX_QUEUE_DEPTH
)
_workers_started = False


async def _process(
    job: ScreeningJob,
    form: Level1Form,
    deck_text: str | None,
) -> None:
    try:
        update_job_status(job.job_id, JobStatus.EXTRACTING)
        agent_result = await run_level1(form, deck_text)

        update_job_status(job.job_id, JobStatus.SCORING)
        # Scoring + narration are inside run_level1; the status update is
        # cosmetic — useful for client progress bars.
        update_job_status(job.job_id, JobStatus.NARRATING)

        dim_scores: dict[str, DimensionScore] = agent_result.dimension_scores
        result = ScreeningResult(
            id=str(uuid.uuid4()),
            created_at=datetime.utcnow(),
            deck_attached=deck_text is not None,
            startup_name=form.startup_name,
            composite_score=agent_result.composite_score,
            triage=TriageOutcome(agent_result.triage),
            dimension_scores=dim_scores,
            raw_signals=agent_result.raw_signals,
            narrative=agent_result.narrative,
            kg_dump=agent_result.kg.dump(),
            extracted_facts=agent_result.extracted_facts,
        )
        save_screening(result)
        update_job_status(job.job_id, JobStatus.DONE, result_id=result.id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Level 1 job %s failed: %s", job.job_id, exc)
        traceback.print_exc()
        update_job_status(job.job_id, JobStatus.FAILED, error=str(exc))


async def _worker(worker_id: int) -> None:
    logger.info("Level 1 worker %d started.", worker_id)
    while True:
        job, form, deck_text = await _queue.get()
        try:
            await _process(job, form, deck_text)
        finally:
            _queue.task_done()


def start_workers(app=None) -> None:
    """Spawn worker coroutines. Idempotent. Call once at app startup."""
    global _workers_started
    if _workers_started:
        return
    _workers_started = True
    for i in range(config.MAX_CONCURRENT_EVALUATIONS):
        asyncio.ensure_future(_worker(i))
    logger.info(
        "%d Level 1 workers started.", config.MAX_CONCURRENT_EVALUATIONS
    )


# ─────────────────────────────────────────────
# Public submission API
# ─────────────────────────────────────────────

async def submit_screening(
    form: Level1Form,
    deck_text: str | None = None,
) -> ScreeningJob:
    """Queue a screening; returns immediately with a ScreeningJob."""
    job = ScreeningJob(
        job_id=str(uuid.uuid4()),
        submitted_at=datetime.utcnow(),
        status=JobStatus.QUEUED,
        deck_attached=deck_text is not None,
    )
    save_job(job, form_json=form.model_dump_json(), deck_text=deck_text)
    try:
        _queue.put_nowait((job, form, deck_text))
    except asyncio.QueueFull:
        update_job_status(
            job.job_id, JobStatus.FAILED,
            error="Queue full — try again shortly",
        )
        raise QueueFullError("Screening queue is at capacity. Retry in a moment.")
    return job


async def screen_sync(
    form: Level1Form,
    deck_text: str | None = None,
) -> ScreeningResult:
    """Inline runner — for tests, scripts, or one-shot synchronous use."""
    job = ScreeningJob(
        job_id=str(uuid.uuid4()),
        submitted_at=datetime.utcnow(),
        status=JobStatus.QUEUED,
        deck_attached=deck_text is not None,
    )
    save_job(job, form_json=form.model_dump_json(), deck_text=deck_text)
    await _process(job, form, deck_text)
    saved = get_job(job.job_id)
    if saved and saved.get("result_id"):
        result = get_screening(saved["result_id"])
        if result:
            return result
    raise RuntimeError(
        f"Screening failed: {(saved or {}).get('error') or 'unknown'}"
    )
