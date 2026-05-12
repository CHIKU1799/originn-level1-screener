"""
Central configuration for the Originn Level 1 screener.

Every setting can be overridden via an environment variable (or .env file).

Default model is gpt-4o-mini for both calls (extraction + narration) — Level 1
is a top-of-funnel filter, cheap is the right tradeoff.
"""
from __future__ import annotations

import os


# ── Model selection ──────────────────────────────────────────────────────
# Both the extraction call and the narration call default to gpt-4o-mini.
# Override LEVEL1_MODEL=gpt-4o if you want sharper extraction at higher cost.
LEVEL1_MODEL: str = os.getenv("LEVEL1_MODEL", "gpt-4o-mini")

# Back-compat alias — internal code still references this name.
EXTRACTION_MODEL: str = LEVEL1_MODEL

# ── OpenAI client settings ───────────────────────────────────────────────
OPENAI_API_KEY:  str = os.getenv("OPENAI_API_KEY", "")
OPENAI_ORG_ID:   str = os.getenv("OPENAI_ORG_ID",  "")
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "")

# ── Concurrency & queue ──────────────────────────────────────────────────
MAX_CONCURRENT_API_CALLS:    int = int(os.getenv("MAX_CONCURRENT_API_CALLS",    "8"))
MAX_CONCURRENT_EVALUATIONS:  int = int(os.getenv("MAX_CONCURRENT_EVALUATIONS",  "4"))
MAX_QUEUE_DEPTH:             int = int(os.getenv("MAX_QUEUE_DEPTH",             "100"))

# ── Retry settings ───────────────────────────────────────────────────────
MAX_RETRIES:      int   = int(os.getenv("MAX_RETRIES",      "4"))
RETRY_BASE_DELAY: float = float(os.getenv("RETRY_BASE_DELAY", "1.0"))
RETRY_MAX_DELAY:  float = float(os.getenv("RETRY_MAX_DELAY",  "60.0"))

# ── Storage ──────────────────────────────────────────────────────────────
# Empty string = put the SQLite file next to the package (screenings.db).
DB_PATH: str = os.getenv("DB_PATH", "")

# ── Triage thresholds (also exported from models.py) ─────────────────────
# Override via env if your portal uses different cutoffs.
TRIAGE_PASS_THRESHOLD:   float = float(os.getenv("TRIAGE_PASS_THRESHOLD",   "70.0"))
TRIAGE_REVIEW_THRESHOLD: float = float(os.getenv("TRIAGE_REVIEW_THRESHOLD", "40.0"))
