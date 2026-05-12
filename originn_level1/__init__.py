"""
Originn Level 1 Screener — a KG-backed first-screen filter for startup
submissions on the Originn portal.

Public re-exports for scripts and notebooks:
  from originn_level1 import screen_sync, Level1Form, FounderInput, FormStage
"""
from .evaluator import screen_sync, submit_screening, start_workers, QueueFullError
from .models import (
    FormStage, FounderInput, Level1Form,
    JobStatus, ScreeningJob, ScreeningResult,
    TriageOutcome, triage_from_score,
    DIMENSION_KEYS, DIMENSION_MAX,
)
from .kg import SubmissionKG
from .deck_loader import load_deck, SUPPORTED_EXTENSIONS

__all__ = [
    "screen_sync", "submit_screening", "start_workers", "QueueFullError",
    "FormStage", "FounderInput", "Level1Form",
    "JobStatus", "ScreeningJob", "ScreeningResult",
    "TriageOutcome", "triage_from_score",
    "DIMENSION_KEYS", "DIMENSION_MAX",
    "SubmissionKG",
    "load_deck", "SUPPORTED_EXTENSIONS",
]

__version__ = "0.1.0"
