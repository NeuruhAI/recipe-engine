"""Deterministic multi-dimension scoring. Pure; no I/O."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
from typing import Mapping

from .config import DIMENSION_DIRECTION, DIMENSIONS, EngineConfig
from .models import (
    SV_DIMENSION,
    SV_SCENARIO,
    CandidateStrategy,
    ClaimClass,
    ConflictState,
    DimensionScore,
    OpportunityEnvelope,
    ScenarioScore,
    ScoreDirection,
)

SCORE_VERSION = "score.v1"


def _q(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_EVEN)


def _clamp01(value: Decimal) -> Decimal:
    if value < 0:
        return Decimal("0")
    if value > 1:
        return Decimal("1")
    return value


def _direction(dim: str) -> ScoreDirection:
    d = DIMENSION_DIRECTION[dim]
    return ScoreDirection.HIGHER_BETTER if d == "higher_better" else ScoreDirection.LOWER_BETTER


def score_strategy(
    opportunity: OpportunityEnvelope,
    strategy: CandidateStrategy,
    config: EngineConfig,
) -> ScenarioScore:
    claims = {c.claim_id: c for c in opportunity.claims}
    evidence = {e.evidence_id: e for e in opportunity.evidence}
    dims: dict[str, DimensionScore] = {}
    quantum = config.score_quantum

    # --- evidence strength ---
    strength_inputs: list[str] = []
    strength_missing: list[str] = []
    strength_vals: list[Decimal] = []
    for cid in strategy.required_claim_ids:
        c = claims.get(cid)
        if c is None:
            strength_missing.append(cid)
            continue
        strength_inputs.append(cid)
        base = c.confidence * c.source_confidence
        if c.classification == ClaimClass.OBSERVED:
            base *= Decimal("1.0")
        elif c.classification == ClaimClass.CORROBORATED:
            base *= Decimal("1.0")
        elif c.classification == ClaimClass.INFERRED:
            base *= Decimal("0.7")
        elif c.classification == ClaimClass.ASSUMPTION:
            base *= Decimal("0.4")
        elif c.classification == ClaimClass.REJECTED:
            base *= Decimal("0")
        if c.freshness == "expired":
            base *= Decimal("0.1")
        elif c.freshness == "stale":
            base *= Decimal("0.5")
        # evidence authority boost
        auth_boost = Decimal("0")
        for eid in c.evidence_refs:
            ev = evidence.get(eid)
            if ev is None:
                continue
            if ev.freshness == "expired":
                base *= Decimal("0.5")
            if ev.authority_level.value in ("domain_authority", "operator", "governance"):
                auth_boost = max(auth_boost, Decimal("0.1"))
        strength_vals.append(_clamp01(base + auth_boost))
    if strength_vals:
        strength = sum(strength_vals) / Decimal(len(strength_vals))
    else:
        strength = None
        strength_missing.append("no_required_claims_present")
    dims["evidence_strength"] = DimensionScore(
        schema_version=SV_DIMENSION,
        dimension="evidence_strength",
        value=None if strength is None else _q(strength, quantum),
        scale="0..1",
        direction=_direction("evidence_strength"),
        weight=config.weights["evidence_strength"],
        rationale="mean claim confidence×source_confidence with class/freshness factors",
        inputs=tuple(strength_inputs),
        missing_inputs=tuple(strength_missing),
        score_version=SCORE_VERSION,
    )

    # --- evidence completeness ---
    req = list(strategy.required_claim_ids)
    present = [cid for cid in req if cid in claims]
    gaps = list(strategy.evidence_gaps)
    if req:
        ratio = Decimal(len(present)) / Decimal(len(req))
    else:
        ratio = opportunity.evidence_completeness
    gap_penalty = min(Decimal("0.5"), Decimal("0.05") * Decimal(len(gaps)))
    completeness = _clamp01(ratio - gap_penalty)
    # envelope completeness floor blend
    completeness = _clamp01((completeness + opportunity.evidence_completeness) / Decimal("2"))
    # expired required claims treat as incomplete
    expired_req = [
        cid
        for cid in present
        if claims[cid].freshness == "expired"
    ]
    if expired_req:
        completeness = _clamp01(completeness * Decimal("0.4"))
    dims["evidence_completeness"] = DimensionScore(
        schema_version=SV_DIMENSION,
        dimension="evidence_completeness",
        value=_q(completeness, quantum),
        scale="0..1",
        direction=_direction("evidence_completeness"),
        weight=config.weights["evidence_completeness"],
        rationale="required claims present vs gaps and envelope completeness",
        inputs=tuple(present),
        missing_inputs=tuple([c for c in req if c not in claims] + gaps + expired_req),
        score_version=SCORE_VERSION,
    )

    # --- feasibility ---
    miss_deps = list(strategy.missing_dependencies)
    if strategy.dependencies and not strategy.available_dependencies and not miss_deps:
        # unknown availability → partial missing
        miss_deps = list(strategy.dependencies)
    if strategy.dependencies:
        avail = set(strategy.available_dependencies)
        dep_ratio = Decimal(len([d for d in strategy.dependencies if d in avail])) / Decimal(
            len(strategy.dependencies)
        )
    else:
        dep_ratio = Decimal("1")
    feasibility = dep_ratio
    if miss_deps:
        feasibility = _clamp01(feasibility * Decimal("0.5"))
    if "infeasible" in (n.lower() for n in strategy.feasibility_notes):
        feasibility = Decimal("0")
    dims["feasibility"] = DimensionScore(
        schema_version=SV_DIMENSION,
        dimension="feasibility",
        value=_q(feasibility, quantum),
        scale="0..1",
        direction=_direction("feasibility"),
        weight=config.weights["feasibility"],
        rationale="dependency availability and feasibility notes",
        inputs=tuple(strategy.available_dependencies),
        missing_inputs=tuple(miss_deps),
        score_version=SCORE_VERSION,
    )

    # --- economic attractiveness (supplied only) ---
    econ_metrics = [m for m in strategy.expected_outcomes if m.metric_id.startswith("econ.")]
    if not econ_metrics:
        econ_metrics = [
            m for m in opportunity.supplied_financial_inputs if m.metric_id.startswith("econ.")
        ]
    econ_missing: list[str] = []
    if econ_metrics:
        # normalize by mapping supplied scores if metric_id is econ.attractiveness else heuristic from value signs
        vals = []
        for m in econ_metrics:
            if m.metric_id == "econ.attractiveness":
                vals.append(_clamp01(m.value if m.scale == "0..1" else _clamp01(m.value / Decimal("100"))))
            else:
                # retain presence signal only — do not invent valuation; map non-negative to mid if unlabeled
                vals.append(Decimal("0.5") if m.value >= 0 else Decimal("0.2"))
        economic = sum(vals) / Decimal(len(vals))
        econ_inputs = tuple(m.metric_id for m in econ_metrics)
    else:
        economic = None
        econ_missing.append("econ.*")
        econ_inputs = ()
    dims["economic_attractiveness"] = DimensionScore(
        schema_version=SV_DIMENSION,
        dimension="economic_attractiveness",
        value=None if economic is None else _q(economic, quantum),
        scale="0..1",
        direction=_direction("economic_attractiveness"),
        weight=config.weights["economic_attractiveness"],
        rationale="domain/operator supplied economics only; core does not calculate ARV/MAO",
        inputs=econ_inputs,
        missing_inputs=tuple(econ_missing),
        score_version=SCORE_VERSION,
    )

    # --- time efficiency ---
    tp = strategy.time_profile
    if tp == "fast":
        time_eff = Decimal("0.9")
    elif tp == "medium":
        time_eff = Decimal("0.6")
    elif tp == "slow":
        time_eff = Decimal("0.3")
    else:
        time_eff = Decimal("0.5")
    dims["time_efficiency"] = DimensionScore(
        schema_version=SV_DIMENSION,
        dimension="time_efficiency",
        value=_q(time_eff, quantum),
        scale="0..1",
        direction=_direction("time_efficiency"),
        weight=config.weights["time_efficiency"],
        rationale=f"time_profile={tp}",
        inputs=(tp,),
        missing_inputs=() if tp != "unknown" else ("time_profile",),
        score_version=SCORE_VERSION,
    )

    # --- strategic fit ---
    fit = Decimal("0.5")
    fit_inputs: list[str] = []
    reqs = set(opportunity.operator_requirements)
    if strategy.strategy_family in reqs:
        fit = Decimal("0.9")
        fit_inputs.append(f"family:{strategy.strategy_family}")
    if opportunity.risk_tolerance == "low" and strategy.reversibility == "irreversible":
        fit = _clamp01(fit * Decimal("0.4"))
        fit_inputs.append("risk_mismatch")
    dims["strategic_fit"] = DimensionScore(
        schema_version=SV_DIMENSION,
        dimension="strategic_fit",
        value=_q(fit, quantum),
        scale="0..1",
        direction=_direction("strategic_fit"),
        weight=config.weights["strategic_fit"],
        rationale="operator requirements and risk tolerance alignment",
        inputs=tuple(fit_inputs),
        missing_inputs=(),
        score_version=SCORE_VERSION,
    )

    # --- reversibility ---
    rev_map = {
        "reversible": Decimal("1.0"),
        "partially_reversible": Decimal("0.55"),
        "irreversible": Decimal("0.15"),
    }
    rev = rev_map[strategy.reversibility]
    dims["reversibility"] = DimensionScore(
        schema_version=SV_DIMENSION,
        dimension="reversibility",
        value=_q(rev, quantum),
        scale="0..1",
        direction=_direction("reversibility"),
        weight=config.weights["reversibility"],
        rationale=f"reversibility={strategy.reversibility}",
        inputs=(strategy.reversibility,),
        missing_inputs=(),
        score_version=SCORE_VERSION,
    )

    # --- operational complexity (lower better) ---
    stage_n = len(strategy.stages_hint) if strategy.stages_hint else max(1, len(strategy.dependencies))
    complexity = _clamp01(Decimal(stage_n) / Decimal("10"))
    dims["operational_complexity"] = DimensionScore(
        schema_version=SV_DIMENSION,
        dimension="operational_complexity",
        value=_q(complexity, quantum),
        scale="0..1",
        direction=_direction("operational_complexity"),
        weight=config.weights["operational_complexity"],
        rationale="stage/dependency load normalized",
        inputs=(str(stage_n),),
        missing_inputs=(),
        score_version=SCORE_VERSION,
    )

    # --- uncertainty (separate from downside) ---
    assumption_n = len(strategy.assumption_claim_ids)
    open_conflicts = sum(
        1 for c in opportunity.claims if c.conflict_state == ConflictState.OPEN
    )
    missing_req = len([cid for cid in strategy.required_claim_ids if cid not in claims])
    uncertainty = _clamp01(
        Decimal("0.15") * Decimal(assumption_n)
        + Decimal("0.2") * Decimal(open_conflicts)
        + Decimal("0.25") * Decimal(missing_req)
        + Decimal("0.1") * Decimal(len(strategy.evidence_gaps))
    )
    dims["uncertainty"] = DimensionScore(
        schema_version=SV_DIMENSION,
        dimension="uncertainty",
        value=_q(uncertainty, quantum),
        scale="0..1",
        direction=_direction("uncertainty"),
        weight=config.weights["uncertainty"],
        rationale="assumptions + open conflicts + missing required claims + gaps",
        inputs=(f"assumptions:{assumption_n}", f"conflicts:{open_conflicts}"),
        missing_inputs=tuple(cid for cid in strategy.required_claim_ids if cid not in claims),
        score_version=SCORE_VERSION,
    )

    # --- downside risk ---
    fail_n = len(strategy.failure_modes)
    downside = _clamp01(Decimal("0.12") * Decimal(fail_n))
    if strategy.reversibility == "irreversible":
        downside = _clamp01(downside + Decimal("0.25"))
    if strategy.capital_intensity == "high":
        downside = _clamp01(downside + Decimal("0.2"))
    elif strategy.capital_intensity == "medium":
        downside = _clamp01(downside + Decimal("0.1"))
    dims["downside_risk"] = DimensionScore(
        schema_version=SV_DIMENSION,
        dimension="downside_risk",
        value=_q(downside, quantum),
        scale="0..1",
        direction=_direction("downside_risk"),
        weight=config.weights["downside_risk"],
        rationale="failure modes, irreversibility, capital intensity",
        inputs=tuple(strategy.failure_modes) + (strategy.capital_intensity,),
        missing_inputs=(),
        score_version=SCORE_VERSION,
    )

    # --- regulatory/policy risk ---
    reg = Decimal("0.2") if strategy.legal_policy_checks else Decimal("0.5")
    if not strategy.policy_known:
        reg = Decimal("0.9")
    if any("prohibited" in x.lower() for x in strategy.legal_policy_checks):
        reg = Decimal("1.0")
    dims["regulatory_policy_risk"] = DimensionScore(
        schema_version=SV_DIMENSION,
        dimension="regulatory_policy_risk",
        value=_q(reg, quantum),
        scale="0..1",
        direction=_direction("regulatory_policy_risk"),
        weight=config.weights["regulatory_policy_risk"],
        rationale="policy_known and legal_policy_checks",
        inputs=tuple(strategy.legal_policy_checks),
        missing_inputs=() if strategy.policy_known else ("policy",),
        score_version=SCORE_VERSION,
    )

    # --- dependency risk ---
    if strategy.dependencies:
        dep_risk = _clamp01(
            Decimal(len(miss_deps)) / Decimal(max(1, len(strategy.dependencies)))
        )
    else:
        dep_risk = Decimal("0")
    dims["dependency_risk"] = DimensionScore(
        schema_version=SV_DIMENSION,
        dimension="dependency_risk",
        value=_q(dep_risk, quantum),
        scale="0..1",
        direction=_direction("dependency_risk"),
        weight=config.weights["dependency_risk"],
        rationale="missing dependency ratio",
        inputs=tuple(strategy.dependencies),
        missing_inputs=tuple(miss_deps),
        score_version=SCORE_VERSION,
    )

    # --- confidence ---
    pieces = [
        dims["evidence_strength"].value,
        dims["evidence_completeness"].value,
        Decimal("1") - (dims["uncertainty"].value or Decimal("0")),
    ]
    pieces_f = [p for p in pieces if p is not None]
    confidence = sum(pieces_f) / Decimal(len(pieces_f)) if pieces_f else None
    dims["confidence"] = DimensionScore(
        schema_version=SV_DIMENSION,
        dimension="confidence",
        value=None if confidence is None else _q(confidence, quantum),
        scale="0..1",
        direction=_direction("confidence"),
        weight=config.weights["confidence"],
        rationale="blend of strength, completeness, inverse uncertainty",
        inputs=("evidence_strength", "evidence_completeness", "uncertainty"),
        missing_inputs=() if confidence is not None else ("confidence_inputs",),
        score_version=SCORE_VERSION,
    )

    # composite: weighted sum of directed values (missing dims skipped with penalty weight discarded)
    num = Decimal("0")
    den = Decimal("0")
    for dim in DIMENSIONS:
        ds = dims[dim]
        w = config.weights[dim]
        if ds.value is None:
            continue
        if ds.direction == ScoreDirection.HIGHER_BETTER:
            oriented = ds.value
        else:
            oriented = Decimal("1") - ds.value
        num += w * oriented
        den += w
    composite = _q(num / den, quantum) if den > 0 else None

    return ScenarioScore(
        schema_version=SV_SCENARIO,
        strategy_id=strategy.strategy_id,
        dimensions=dims,
        composite=composite,
        score_version=SCORE_VERSION,
    )
