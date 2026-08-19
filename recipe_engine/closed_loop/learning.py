"""Learning proposals — candidate changes that must justify themselves in numbers.

This is the module that decides whether Neuruh gets to say "this is better".
The answer is: only with an evidence window, a population, a sample count, a
metric, and a named version pair. `propose()` refuses everything else.

The proposal is a *proposal*. It carries a candidate version and never a live
config; it has no writer; `authority` is `"NONE"`. What happens to it is
`promotion.py`'s problem, and what happens after that is governance's.

Sample floors are the BLACK doctrine's, ported by reference from
`neuruh-outcome-record.black_doctrine` (NO_FIT_LT_30): 30 observations before a
claim, 100 before a weight change, holdout required. They are restated here
rather than imported because recipe-engine does not depend on that package at
runtime — and the constants are asserted equal in the cross-repo court.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.attribution import AttributionSet, LEARNABLE_STRENGTHS
from recipe_engine.closed_loop.calibration import CalibrationCell, CalibrationReport
from recipe_engine.closed_loop.identity import canonical_json, digest


SCHEMA_LEARNING = "neuruh.closed-loop.learning-proposal.v1"
AUTHORITY = "NONE"

#: BLACK doctrine, ported by reference. See module docstring.
MIN_SAMPLES_FOR_CLAIM = 30
MIN_SAMPLES_FOR_WEIGHT_CHANGE = 100
HOLDOUT_REQUIRED = True
AUTO_APPLY_FORBIDDEN = True
BLACK_DOCTRINE_SOURCE = "neuruh-outcome-record.black_doctrine (NO_FIT_LT_30)"


class ChangeKind(str, Enum):
    """What a proposal is allowed to ask for. Nothing here touches code."""
    RECIPE_WEIGHT_ADJUSTMENT = "recipe_weight_adjustment"
    SOURCE_ORDERING = "source_ordering"
    THRESHOLD_ADJUSTMENT = "threshold_adjustment"
    ROUTING_ADJUSTMENT = "routing_adjustment"
    CONFIDENCE_RULE = "confidence_rule"
    OPERATOR_QUEUE_PRIORITY = "operator_queue_priority"


#: Changes that alter how evidence is weighed, and therefore need the higher
#: floor. Ordering and queue priority are reversible presentation changes;
#: weights and thresholds change what qualifies.
WEIGHT_CLASS_CHANGES: frozenset[ChangeKind] = frozenset({
    ChangeKind.RECIPE_WEIGHT_ADJUSTMENT,
    ChangeKind.THRESHOLD_ADJUSTMENT,
    ChangeKind.CONFIDENCE_RULE,
})


class LearningRefusal(ValueError):
    """The proposal cannot be made on this evidence. Always states why."""


@dataclass(frozen=True)
class EvidenceWindow:
    """The population a claim is about, bounded in time and named."""
    start: str
    end: str
    population: str
    sample_count: int
    holdout_count: int = 0

    def __post_init__(self) -> None:
        for name in ("start", "end", "population"):
            if not str(getattr(self, name)).strip():
                raise LearningRefusal(f"evidence_window.{name} must be non-empty")
        if str(self.end) < str(self.start):
            raise LearningRefusal("evidence window ends before it starts")
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count < 0:
            raise LearningRefusal("sample_count must be a non-negative integer")
        if isinstance(self.holdout_count, bool) or not isinstance(self.holdout_count, int) or self.holdout_count < 0:
            raise LearningRefusal("holdout_count must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "population": self.population,
                "sample_count": self.sample_count, "holdout_count": self.holdout_count}


@dataclass(frozen=True)
class ProposedChange:
    """One change, from a stated current value to a stated candidate value."""
    change_kind: ChangeKind
    target_key: str
    current_value: Any
    candidate_value: Any
    rationale_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.change_kind, ChangeKind):
            raise LearningRefusal("change_kind must be a ChangeKind")
        for name in ("target_key", "rationale_code"):
            if not str(getattr(self, name)).strip():
                raise LearningRefusal(f"change.{name} must be non-empty")
        if self.current_value == self.candidate_value:
            raise LearningRefusal(
                f"{self.target_key}: candidate equals current; that is not a change")

    def to_dict(self) -> dict[str, Any]:
        return {"change_kind": self.change_kind.value, "target_key": self.target_key,
                "current_value": self.current_value, "candidate_value": self.candidate_value,
                "rationale_code": self.rationale_code}


@dataclass(frozen=True)
class ExpectedBenefit:
    """A number, a metric, and the size of the population it was measured on."""
    metric_key: str
    current_value: Decimal
    projected_value: Decimal
    measured_on_samples: int

    def __post_init__(self) -> None:
        if not str(self.metric_key).strip():
            raise LearningRefusal("expected_benefit.metric_key must be non-empty")
        if isinstance(self.measured_on_samples, bool) or not isinstance(self.measured_on_samples, int):
            raise LearningRefusal("measured_on_samples must be an integer")
        if self.measured_on_samples < 1:
            raise LearningRefusal(
                "an expected benefit measured on nothing is an opinion")

    @property
    def delta(self) -> Decimal:
        return self.projected_value - self.current_value

    def to_dict(self) -> dict[str, Any]:
        return {"metric_key": self.metric_key, "current_value": str(self.current_value),
                "projected_value": str(self.projected_value), "delta": str(self.delta),
                "measured_on_samples": self.measured_on_samples}


@dataclass(frozen=True)
class RegressionRisk:
    """What this could break, and how badly. `unknown_risks` is not a failure
    to fill in a field — it is the honest part of the estimate."""
    risk_class: str                     # low | medium | high
    affected_populations: tuple[str, ...]
    known_regressions: int
    critical_regressions: int
    unknown_risks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.risk_class not in ("low", "medium", "high"):
            raise LearningRefusal("risk_class must be low, medium or high")
        for name in ("known_regressions", "critical_regressions"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise LearningRefusal(f"{name} must be a non-negative integer")
        if self.critical_regressions > self.known_regressions:
            raise LearningRefusal("critical regressions cannot exceed known regressions")

    def to_dict(self) -> dict[str, Any]:
        return {"risk_class": self.risk_class,
                "affected_populations": list(self.affected_populations),
                "known_regressions": self.known_regressions,
                "critical_regressions": self.critical_regressions,
                "unknown_risks": list(self.unknown_risks)}


@dataclass(frozen=True)
class LearningProposal:
    """A candidate version, with the numbers that argue for it."""
    proposal_id: str
    target_id: str
    target_kind: str
    current_version: str
    current_artifact_digest: str
    candidate_version: str
    candidate_artifact_digest: str
    evidence_window: EvidenceWindow
    changes: tuple[ProposedChange, ...]
    expected_benefit: tuple[ExpectedBenefit, ...]
    regression_risk: RegressionRisk
    calibration_report_digest: str
    supporting_attribution_digests: tuple[str, ...]
    proposed_at: str
    authority: str = AUTHORITY
    auto_apply: bool = False

    def __post_init__(self) -> None:
        if self.authority != AUTHORITY:
            raise LearningRefusal("a learning proposal grants no authority")
        if AUTO_APPLY_FORBIDDEN and self.auto_apply:
            raise LearningRefusal(
                "auto-apply is forbidden; learning proposes, it never applies")
        if self.current_version == self.candidate_version:
            raise LearningRefusal("candidate_version must differ from current_version")
        for name in ("proposal_id", "target_id", "target_kind", "current_version",
                     "candidate_version", "calibration_report_digest", "proposed_at"):
            if not str(getattr(self, name)).strip():
                raise LearningRefusal(f"proposal.{name} must be non-empty")
        if not self.changes:
            raise LearningRefusal("a proposal with no changes proposes nothing")
        if not self.expected_benefit:
            raise LearningRefusal(
                "a proposal must state its expected benefit as a metric and a number")

    @property
    def weight_class(self) -> bool:
        return any(c.change_kind in WEIGHT_CLASS_CHANGES for c in self.changes)

    @property
    def required_samples(self) -> int:
        return MIN_SAMPLES_FOR_WEIGHT_CHANGE if self.weight_class else MIN_SAMPLES_FOR_CLAIM

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_LEARNING,
            "proposal_id": self.proposal_id,
            "target_id": self.target_id,
            "target_kind": self.target_kind,
            "current_version": self.current_version,
            "current_artifact_digest": self.current_artifact_digest,
            "candidate_version": self.candidate_version,
            "candidate_artifact_digest": self.candidate_artifact_digest,
            "evidence_window": self.evidence_window.to_dict(),
            "changes": [c.to_dict() for c in self.changes],
            "expected_benefit": [b.to_dict() for b in self.expected_benefit],
            "regression_risk": self.regression_risk.to_dict(),
            "calibration_report_digest": self.calibration_report_digest,
            "supporting_attribution_digests": list(self.supporting_attribution_digests),
            "proposed_at": self.proposed_at,
            "authority": self.authority,
            "auto_apply": self.auto_apply,
            "black_doctrine_source": BLACK_DOCTRINE_SOURCE,
            "required_samples": self.required_samples,
        }

    @property
    def digest(self) -> str:
        return digest(self.to_dict())


def propose(
    *,
    proposal_id: str,
    target_id: str,
    target_kind: str,
    current_version: str,
    current_artifact_digest: str,
    candidate_version: str,
    candidate_artifact_digest: str,
    evidence_window: EvidenceWindow,
    changes: Sequence[ProposedChange],
    expected_benefit: Sequence[ExpectedBenefit],
    regression_risk: RegressionRisk,
    calibration: CalibrationReport,
    supporting_cell: CalibrationCell,
    attributions: Sequence[AttributionSet] = (),
    proposed_at: str,
) -> LearningProposal:
    """Build a proposal, or refuse and say exactly what is missing.

    The refusals, in the order they bite:

    1. the supporting calibration cell must be *in* the cited report;
    2. that cell must be scored, not below its sample floor;
    3. the evidence window's sample count must meet the floor for the change
       class — 100 for anything that reweighs evidence, 30 otherwise;
    4. a holdout is required, because a model tuned on everything it has seen
       has learned the sample, not the world;
    5. every cited attribution must contain at least one DIRECT or SUPPORTED
       contribution — ASSOCIATED lineage is not evidence of effect.
    """
    proposal = LearningProposal(
        proposal_id=proposal_id, target_id=target_id, target_kind=target_kind,
        current_version=current_version, current_artifact_digest=current_artifact_digest,
        candidate_version=candidate_version, candidate_artifact_digest=candidate_artifact_digest,
        evidence_window=evidence_window, changes=tuple(changes),
        expected_benefit=tuple(expected_benefit), regression_risk=regression_risk,
        calibration_report_digest=calibration.digest,
        supporting_attribution_digests=tuple(sorted(a.digest for a in attributions)),
        proposed_at=proposed_at)

    if calibration.cell(supporting_cell.dimension, supporting_cell.key) is None:
        raise LearningRefusal(
            "the supporting calibration cell is not present in the cited report")
    if not supporting_cell.sufficient:
        raise LearningRefusal(
            f"supporting cell {supporting_cell.dimension.value}={supporting_cell.key} "
            f"is not scored: {supporting_cell.insufficiency_reason}")

    required = proposal.required_samples
    if evidence_window.sample_count < required:
        kind = "weight-class" if proposal.weight_class else "claim"
        raise LearningRefusal(
            f"insufficient evidence for a {kind} change: {evidence_window.sample_count} "
            f"samples < {required} required ({BLACK_DOCTRINE_SOURCE})")
    if HOLDOUT_REQUIRED and evidence_window.holdout_count < 1:
        raise LearningRefusal(
            "a holdout is required; a change validated only on the data that "
            f"suggested it has learned the sample ({BLACK_DOCTRINE_SOURCE})")

    for attribution in attributions:
        if not attribution.learnable():
            raise LearningRefusal(
                f"attribution {attribution.attribution_id} carries no DIRECT or "
                "SUPPORTED contribution; associated lineage is not evidence of effect")
    return proposal
