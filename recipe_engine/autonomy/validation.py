"""Contract validation. Fail-closed. Pure; no I/O."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

from .errors import ValidationError
from .models import (
    SV_CLAIM,
    SV_EVIDENCE,
    SV_METRIC,
    SV_OPPORTUNITY,
    SV_STRATEGY,
    AuthorityLevel,
    CandidateStrategy,
    Claim,
    ClaimClass,
    ConflictState,
    EvidenceReference,
    MetricValue,
    OpportunityEnvelope,
    Sensitivity,
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FRESHNESS = frozenset({"current", "stale", "expired"})
_METRIC_SOURCES = frozenset({"domain_supplied", "operator_supplied"})
_REVERSIBILITY = frozenset({"reversible", "partially_reversible", "irreversible"})


def _require_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValidationError(f"invalid {field}: {value!r}")
    return value


def _decimal_unit(value: Any, field: str, lo: Decimal | None = None, hi: Decimal | None = None) -> Decimal:
    try:
        if isinstance(value, Decimal):
            d = value
        elif isinstance(value, (int, str)):
            d = Decimal(str(value))
        elif isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise ValidationError(f"{field} is NaN/Infinity")
            d = Decimal(str(value))
        else:
            raise ValidationError(f"{field} must be numeric")
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{field} invalid decimal") from exc
    if d.is_nan() or d.is_infinite():
        raise ValidationError(f"{field} is NaN/Infinity")
    if lo is not None and d < lo:
        raise ValidationError(f"{field} below minimum {lo}")
    if hi is not None and d > hi:
        raise ValidationError(f"{field} above maximum {hi}")
    return d


def validate_evidence(ev: EvidenceReference) -> EvidenceReference:
    if ev.schema_version != SV_EVIDENCE:
        raise ValidationError(f"unsupported evidence schema: {ev.schema_version}")
    _require_id(ev.evidence_id, "evidence_id")
    if not ev.source_type or not ev.internal_pointer:
        raise ValidationError("evidence requires source_type and internal_pointer")
    if not ev.content_hash or not ev.provenance_chain_id:
        raise ValidationError("evidence requires content_hash and provenance_chain_id")
    if ev.freshness not in _FRESHNESS:
        raise ValidationError(f"invalid freshness: {ev.freshness}")
    if not isinstance(ev.sensitivity, Sensitivity):
        raise ValidationError("invalid sensitivity")
    if not isinstance(ev.authority_level, AuthorityLevel):
        raise ValidationError("invalid authority_level")
    if ev.authority_level == AuthorityLevel.UNKNOWN:
        raise ValidationError("unknown evidence authority fails closed", {"evidence_id": ev.evidence_id})
    return ev


def validate_claim(
    claim: Claim,
    evidence_by_id: dict[str, EvidenceReference],
) -> Claim:
    if claim.schema_version != SV_CLAIM:
        raise ValidationError(f"unsupported claim schema: {claim.schema_version}")
    _require_id(claim.claim_id, "claim_id")
    if not claim.statement.strip():
        raise ValidationError("claim statement required")
    conf = _decimal_unit(claim.confidence, "confidence", Decimal("0"), Decimal("1"))
    sconf = _decimal_unit(claim.source_confidence, "source_confidence", Decimal("0"), Decimal("1"))
    if claim.freshness not in _FRESHNESS:
        raise ValidationError(f"invalid claim freshness: {claim.freshness}")
    if not isinstance(claim.classification, ClaimClass):
        raise ValidationError("invalid claim classification")
    if not isinstance(claim.conflict_state, ConflictState):
        raise ValidationError("invalid conflict_state")

    # classification rules
    if claim.classification == ClaimClass.OBSERVED:
        if not claim.evidence_refs:
            raise ValidationError("observed claims require evidence", {"claim_id": claim.claim_id})
        for eid in claim.evidence_refs:
            if eid not in evidence_by_id:
                raise ValidationError(f"missing evidence for observed claim: {eid}")
    if claim.classification == ClaimClass.CORROBORATED:
        if claim.creator.startswith("model:"):
            raise ValidationError(
                "model-originated claims cannot self-promote to corroborated",
                {"claim_id": claim.claim_id},
            )
        sources = set()
        for eid in claim.evidence_refs:
            ev = evidence_by_id.get(eid)
            if ev is None:
                raise ValidationError(f"missing evidence for corroborated claim: {eid}")
            if ev.authority_level in (
                AuthorityLevel.DOMAIN_AUTHORITY,
                AuthorityLevel.OPERATOR,
                AuthorityLevel.GOVERNANCE,
            ):
                sources.add(f"auth:{ev.authority_level.value}")
            else:
                sources.add(f"src:{ev.source_type}")
        auth_ok = any(s.startswith("auth:") for s in sources)
        if not auth_ok and len(sources) < 2:
            raise ValidationError(
                "corroborated claims require two distinct sources or authoritative source",
                {"claim_id": claim.claim_id},
            )
    if claim.classification == ClaimClass.INFERRED:
        # may not silently be treated as observed elsewhere; structure ok
        pass
    if claim.classification == ClaimClass.ASSUMPTION:
        # must remain visible; no promotion rules here
        pass

    # rebuild with normalized decimals if needed
    if conf != claim.confidence or sconf != claim.source_confidence:
        return Claim(
            schema_version=claim.schema_version,
            claim_id=claim.claim_id,
            statement=claim.statement,
            classification=claim.classification,
            evidence_refs=claim.evidence_refs,
            confidence=conf,
            source_confidence=sconf,
            conflict_state=claim.conflict_state,
            verification_requirements=claim.verification_requirements,
            freshness=claim.freshness,
            expires_at=claim.expires_at,
            creator=claim.creator,
            version=claim.version,
            critical=claim.critical,
        )
    return claim


def validate_metric(m: MetricValue) -> MetricValue:
    if m.schema_version != SV_METRIC:
        raise ValidationError(f"unsupported metric schema: {m.schema_version}")
    _require_id(m.metric_id, "metric_id")
    val = _decimal_unit(m.value, "metric.value")
    if m.source not in _METRIC_SOURCES:
        raise ValidationError(
            "metric source must be domain_supplied or operator_supplied (never engine-invented)",
            {"metric_id": m.metric_id, "source": m.source},
        )
    if not m.unit or not m.scale:
        raise ValidationError("metric requires unit and scale")
    if val != m.value:
        return MetricValue(
            schema_version=m.schema_version,
            metric_id=m.metric_id,
            value=val,
            unit=m.unit,
            scale=m.scale,
            source=m.source,
            label=m.label,
        )
    return m


def validate_opportunity(opp: OpportunityEnvelope, *, max_claims: int, max_evidence: int) -> OpportunityEnvelope:
    if opp.schema_version != SV_OPPORTUNITY:
        raise ValidationError(f"unsupported opportunity schema: {opp.schema_version}")
    _require_id(opp.opportunity_id, "opportunity_id")
    if not opp.domain or not opp.desired_outcome:
        raise ValidationError("domain and desired_outcome required")
    if len(opp.claims) > max_claims:
        raise ValidationError("claim count exceeds bound")
    if len(opp.evidence) > max_evidence:
        raise ValidationError("evidence count exceeds bound")
    completeness = _decimal_unit(
        opp.evidence_completeness, "evidence_completeness", Decimal("0"), Decimal("1")
    )
    evidence_by_id: dict[str, EvidenceReference] = {}
    for ev in opp.evidence:
        validate_evidence(ev)
        if ev.evidence_id in evidence_by_id:
            raise ValidationError(f"duplicate evidence_id: {ev.evidence_id}")
        evidence_by_id[ev.evidence_id] = ev
    claim_ids: set[str] = set()
    claims: list[Claim] = []
    for c in opp.claims:
        cc = validate_claim(c, evidence_by_id)
        if cc.claim_id in claim_ids:
            raise ValidationError(f"duplicate claim_id: {cc.claim_id}")
        claim_ids.add(cc.claim_id)
        claims.append(cc)
    metrics = tuple(validate_metric(m) for m in opp.supplied_financial_inputs)
    return OpportunityEnvelope(
        schema_version=opp.schema_version,
        opportunity_id=opp.opportunity_id,
        domain=opp.domain,
        territory=opp.territory,
        subject=opp.subject,
        assets=opp.assets,
        desired_outcome=opp.desired_outcome,
        constraints=opp.constraints,
        authority_context=opp.authority_context,
        time_horizon=opp.time_horizon,
        claims=tuple(claims),
        evidence=opp.evidence,
        evidence_completeness=completeness,
        supplied_financial_inputs=metrics,
        risk_tolerance=opp.risk_tolerance,
        operator_requirements=opp.operator_requirements,
        version=opp.version,
        extensions=opp.extensions,
    )


def validate_strategy(s: CandidateStrategy) -> CandidateStrategy:
    if s.schema_version != SV_STRATEGY:
        raise ValidationError(f"unsupported strategy schema: {s.schema_version}")
    _require_id(s.strategy_id, "strategy_id")
    if not s.title or not s.thesis or not s.strategy_family:
        raise ValidationError("strategy requires title, thesis, strategy_family")
    if s.reversibility not in _REVERSIBILITY:
        raise ValidationError(f"invalid reversibility: {s.reversibility}")
    if not s.next_safe_action:
        raise ValidationError("next_safe_action required")
    if not s.governance_class:
        raise ValidationError("governance_class required")
    outcomes = tuple(validate_metric(m) for m in s.expected_outcomes)
    return CandidateStrategy(
        schema_version=s.schema_version,
        strategy_id=s.strategy_id,
        title=s.title,
        thesis=s.thesis,
        strategy_family=s.strategy_family,
        required_claim_ids=s.required_claim_ids,
        assumption_claim_ids=s.assumption_claim_ids,
        expected_outcomes=outcomes,
        resources=s.resources,
        dependencies=s.dependencies,
        duration=s.duration,
        reversibility=s.reversibility,
        failure_modes=s.failure_modes,
        legal_policy_checks=s.legal_policy_checks,
        governance_class=s.governance_class,
        next_safe_action=s.next_safe_action,
        stop_conditions=s.stop_conditions,
        evidence_gaps=s.evidence_gaps,
        action_classes=s.action_classes,
        capital_intensity=s.capital_intensity,
        time_profile=s.time_profile,
        available_dependencies=s.available_dependencies,
        missing_dependencies=s.missing_dependencies,
        feasibility_notes=s.feasibility_notes,
        prohibited=s.prohibited,
        policy_known=s.policy_known,
        stages_hint=s.stages_hint,
        extensions=s.extensions,
    )


def validate_candidates(
    candidates: Sequence[CandidateStrategy], *, max_candidates: int
) -> list[CandidateStrategy]:
    if len(candidates) > max_candidates:
        raise ValidationError("candidate count exceeds bound")
    seen: set[str] = set()
    out: list[CandidateStrategy] = []
    for c in candidates:
        sc = validate_strategy(c)
        if sc.strategy_id in seen:
            raise ValidationError(f"duplicate strategy_id: {sc.strategy_id}")
        seen.add(sc.strategy_id)
        out.append(sc)
    # stable order by strategy_id for determinism under permutation
    out.sort(key=lambda x: x.strategy_id)
    return out


def collect_open_conflicts(opp: OpportunityEnvelope) -> list[str]:
    conflicts: list[str] = []
    for c in opp.claims:
        if c.conflict_state == ConflictState.OPEN:
            conflicts.append(c.claim_id)
    for e in opp.evidence:
        conflicts.extend(list(e.conflict_ids))
    return sorted(set(conflicts))


def collect_missing_required_claims(
    strategy: CandidateStrategy, claim_ids: Iterable[str]
) -> list[str]:
    present = set(claim_ids)
    return sorted(cid for cid in strategy.required_claim_ids if cid not in present)
