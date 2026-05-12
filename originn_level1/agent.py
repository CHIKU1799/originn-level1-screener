"""
Originn Level 1 agent.

Two LLM calls, both cheap:
  1. EXTRACT  — form text (+ optional deck text) → ExtractedFacts JSON
  2. NARRATE  — KG signals + computed scores → founder-facing narrative

Scoring itself is pure graph math inside SubmissionKG (no LLM). Splitting it
this way means the score is reproducible — the same submission produces the
same score across runs, regardless of model temperature drift in the
narration pass.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ._openai_client import rate_limited_create
from .kg import SubmissionKG
from .models import (
    ExtractedFacts,
    Level1Form,
    Narrative,
    triage_from_score,
)
from .prompts import render as render_prompt
from . import config

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Internal pieces
# ─────────────────────────────────────────────

async def _extract(form: Level1Form, deck_text: str | None) -> ExtractedFacts:
    user_payload = "=== STEP 1 FORM ===\n" + form.to_text()
    if deck_text:
        # Keep deck context bounded for predictable Level 1 cost
        user_payload += f"\n\n=== ATTACHED DECK ===\n{deck_text[:9000]}"
    else:
        user_payload += "\n\n=== ATTACHED DECK ===\n(none — form only)"

    response = await rate_limited_create(
        model=config.EXTRACTION_MODEL,
        messages=[
            {"role": "system", "content": render_prompt("level1_extract")},
            {"role": "user",   "content": user_payload},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=2000,
    )
    raw = json.loads(response.choices[0].message.content)
    return ExtractedFacts.model_validate(raw)


def _build_narrate_payload(
    *,
    startup_name: str,
    composite_score: float,
    triage: str,
    dimension_scores: dict,
    raw_signals,
    kg_dump,
) -> str:
    payload = {
        "startup_name": startup_name,
        "composite_score": composite_score,
        "triage": triage,
        "dimension_scores": {
            k: {"score": v.score, "evidence": v.evidence}
            for k, v in dimension_scores.items()
        },
        "raw_signals": raw_signals.model_dump(),
        "kg_node_summary": [
            {
                "id": n.id,
                "type": n.type,
                "name": n.attrs.get("name"),
                "detail": (
                    n.attrs.get("what")
                    or n.attrs.get("claim")
                    or n.attrs.get("credential")
                    or n.attrs.get("detail")
                    or n.attrs.get("trigger")
                    or n.attrs.get("what_hurts")
                ),
                "source": n.attrs.get("source"),
            }
            for n in kg_dump.nodes
        ],
    }
    return json.dumps(payload, indent=2)


async def _narrate(payload_json: str) -> Narrative:
    response = await rate_limited_create(
        model=config.EXTRACTION_MODEL,   # mini is plenty for narration
        messages=[
            {"role": "system", "content": render_prompt("level1_narrate")},
            {"role": "user",   "content": payload_json},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=800,
    )
    raw = json.loads(response.choices[0].message.content)
    return Narrative.model_validate(raw)


# ─────────────────────────────────────────────
# Public agent
# ─────────────────────────────────────────────

@dataclass
class Level1AgentResult:
    extracted_facts: ExtractedFacts
    kg: SubmissionKG
    composite_score: float
    triage: str
    dimension_scores: dict
    raw_signals: object   # RawSignals — kept as object to avoid circular type import
    narrative: Narrative


async def run_level1(form: Level1Form, deck_text: str | None) -> Level1AgentResult:
    facts = await _extract(form, deck_text)

    kg = SubmissionKG(form.startup_name)
    kg.ingest(facts)
    scoring = kg.score()

    composite = scoring["composite_score"]
    triage = triage_from_score(composite).value
    dim_scores = scoring["dimension_scores"]
    raw_signals = scoring["raw_signals"]
    kg_dump = kg.dump()

    payload = _build_narrate_payload(
        startup_name=form.startup_name,
        composite_score=composite,
        triage=triage,
        dimension_scores=dim_scores,
        raw_signals=raw_signals,
        kg_dump=kg_dump,
    )
    narrative = await _narrate(payload)

    return Level1AgentResult(
        extracted_facts=facts,
        kg=kg,
        composite_score=composite,
        triage=triage,
        dimension_scores=dim_scores,
        raw_signals=raw_signals,
        narrative=narrative,
    )
