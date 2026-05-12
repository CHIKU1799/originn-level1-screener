"""
SubmissionKG — knowledge graph for Originn Level 1 submissions.

Why a KG instead of a flat dict:
  • Ideation submissions are mostly *claims*, not numbers. Scoring needs to
    reason about coverage (did the founder talk about a problem at all?),
    grounding (is the claim backed by evidence?), and consistency
    (does the form contradict the deck?). A typed graph models that
    cleanly; a flat dict does not.
  • Every score component traces to a graph fact — frontends can render
    provenance for every point gained or lost.
  • Form + (optional) deck are two sources for the same graph. A claim
    that appears in both gets `source = "both"` provenance and counts
    toward a cross-source bonus.

Scoring is pure graph math (no LLM). The same submission produces the same
score every run, regardless of model temperature.

Public surface:
  SubmissionKG(startup_name)
  .ingest(facts: ExtractedFacts | dict)   — bulk add from extractor
  .score() -> dict                        — deterministic scoring
  .dump() -> KGDump                       — JSON-serialisable graph
"""
from __future__ import annotations

from typing import Any, Optional

import networkx as nx

from .models import (
    CrossSourceConflict,
    DimensionScore,
    DIMENSION_MAX,
    ExtractedFacts,
    KGDump,
    KGEdge,
    KGNode,
    RawSignals,
)


# ─────────────────────────────────────────────
# Node / edge type constants
# Stable strings — referenced from frontend KG viz, do not rename
# ─────────────────────────────────────────────

N_VENTURE     = "venture"
N_PROBLEM     = "problem"
N_CUSTOMER    = "customer"
N_SOLUTION    = "solution"
N_WHY_NOW     = "why_now"
N_COMPETITOR  = "competitor"
N_FOUNDER     = "founder"
N_CREDENTIAL  = "credential"
N_TRACTION    = "traction_signal"
N_EVIDENCE    = "evidence"
N_DIFF        = "differentiator"

E_ADDRESSES         = "addresses"
E_AFFECTS           = "affects"
E_GROUNDED_BY       = "grounded_by"
E_QUALIFIED_BY      = "qualified_by"
E_DIFFERENTIATES    = "differentiates_from"
E_TRIGGERED_BY      = "triggered_by"
E_HAS_TRACTION      = "has_traction"
E_HAS_FOUNDER       = "has_founder"
E_CONFLICTS_WITH    = "conflicts_with"

SRC_FORM = "form"
SRC_DECK = "deck"
SRC_BOTH = "both"

EXPECTED_NODE_TYPES: set[str] = {
    N_VENTURE, N_PROBLEM, N_CUSTOMER, N_SOLUTION,
    N_WHY_NOW, N_FOUNDER, N_CREDENTIAL,
}

# Verticals where a clear technical mechanism + at least one technical
# founder is a hard expectation. Idea-stage submissions in these verticals
# without tech signal get a lower technical_feasibility score.
_TECH_HEAVY_VERTICALS: tuple[str, ...] = (
    "deeptech", "ai", "hardware", "iot", "fintech", "healthtech",
    "biotech", "robotics",
)

_TECHNICAL_CREDENTIAL_KINDS: tuple[str, ...] = (
    "institute", "prior_role", "domain_experience", "research",
)


class SubmissionKG:
    def __init__(self, startup_name: str) -> None:
        self.g: nx.MultiDiGraph = nx.MultiDiGraph()
        self.startup_name = startup_name
        self.venture_id = f"venture:{startup_name}"
        self.inconsistencies: list[CrossSourceConflict] = []

    # ─────────────────────────────────────────
    # Ingestion
    # ─────────────────────────────────────────
    def _add(self, node_id: str, ntype: str, **attrs: Any) -> None:
        self.g.add_node(node_id, type=ntype, **attrs)

    def _edge(self, u: str, v: str, etype: str, **attrs: Any) -> None:
        self.g.add_edge(u, v, key=etype, type=etype, **attrs)

    def ingest(self, facts: ExtractedFacts | dict) -> None:
        if isinstance(facts, ExtractedFacts):
            f = facts.model_dump()
        else:
            f = facts

        v = f.get("venture") or {}
        self._add(
            self.venture_id, N_VENTURE,
            name=v.get("name"), stage=v.get("stage"),
            vertical=v.get("vertical"), institute=v.get("institute"),
            source=v.get("source", SRC_FORM),
        )

        if p := f.get("problem"):
            pid = "problem:main"
            self._add(pid, N_PROBLEM,
                      who_hurts=p.get("who_hurts"),
                      what_hurts=p.get("what_hurts"),
                      source=p.get("source", SRC_FORM))
            if c := f.get("customer"):
                cid = "customer:main"
                self._add(cid, N_CUSTOMER,
                          segment=c.get("segment"),
                          identifiable=c.get("identifiable", False),
                          source=c.get("source", SRC_FORM))
                self._edge(pid, cid, E_AFFECTS)

        if s := f.get("solution"):
            sid = "solution:main"
            self._add(sid, N_SOLUTION,
                      what=s.get("what"), mechanism=s.get("mechanism"),
                      source=s.get("source", SRC_FORM))
            if "problem:main" in self.g:
                self._edge(sid, "problem:main", E_ADDRESSES)

        if w := f.get("why_now"):
            wid = "why_now:main"
            self._add(wid, N_WHY_NOW,
                      trigger=w.get("trigger"),
                      source=w.get("source", SRC_FORM))
            self._edge(self.venture_id, wid, E_TRIGGERED_BY)

        for i, comp in enumerate(f.get("competitors") or []):
            cid = f"competitor:{i}"
            self._add(cid, N_COMPETITOR,
                      name=comp.get("name"),
                      source=comp.get("source", SRC_FORM))
            if "solution:main" in self.g:
                self._edge("solution:main", cid, E_DIFFERENTIATES)

        for i, d in enumerate(f.get("differentiators") or []):
            did = f"differentiator:{i}"
            self._add(did, N_DIFF,
                      claim=d.get("claim"),
                      source=d.get("source", SRC_FORM))

        for i, fo in enumerate(f.get("founders") or []):
            fid = f"founder:{i}:{fo.get('name','unknown')}"
            self._add(fid, N_FOUNDER,
                      name=fo.get("name"), role=fo.get("role"),
                      source=fo.get("source", SRC_FORM))
            self._edge(self.venture_id, fid, E_HAS_FOUNDER)

        for i, cr in enumerate(f.get("credentials") or []):
            cid = f"credential:{i}"
            self._add(cid, N_CREDENTIAL,
                      credential=cr.get("credential"),
                      kind=cr.get("kind", "other"),
                      source=cr.get("source", SRC_FORM))
            target_name = (cr.get("founder_name") or "").lower().strip()
            for nid, data in list(self.g.nodes(data=True)):
                if data.get("type") == N_FOUNDER and \
                   (data.get("name") or "").lower().strip() == target_name:
                    self._edge(nid, cid, E_QUALIFIED_BY)
                    break

        for i, t in enumerate(f.get("traction_signals") or []):
            tid = f"traction:{i}"
            self._add(tid, N_TRACTION,
                      kind=t.get("kind"), detail=t.get("detail"),
                      source=t.get("source", SRC_FORM))
            self._edge(self.venture_id, tid, E_HAS_TRACTION)

        for i, e in enumerate(f.get("evidence_items") or []):
            eid = f"evidence:{i}"
            self._add(eid, N_EVIDENCE,
                      claim_topic=e.get("claim_topic"),
                      detail=e.get("detail"),
                      source=e.get("source", SRC_FORM))
            target_map = {
                "problem":  "problem:main",
                "market":   "customer:main",
                "solution": "solution:main",
                "traction": self.venture_id,
            }
            target = target_map.get(e.get("claim_topic"))
            if target and target in self.g:
                self._edge(target, eid, E_GROUNDED_BY)

        for cf in f.get("cross_source_conflicts") or []:
            if isinstance(cf, CrossSourceConflict):
                self.inconsistencies.append(cf)
            else:
                self.inconsistencies.append(CrossSourceConflict(**cf))

    # ─────────────────────────────────────────
    # Structural signals (pure graph math, no LLM)
    # ─────────────────────────────────────────
    def _nodes_by_type(self, ntype: str) -> list[str]:
        return [n for n, d in self.g.nodes(data=True) if d.get("type") == ntype]

    def coverage_ratio(self) -> tuple[float, set[str]]:
        present = {d["type"] for _, d in self.g.nodes(data=True)
                   if d.get("type") in EXPECTED_NODE_TYPES}
        return len(present) / len(EXPECTED_NODE_TYPES), present

    def grounding_ratio(self) -> float:
        targets = ["problem:main", "solution:main", "customer:main", self.venture_id]
        targets = [t for t in targets if t in self.g]
        if not targets:
            return 0.0
        grounded = 0
        for t in targets:
            for _, v, k in self.g.out_edges(t, keys=True):
                if k == E_GROUNDED_BY:
                    grounded += 1
                    break
        return grounded / len(targets)

    def founder_qualification_score(self) -> float:
        founders = self._nodes_by_type(N_FOUNDER)
        if not founders:
            return 0.0
        qualified = 0
        for fid in founders:
            for _, _, k in self.g.out_edges(fid, keys=True):
                if k == E_QUALIFIED_BY:
                    qualified += 1
                    break
        return qualified / len(founders)

    def differentiation_score(self) -> float:
        has_competitor      = bool(self._nodes_by_type(N_COMPETITOR))
        has_why_now         = bool(self._nodes_by_type(N_WHY_NOW))
        has_differentiator  = bool(self._nodes_by_type(N_DIFF))
        return (
            (0.45 if has_competitor else 0.0)
            + (0.35 if has_why_now else 0.0)
            + (0.20 if has_differentiator else 0.0)
        )

    def consistency_ratio(self) -> float:
        total_claims = sum(
            1 for _, d in self.g.nodes(data=True)
            if d.get("source") in (SRC_FORM, SRC_DECK, SRC_BOTH)
        )
        if total_claims == 0:
            return 1.0
        return max(0.0, 1.0 - (len(self.inconsistencies) / total_claims))

    def cross_source_bonus(self) -> float:
        both = sum(1 for _, d in self.g.nodes(data=True)
                   if d.get("source") == SRC_BOTH)
        total = sum(1 for _, d in self.g.nodes(data=True)
                    if d.get("source") in (SRC_FORM, SRC_DECK, SRC_BOTH))
        return both / total if total else 0.0

    # ── Technical feasibility signals ──────────────────────────────────────
    def technical_mechanism_present(self) -> bool:
        """A Solution node with a non-empty `mechanism` field counts."""
        for _, d in self.g.nodes(data=True):
            if d.get("type") == N_SOLUTION and (d.get("mechanism") or "").strip():
                return True
        return False

    def technical_founder_present(self) -> bool:
        """Any credential node whose kind is in TECHNICAL_CREDENTIAL_KINDS."""
        for _, d in self.g.nodes(data=True):
            if d.get("type") == N_CREDENTIAL and \
               d.get("kind") in _TECHNICAL_CREDENTIAL_KINDS:
                return True
        return False

    def vertical_fit(self) -> float:
        """
        Returns 1.0 when the (vertical, tech-signal) pair is internally
        consistent: tech-heavy vertical → expects tech founder + mechanism;
        non-tech vertical → no penalty for lighter tech signal.
        """
        venture = self.g.nodes.get(self.venture_id, {})
        vertical = (venture.get("vertical") or "").lower()
        is_tech_heavy = any(t in vertical for t in _TECH_HEAVY_VERTICALS)
        if not is_tech_heavy:
            return 1.0
        return (
            (0.5 if self.technical_mechanism_present() else 0.0)
            + (0.5 if self.technical_founder_present() else 0.0)
        )

    def technical_feasibility_score(self) -> tuple[float, list[str]]:
        """
        Returns (raw 0..1, evidence list).
        Mechanism described (0.4) + technical founder (0.4) + vertical fit (0.2).
        """
        ev: list[str] = []
        score = 0.0
        if self.technical_mechanism_present():
            score += 0.4
            ev.append("solution:main:mechanism")
        else:
            ev.append("missing:solution_mechanism")
        if self.technical_founder_present():
            score += 0.4
            ev.append("credential:technical_kind")
        else:
            ev.append("missing:technical_credential")
        vf = self.vertical_fit()
        score += 0.2 * vf
        ev.append(f"vertical_fit={vf:.2f}")
        return score, ev

    # ─────────────────────────────────────────
    # Dimension scoring (composite 0..100)
    # ─────────────────────────────────────────
    def score(self) -> dict:
        cov, present_types = self.coverage_ratio()
        grd  = self.grounding_ratio()
        ffit = self.founder_qualification_score()
        diff = self.differentiation_score()
        cons = self.consistency_ratio()
        xsrc = self.cross_source_bonus()
        tech_raw, tech_ev = self.technical_feasibility_score()

        # ── Map to 5 dimensions, 0..DIMENSION_MAX each ────────────────────
        # Clarity & Coherence — coverage (70%) + consistency (30%)
        clarity_raw = 0.7 * cov + 0.3 * cons
        clarity_ev = [f"coverage={cov:.2f}", f"consistency={cons:.2f}"]
        for nt in (EXPECTED_NODE_TYPES - present_types):
            clarity_ev.append(f"missing:{nt}")

        # Problem & Market Plausibility
        pm_present = (
            int(N_PROBLEM in present_types) + int(N_CUSTOMER in present_types)
        ) / 2
        plausibility_raw = 0.5 * pm_present + 0.5 * grd
        plausibility_ev = [
            f"problem_present={N_PROBLEM in present_types}",
            f"customer_present={N_CUSTOMER in present_types}",
            f"grounding={grd:.2f}",
        ]

        # Founder–Problem Fit
        founder_raw = ffit
        founder_ev = [f"qualified_founders={ffit:.2f}"]
        if ffit < 1.0:
            founder_ev.append("missing:qualified_by_edge_for_some_founder")

        # Differentiation & Why-Now
        differentiation_raw = diff
        differentiation_ev = [
            f"competitor_present={bool(self._nodes_by_type(N_COMPETITOR))}",
            f"why_now_present={bool(self._nodes_by_type(N_WHY_NOW))}",
            f"differentiator_present={bool(self._nodes_by_type(N_DIFF))}",
        ]

        # Technical Feasibility (already computed above)
        # tech_raw, tech_ev

        # Cross-source bonus — small bump to each dim, capped at the max
        bonus = xsrc * (DIMENSION_MAX * 0.04)   # up to ~+0.8 per dim

        def _to_dim(raw: float, evidence: list[str]) -> DimensionScore:
            return DimensionScore(
                score=round(min(DIMENSION_MAX, DIMENSION_MAX * raw + bonus), 1),
                evidence=evidence,
            )

        dims = {
            "clarity_coherence":           _to_dim(clarity_raw,         clarity_ev),
            "problem_market_plausibility": _to_dim(plausibility_raw,    plausibility_ev),
            "founder_problem_fit":         _to_dim(founder_raw,         founder_ev),
            "differentiation_why_now":     _to_dim(differentiation_raw, differentiation_ev),
            "technical_feasibility":       _to_dim(tech_raw,            tech_ev),
        }
        composite = round(sum(d.score for d in dims.values()), 1)

        signals = RawSignals(
            coverage=round(cov, 2),
            grounding=round(grd, 2),
            founder_qualification=round(ffit, 2),
            differentiation=round(diff, 2),
            consistency=round(cons, 2),
            cross_source=round(xsrc, 2),
            technical_mechanism_present=self.technical_mechanism_present(),
            technical_founder_present=self.technical_founder_present(),
            vertical_fit=round(self.vertical_fit(), 2),
            present_node_types=sorted(present_types),
            missing_node_types=sorted(EXPECTED_NODE_TYPES - present_types),
            inconsistencies=self.inconsistencies,
        )

        return {
            "dimension_scores": dims,
            "composite_score": composite,
            "raw_signals": signals,
        }

    # ─────────────────────────────────────────
    # Serialisation
    # ─────────────────────────────────────────
    def dump(self) -> KGDump:
        nodes: list[KGNode] = []
        for n, d in self.g.nodes(data=True):
            attrs = {k: v for k, v in d.items() if k != "type"}
            nodes.append(KGNode(id=n, type=d.get("type", "unknown"), attrs=attrs))
        edges: list[KGEdge] = [
            KGEdge(source=u, target=v, type=d.get("type", "unknown"))
            for u, v, d in self.g.edges(data=True)
        ]
        return KGDump(nodes=nodes, edges=edges)
