"""
Originn Level 1 screener tests.

The KG + scoring tests are pure Python (no LLM, fast). The end-to-end
smoke test that exercises the real OpenAI API is opt-in via the
LEVEL1_RUN_E2E environment variable so default `pytest` stays cheap.
"""
from __future__ import annotations

import os

import pytest

from originn_level1 import (
    DIMENSION_MAX, FormStage, FounderInput, Level1Form, SubmissionKG,
    TriageOutcome, triage_from_score,
)
from originn_level1.models import ExtractedFacts


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _strong_facts(startup_name: str = "VoxArena") -> dict:
    """Fully populated extraction — should score near-max."""
    return {
        "venture": {
            "name": startup_name, "stage": "Idea",
            "vertical": "DeepTech & AI",
            "institute": "IIT Bombay",
            "source": "both",
        },
        "problem": {
            "who_hurts": "Enterprise sales reps and procurement teams",
            "what_hurts": "Negotiation training is one-shot and doesn't adapt",
            "source": "both",
        },
        "customer": {
            "segment": "Enterprise sales orgs in India + SEA",
            "identifiable": True, "source": "both",
        },
        "solution": {
            "what": "Voice-AI adaptive negotiation roleplay",
            "mechanism": "Real-time speech LLM with per-rep policy adaptation",
            "source": "both",
        },
        "why_now": {
            "trigger": "Voice LLMs crossed real-time conversational quality",
            "source": "deck",
        },
        "competitors": [
            {"name": "Gong",       "source": "deck"},
            {"name": "Chorus.ai",  "source": "deck"},
        ],
        "differentiators": [
            {"claim": "Adaptive counterparty, not scripted", "source": "form"},
        ],
        "founders": [
            {"name": "Aarav Mehta",  "role": "CEO",           "source": "both"},
            {"name": "Priya Iyer",   "role": "Head of Speech","source": "both"},
        ],
        "credentials": [
            {
                "founder_name": "Aarav Mehta",
                "credential": "IIT Bombay Speech Lab alum, 6yr at NLP startup",
                "kind": "prior_role", "source": "both",
            },
            {
                "founder_name": "Priya Iyer",
                "credential": "IISc CSA PhD, conversational speech research",
                "kind": "research", "source": "both",
            },
        ],
        "traction_signals": [
            {"kind": "pilot", "detail": "3 paid pilots with mid-market firms",
             "source": "deck"},
        ],
        "evidence_items": [
            {"claim_topic": "problem",
             "detail": "Surveys of 40 sales reps cite scripted roleplay fatigue",
             "source": "deck"},
            {"claim_topic": "solution",
             "detail": "Benchmark on negotiation roleplay realism", "source": "deck"},
        ],
        "cross_source_conflicts": [],
    }


def _weak_facts() -> dict:
    """Buzzword salad — should REJECT."""
    return {
        "venture": {
            "name": "SynthGenix", "stage": "Idea",
            "vertical": "DeepTech & AI", "institute": None, "source": "form",
        },
        "problem": None,
        "customer": None,
        "solution": {
            "what": "World's first AI blockchain Web3 productivity platform",
            "mechanism": None,
            "source": "form",
        },
        "why_now": None,
        "competitors": [],
        "differentiators": [{"claim": "10x better than anything", "source": "form"}],
        "founders": [],
        "credentials": [],
        "traction_signals": [],
        "evidence_items": [],
        "cross_source_conflicts": [],
    }


# ─────────────────────────────────────────────
# Form model
# ─────────────────────────────────────────────

def test_form_to_text_includes_all_fields():
    form = Level1Form(
        startup_name="VoxArena",
        industry_vertical="DeepTech & AI",
        institute_or_incubator="IIT Bombay",
        what_are_you_building="Voice AI for negotiation training.",
        stage=FormStage.IDEA,
        why_solve_this="Years of watching bad habits get rehearsed.",
        founders=[
            FounderInput(name="Aarav Mehta", role="CEO",
                         credential_one_line="IIT Bombay Speech Lab alum"),
        ],
    )
    text = form.to_text()
    assert "VoxArena" in text
    assert "DeepTech" in text
    assert "Aarav Mehta" in text
    assert "Speech Lab" in text


# ─────────────────────────────────────────────
# KG scoring — deterministic math, no LLM
# ─────────────────────────────────────────────

def test_strong_submission_passes():
    facts = ExtractedFacts.model_validate(_strong_facts())
    kg = SubmissionKG("VoxArena")
    kg.ingest(facts)
    out = kg.score()
    assert out["composite_score"] >= 70, (
        f"strong submission should PASS, got {out['composite_score']}"
    )
    assert triage_from_score(out["composite_score"]) == TriageOutcome.PASS
    assert set(out["dimension_scores"]) == {
        "clarity_coherence", "problem_market_plausibility",
        "founder_problem_fit", "differentiation_why_now",
        "technical_feasibility",
    }


def test_weak_submission_rejects():
    facts = ExtractedFacts.model_validate(_weak_facts())
    kg = SubmissionKG("SynthGenix")
    kg.ingest(facts)
    out = kg.score()
    assert out["composite_score"] < 40, (
        f"weak submission should REJECT, got {out['composite_score']}"
    )
    assert triage_from_score(out["composite_score"]) == TriageOutcome.REJECT
    assert out["dimension_scores"]["founder_problem_fit"].score == 0
    assert out["dimension_scores"]["problem_market_plausibility"].score < 5


def test_dimension_max_is_consistent():
    facts = ExtractedFacts.model_validate(_strong_facts())
    kg = SubmissionKG("VoxArena")
    kg.ingest(facts)
    out = kg.score()
    for k, dim in out["dimension_scores"].items():
        assert 0 <= dim.score <= DIMENSION_MAX, (
            f"{k} score {dim.score} out of [0, {DIMENSION_MAX}]"
        )


def test_technical_feasibility_flags_missing_mechanism():
    facts = ExtractedFacts.model_validate(_weak_facts())
    kg = SubmissionKG("SynthGenix")
    kg.ingest(facts)
    out = kg.score()
    tech = out["dimension_scores"]["technical_feasibility"]
    assert tech.score < DIMENSION_MAX * 0.5, (
        f"tech-heavy vertical without mechanism / technical founder should "
        f"score low, got {tech.score}"
    )
    assert any("missing:solution_mechanism" in e for e in tech.evidence)
    assert any("missing:technical_credential" in e for e in tech.evidence)


def test_technical_feasibility_rewards_mechanism_plus_credential():
    facts = ExtractedFacts.model_validate(_strong_facts())
    kg = SubmissionKG("VoxArena")
    kg.ingest(facts)
    out = kg.score()
    tech = out["dimension_scores"]["technical_feasibility"]
    assert tech.score >= DIMENSION_MAX * 0.8


def test_kg_dump_is_serialisable():
    facts = ExtractedFacts.model_validate(_strong_facts())
    kg = SubmissionKG("VoxArena")
    kg.ingest(facts)
    dump = kg.dump()
    assert len(dump.nodes) > 5
    assert any(n.type == "founder" for n in dump.nodes)
    assert any(e.type == "qualified_by" for e in dump.edges)


def test_triage_thresholds_defaults():
    assert triage_from_score(80.0) == TriageOutcome.PASS
    assert triage_from_score(70.0) == TriageOutcome.PASS
    assert triage_from_score(50.0) == TriageOutcome.REVIEW
    assert triage_from_score(40.0) == TriageOutcome.REVIEW
    assert triage_from_score(20.0) == TriageOutcome.REJECT


# ─────────────────────────────────────────────
# End-to-end smoke (opt-in — needs OPENAI_API_KEY)
# ─────────────────────────────────────────────

@pytest.mark.skipif(
    not os.getenv("LEVEL1_RUN_E2E"),
    reason="Set LEVEL1_RUN_E2E=1 to run the live-LLM end-to-end test.",
)
def test_screen_sync_e2e():
    """Hits the real OpenAI API. Confirms the full pipeline wires up."""
    import asyncio
    from originn_level1 import screen_sync

    form = Level1Form(
        startup_name="VoxArena",
        industry_vertical="DeepTech & AI",
        institute_or_incubator="IIT Bombay",
        what_are_you_building=(
            "Voice-AI negotiation training that adapts to each rep's style. "
            "Live AI counterparty + per-call coaching."
        ),
        stage=FormStage.IDEA,
        why_solve_this="Voice LLMs are finally good enough to argue back.",
        founders=[
            FounderInput(name="Aarav Mehta", role="CEO",
                         credential_one_line="IIT Bombay Speech Lab alum"),
            FounderInput(name="Priya Iyer", role="Head of Speech",
                         credential_one_line="IISc CSA PhD"),
        ],
    )
    # Deck is required — inline snippet stands in for an uploaded file.
    deck_text = """
    === Slide 1 — Problem ===
    Enterprise sales reps practise negotiation via static PDFs and one-shot
    workshops. None of it adapts to how a specific rep actually argues.

    === Slide 2 — Solution ===
    A voice-AI counterparty that runs live roleplays and gives per-call
    coaching. Mechanism: real-time speech LLM with per-rep policy adaptation.

    === Slide 3 — Why Now ===
    Voice LLMs crossed real-time conversational quality in 2024.

    === Slide 4 — Team ===
    Aarav Mehta (CEO, IIT Bombay Speech Lab alum).
    Priya Iyer (Head of Speech, IISc CSA PhD in conversational speech).

    === Slide 5 — Traction ===
    3 paid pilots with mid-market enterprise sales orgs.
    """.strip()
    result = asyncio.run(screen_sync(form, deck_text=deck_text))
    assert result.composite_score > 0
    assert result.triage in TriageOutcome
    assert len(result.dimension_scores) == 5
    assert result.narrative.technical_remark
    assert result.deck_attached is True


def test_screen_sync_rejects_missing_deck():
    """Calling screen_sync without a deck must raise ValueError."""
    import asyncio
    from originn_level1 import screen_sync

    form = Level1Form(
        startup_name="VoxArena",
        industry_vertical="DeepTech & AI",
        institute_or_incubator="IIT Bombay",
        what_are_you_building="Voice-AI negotiation training.",
        stage=FormStage.IDEA,
        why_solve_this="Voice LLMs are finally good enough.",
        founders=[
            FounderInput(name="Aarav Mehta", role="CEO",
                         credential_one_line="IIT Bombay Speech Lab alum"),
        ],
    )
    with pytest.raises(ValueError, match="deck_text is required"):
        asyncio.run(screen_sync(form, deck_text=""))
