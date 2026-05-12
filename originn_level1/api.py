"""
FastAPI server for the Originn Level 1 screener.

  POST /screen                       — multipart submit, returns job_id (202)
  GET  /jobs/{job_id}                — poll status; includes full result when done
  GET  /jobs                         — list recent jobs
  GET  /screenings                   — list screening results (filterable)
  GET  /screenings/{screening_id}    — full result by ID
  GET  /health                       — liveness probe
  GET  /docs                         — Swagger UI (FastAPI default)

Run:
  uvicorn originn_level1.api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .deck_loader import (
    EmptyDeckError, UnsupportedDeckFormat, SUPPORTED_EXTENSIONS, load_deck,
)
from .evaluator import (
    QueueFullError, start_workers, submit_screening,
)
from .models import FormStage, FounderInput, Level1Form, TriageOutcome
from .storage import (
    get_job, get_screening, list_jobs, list_screenings,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_workers()
    yield


app = FastAPI(
    title="Originn Level 1 Screener",
    description=(
        "First-screen filter for the Originn startup portal. Takes a 7-field "
        "Step 1 form (and an optional deck), builds a knowledge graph of the "
        "submission, and returns a triage label (pass / review / reject) "
        "plus a 0–100 composite score across five dimensions."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# Submission endpoint
# ─────────────────────────────────────────────

@app.post("/screen", status_code=202,
          summary="Submit Step 1 form (+ optional deck) for Level 1 screening")
async def screen(
    startup_name:           str = Form(...),
    industry_vertical:      str = Form(...),
    institute_or_incubator: str = Form(""),
    what_are_you_building:  str = Form(...),
    stage:                  str = Form(...),
    why_solve_this:         str = Form(...),
    founders_json:          str = Form(
        ...,
        description=(
            'JSON array of founders, e.g. '
            '[{"name": "Ada Lovelace", "credential_one_line": "...", "role": "..."}]'
        ),
    ),
    file:                   Optional[UploadFile] = File(None),
):
    """
    Multipart submission. `founders_json` is a JSON string because HTML
    forms don't natively encode arrays of objects. Optional file upload
    (.pptx / .pdf / .md / .txt) attaches the deck.
    """
    # Parse founders
    try:
        founders_raw = json.loads(founders_json)
        if not isinstance(founders_raw, list) or not founders_raw:
            raise ValueError("founders_json must be a non-empty JSON array")
        founders = [FounderInput.model_validate(f) for f in founders_raw]
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(400, f"Invalid founders_json: {exc}")

    try:
        stage_enum = FormStage(stage)
    except ValueError:
        raise HTTPException(
            400, f"Invalid stage {stage!r}. Allowed: {[s.value for s in FormStage]}"
        )

    form = Level1Form(
        startup_name=startup_name,
        industry_vertical=industry_vertical,
        institute_or_incubator=institute_or_incubator,
        what_are_you_building=what_are_you_building,
        stage=stage_enum,
        why_solve_this=why_solve_this,
        founders=founders,
    )

    deck_text: Optional[str] = None
    if file is not None:
        content = await file.read()
        try:
            deck_text = load_deck(file.filename or "deck", content)
        except UnsupportedDeckFormat as exc:
            raise HTTPException(415, str(exc))
        except EmptyDeckError as exc:
            raise HTTPException(400, str(exc))

    try:
        job = await submit_screening(form, deck_text=deck_text)
    except QueueFullError as exc:
        raise HTTPException(503, str(exc))

    return {
        "job_id":        job.job_id,
        "status":        job.status.value,
        "deck_attached": job.deck_attached,
        "message":       "Screening queued. Poll GET /jobs/{job_id}.",
    }


# ─────────────────────────────────────────────
# Polling
# ─────────────────────────────────────────────

@app.get("/jobs/{job_id}", summary="Poll a screening job")
def job_status(job_id: str, include_result: bool = True):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id!r} not found")
    response = {
        "job_id":        job["job_id"],
        "status":        job["status"],
        "submitted_at":  job["submitted_at"],
        "deck_attached": bool(job["deck_attached"]),
        "error":         job["error"],
        "result_id":     job["result_id"],
    }
    if include_result and job["status"] == "done" and job["result_id"]:
        result = get_screening(job["result_id"])
        if result:
            response["result"] = result.model_dump(mode="json")
    return response


@app.get("/jobs", summary="List recent jobs")
def jobs_list(
    status: Optional[str] = None,
    limit: int = Query(default=50, le=200),
):
    return list_jobs(status=status, limit=limit)


# ─────────────────────────────────────────────
# Screening history
# ─────────────────────────────────────────────

@app.get("/screenings", summary="List screening results")
def screenings_list(
    startup_name: Optional[str] = None,
    triage:       Optional[str] = None,
    min_score:    Optional[float] = None,
    limit:  int = Query(default=50, le=200),
    offset: int = 0,
):
    triage_enum = None
    if triage:
        try:
            triage_enum = TriageOutcome(triage)
        except ValueError:
            raise HTTPException(
                400,
                f"Invalid triage {triage!r}. "
                f"Allowed: {[t.value for t in TriageOutcome]}",
            )
    return list_screenings(
        startup_name=startup_name, triage=triage_enum,
        min_score=min_score, limit=limit, offset=offset,
    )


@app.get("/screenings/{screening_id}", summary="Get a screening by ID")
def screening_one(screening_id: str):
    result = get_screening(screening_id)
    if not result:
        raise HTTPException(404, f"Screening {screening_id!r} not found")
    return result.model_dump(mode="json")


# ─────────────────────────────────────────────
# Meta
# ─────────────────────────────────────────────

@app.get("/health", summary="Liveness probe")
def health():
    return {
        "status": "ok",
        "model": config.LEVEL1_MODEL,
        "max_concurrent": config.MAX_CONCURRENT_EVALUATIONS,
        "queue_depth": config.MAX_QUEUE_DEPTH,
        "supported_deck_formats": list(SUPPORTED_EXTENSIONS),
        "triage_thresholds": {
            "pass":   config.TRIAGE_PASS_THRESHOLD,
            "review": config.TRIAGE_REVIEW_THRESHOLD,
        },
    }
