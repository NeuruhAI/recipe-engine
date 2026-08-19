"""Outcome — three different questions that a single "done" flag destroys.

"Task completed" is a process fact. It is not evidence that anything happened
in the world, and it is certainly not evidence that anyone made money. A loop
that learns from process outcomes learns to complete tasks.

So an outcome is recorded in three tiers, each with its own evidence:

* **PROCESS** — what the system did. Data was found; a record was written.
* **BUSINESS** — what the operation achieved. A call connected; an appointment
  was set; a contract closed; gross profit landed.
* **WORLD** — what became true out there, independent of whether we benefited.
  The property sold. The owner moved. The case closed.

A business or world observation without evidence is refused. That is the
anti-fabrication rule: the system may always say "we completed the lookup"; it
may not say "the deal closed" because a model thought it probably had.

The metric vocabulary is generic on purpose (`GENERIC_METRICS`). DeedSonar's
economics live in DeedSonar; this contract carries `time_to_resolution`,
`cost_to_resolution`, `source_cost`, `operator_minutes`, `conversion`,
`revenue`, `gross_margin` and lets a domain register its own keys alongside.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.identity import EvidenceRef, canonical_json, digest


SCHEMA_OUTCOME = "neuruh.closed-loop.outcome.v1"
SCHEMA_OBSERVATION = "neuruh.closed-loop.outcome-observation.v1"


class OutcomeTier(str, Enum):
    PROCESS = "process"
    BUSINESS = "business"
    WORLD = "world"


#: Tiers whose observations assert something outside the system and therefore
#: cannot be recorded on the system's own say-so.
EVIDENCE_REQUIRED_TIERS: frozenset[OutcomeTier] = frozenset({
    OutcomeTier.BUSINESS, OutcomeTier.WORLD,
})


class OutcomeGrade(str, Enum):
    """Deliberately the same vocabulary the shared OutcomeRecord primitive uses,
    so a projection into `neuruh.outcome-record.v1` needs no translation."""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    INVALID = "invalid"
    UNKNOWN = "unknown"


#: Domain-neutral metric keys. A vertical may add its own; it may not redefine
#: these, because cross-domain learning depends on them meaning one thing.
GENERIC_METRICS: dict[str, str] = {
    "time_to_resolution_days": "days from decision to a resolved subject",
    "cost_to_resolution": "total cost attributable to resolving the subject",
    "source_cost": "cost paid to the data source(s) used",
    "operator_minutes": "human minutes spent",
    "conversion": "1 if the tracked conversion occurred, else 0",
    "revenue": "revenue recognised against the subject",
    "gross_margin": "revenue minus directly attributable cost",
}


class OutcomeError(ValueError):
    """The outcome cannot be recorded as stated."""


@dataclass(frozen=True)
class OutcomeMetric:
    """One measured quantity. `value=None` means measured-and-unavailable, which
    is different from zero and must stay different."""
    metric_key: str
    value: Decimal | None
    unit: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.metric_key).strip():
            raise OutcomeError("metric_key must be non-empty")
        if not str(self.unit).strip():
            raise OutcomeError("metric unit must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {"metric_key": self.metric_key,
                "value": None if self.value is None else str(self.value),
                "unit": self.unit, "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True)
class OutcomeObservation:
    """One thing that turned out to be so, at one tier."""
    observation_id: str
    tier: OutcomeTier
    fact_key: str
    value: Any
    observed_at: str
    evidence: tuple[EvidenceRef, ...] = ()
    metrics: tuple[OutcomeMetric, ...] = ()

    def __post_init__(self) -> None:
        for name in ("observation_id", "fact_key", "observed_at"):
            if not str(getattr(self, name)).strip():
                raise OutcomeError(f"observation.{name} must be non-empty")
        if not isinstance(self.tier, OutcomeTier):
            raise OutcomeError("observation.tier must be an OutcomeTier")
        if self.tier in EVIDENCE_REQUIRED_TIERS and not self.evidence:
            raise OutcomeError(
                f"a {self.tier.value} outcome asserts something outside this system "
                "and requires evidence; process completion is not evidence of it")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_OBSERVATION,
                "observation_id": self.observation_id, "tier": self.tier.value,
                "fact_key": self.fact_key, "value": self.value,
                "observed_at": self.observed_at,
                "evidence": [e.to_dict() for e in self.evidence],
                "metrics": [m.to_dict() for m in self.metrics]}


@dataclass(frozen=True)
class LoopOutcomeRecord:
    """Everything observed about one subject under one decision, by tier.

    Binds the decision and the execution receipt by digest, so the outcome can
    be attributed without anyone re-deriving "which run was this again?".
    """
    outcome_id: str
    subject_id: str
    decision_id: str
    decision_digest: str
    action_receipt_digest: str | None
    forecast_digest: str | None
    observations: tuple[OutcomeObservation, ...]
    grade: OutcomeGrade
    grader_role: str | None
    graded_at: str | None
    observed_at: str
    lost_reason_code: str | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("outcome_id", "subject_id", "decision_id", "decision_digest", "observed_at"):
            if not str(getattr(self, name)).strip():
                raise OutcomeError(f"outcome.{name} must be non-empty")
        if not isinstance(self.grade, OutcomeGrade):
            raise OutcomeError("outcome.grade must be an OutcomeGrade")
        if not self.observations:
            raise OutcomeError("an outcome record with no observations records nothing")
        if self.grade is OutcomeGrade.FAILURE and not self.lost_reason_code:
            raise OutcomeError(
                "a failure must name its lost_reason_code — 'it did not work' is "
                "not something the loop can learn from")
        if (self.grader_role is None) != (self.graded_at is None):
            raise OutcomeError("grader_role and graded_at must be present together")
        for obs in self.observations:
            if str(obs.observed_at) > str(self.observed_at):
                raise OutcomeError(
                    f"observation {obs.observation_id} is later than the record's "
                    "observed_at; the record cannot predate what it contains")

    def tier(self, tier: OutcomeTier) -> tuple[OutcomeObservation, ...]:
        return tuple(o for o in self.observations if o.tier is tier)

    def has_business_outcome(self) -> bool:
        return bool(self.tier(OutcomeTier.BUSINESS))

    def metric(self, metric_key: str) -> OutcomeMetric | None:
        for obs in self.observations:
            for metric in obs.metrics:
                if metric.metric_key == metric_key:
                    return metric
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_OUTCOME,
            "outcome_id": self.outcome_id,
            "subject_id": self.subject_id,
            "decision_id": self.decision_id,
            "decision_digest": self.decision_digest,
            "action_receipt_digest": self.action_receipt_digest,
            "forecast_digest": self.forecast_digest,
            "observations": [o.to_dict() for o in self.observations],
            "grade": self.grade.value,
            "grader_role": self.grader_role,
            "graded_at": self.graded_at,
            "observed_at": self.observed_at,
            "lost_reason_code": self.lost_reason_code,
            "notes": list(self.notes),
        }

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    def calibration_blockers(self) -> tuple[str, ...]:
        """Why this outcome may not be scored — stated, never implied.

        Empty means every structural precondition for calibration holds. It is
        still not permission: `promotion.py` applies the world-mode rule, and
        the shared 017 boundary applies its own.
        """
        blockers: list[str] = []
        if self.forecast_digest is None:
            blockers.append("no sealed pre-outcome forecast bound")
        if self.action_receipt_digest is None:
            blockers.append("no execution receipt bound")
        if self.grade in (OutcomeGrade.INVALID, OutcomeGrade.UNKNOWN):
            blockers.append(f"grade {self.grade.value} carries no calibration actual")
        if self.grader_role is None:
            blockers.append("no human grade")
        return tuple(blockers)
