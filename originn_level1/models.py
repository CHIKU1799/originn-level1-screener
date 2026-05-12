"""
Data models for the Originn Level 1 screener.

Level 1 is the *first* filter on the Originn portal. Even ideation-stage
ventures with no product, no revenue, and no traction submit here. Inputs
are deliberately light:

  • A 7-field Step 1 form (always present)
  • An optional uploaded deck (.pptx / .pdf / .md / .txt)

Output is a triage label (pass / review / reject), a 0–100 composite score,
five sub-scores (0–20 each), the underlying knowledge graph, and a
founder-facing narrative including a technical feasibility remark.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from . import config


# ─────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────

class TriageOutcome(str, Enum):
    PASS   = "pass"     # composite ≥ PASS threshold → advance to Step 2
    REVIEW = "review"   # REVIEW ≤ composite < PASS → human reviewer
    REJECT = "reject"   # composite < REVIEW threshold → revise & resubmit


class FormStage(str, Enum):
    IDEA    = "Idea"
    MVP     = "MVP"
    SCALING = "Scaling"


class JobStatus(str, Enum):
    QUEUED     = "queued"
    EXTRACTING = "extracting"
    SCORING    = "scoring"
    NARRATING  = "narrating"
    DONE       = "done"
    FAILED     = "failed"


# ─────────────────────────────────────────────
# Input — the Step 1 form
# ─────────────────────────────────────────────

class FounderInput(BaseModel):
    """One row of the Founders field on the Step 1 form."""
    name: str
    credential_one_line: str = Field(
        description="One-line credential — institute, prior role, or domain experience."
    )
    role: Optional[str] = None


class Level1Form(BaseModel):
    """
    Step 1 form on the Originn portal — 7 fields.

    Every field is plain text the founder types; this model just typechecks
    shape. Quality assessment (clarity, plausibility) is done downstream.
    """
    startup_name: str
    industry_vertical: str
    institute_or_incubator: str = ""
    what_are_you_building: str = Field(
        description="2–4 sentences describing the problem being solved + the approach."
    )
    stage: FormStage
    why_solve_this: str = Field(
        description="Founder motivation — why this team, why this problem."
    )
    founders: list[FounderInput] = Field(
        default_factory=list,
        description="At least one founder name + one-line credential.",
    )

    def to_text(self) -> str:
        """Flatten to plain text for the LLM extractor."""
        founders_block = "\n".join(
            f"  • {f.name}"
            + (f" — {f.role}" if f.role else "")
            + f" — {f.credential_one_line}"
            for f in self.founders
        ) or "  (no founders listed)"
        return (
            f"Startup name: {self.startup_name}\n"
            f"Industry vertical: {self.industry_vertical}\n"
            f"Institute / Incubator: {self.institute_or_incubator}\n"
            f"Stage: {self.stage.value}\n\n"
            f"What are you building?\n{self.what_are_you_building}\n\n"
            f"Why are you solving this problem?\n{self.why_solve_this}\n\n"
            f"Founders:\n{founders_block}\n"
        )


# ─────────────────────────────────────────────
# Extracted facts (LLM extractor output)
# ─────────────────────────────────────────────

class _Sourced(BaseModel):
    """Mixin — every claim carries provenance: form, deck, or both."""
    source: str = "form"  # "form" | "deck" | "both"


class VentureFacts(_Sourced):
    name: str
    stage: Optional[str] = None
    vertical: Optional[str] = None
    institute: Optional[str] = None


class ProblemFacts(_Sourced):
    who_hurts: str
    what_hurts: str


class CustomerFacts(_Sourced):
    segment: str
    identifiable: bool = False


class SolutionFacts(_Sourced):
    what: str
    mechanism: Optional[str] = None


class WhyNowFacts(_Sourced):
    trigger: str


class CompetitorFact(_Sourced):
    name: str


class DifferentiatorFact(_Sourced):
    claim: str


class FounderFact(_Sourced):
    name: str
    role: Optional[str] = None


class CredentialFact(_Sourced):
    founder_name: str
    credential: str
    kind: str = "other"  # institute | prior_role | domain_experience | research | other


class TractionFact(_Sourced):
    kind: str   # interviews | loi | waitlist | pilot | revenue | award | grant | other
    detail: str


class EvidenceFact(_Sourced):
    claim_topic: str   # problem | market | solution | traction
    detail: str


class CrossSourceConflict(BaseModel):
    topic: str
    form_says: str
    deck_says: str


class ExtractedFacts(BaseModel):
    """Structured output of the extraction LLM call."""
    venture: VentureFacts
    problem: Optional[ProblemFacts] = None
    customer: Optional[CustomerFacts] = None
    solution: Optional[SolutionFacts] = None
    why_now: Optional[WhyNowFacts] = None
    competitors: list[CompetitorFact] = []
    differentiators: list[DifferentiatorFact] = []
    founders: list[FounderFact] = []
    credentials: list[CredentialFact] = []
    traction_signals: list[TractionFact] = []
    evidence_items: list[EvidenceFact] = []
    cross_source_conflicts: list[CrossSourceConflict] = []


# ─────────────────────────────────────────────
# Result
# ─────────────────────────────────────────────

DIMENSION_MAX: float = 20.0   # 5 dimensions × 20 = 100 composite

DIMENSION_KEYS: tuple[str, ...] = (
    "clarity_coherence",
    "problem_market_plausibility",
    "founder_problem_fit",
    "differentiation_why_now",
    "technical_feasibility",
)


class DimensionScore(BaseModel):
    score: float = Field(ge=0, le=DIMENSION_MAX)
    evidence: list[str] = Field(
        default_factory=list,
        description="KG node/edge IDs or missing-signal markers behind this score.",
    )


class RawSignals(BaseModel):
    coverage: float
    grounding: float
    founder_qualification: float
    differentiation: float
    consistency: float
    cross_source: float
    technical_mechanism_present: bool
    technical_founder_present: bool
    vertical_fit: float
    present_node_types: list[str]
    missing_node_types: list[str]
    inconsistencies: list[CrossSourceConflict]


class Narrative(BaseModel):
    one_line_summary: str
    top_strengths: list[str]
    top_concerns: list[str]
    next_steps: list[str]
    technical_remark: str = Field(
        description="One-paragraph remark on technical feasibility "
                    "(mechanism plausibility, build difficulty, team's tech depth)."
    )


class KGNode(BaseModel):
    id: str
    type: str
    attrs: dict = {}


class KGEdge(BaseModel):
    source: str
    target: str
    type: str


class KGDump(BaseModel):
    nodes: list[KGNode]
    edges: list[KGEdge]


class ScreeningResult(BaseModel):
    """The full Level 1 screening result. Persisted to SQLite."""
    id: str
    created_at: datetime
    deck_attached: bool

    startup_name: str
    composite_score: float = Field(ge=0, le=100)
    triage: TriageOutcome

    dimension_scores: dict[str, DimensionScore]  # 5 entries
    raw_signals: RawSignals
    narrative: Narrative
    kg_dump: KGDump

    extracted_facts: ExtractedFacts


# ─────────────────────────────────────────────
# Job (async submission state)
# ─────────────────────────────────────────────

class ScreeningJob(BaseModel):
    job_id: str
    submitted_at: datetime
    status: JobStatus = JobStatus.QUEUED
    deck_attached: bool = False
    result_id: Optional[str] = None
    error: Optional[str] = None


# ─────────────────────────────────────────────
# Triage thresholds — sourced from config so they're env-overridable
# ─────────────────────────────────────────────

def triage_from_score(composite: float) -> TriageOutcome:
    if composite >= config.TRIAGE_PASS_THRESHOLD:
        return TriageOutcome.PASS
    if composite >= config.TRIAGE_REVIEW_THRESHOLD:
        return TriageOutcome.REVIEW
    return TriageOutcome.REJECT
