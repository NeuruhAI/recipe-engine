"""Projections onto the shared and public primitives.

recipe-engine does not depend on `neuruh-outcome-record`, `017`, `019` or `020`
at runtime — those are separate packages with separate custody, and a private
engine that imports them at runtime quietly makes them load-bearing. So the
seam is a *projection*: this module emits the exact shapes those primitives
accept, and the receiving side validates with its own real code.

The paired half lives in `neuruh-outcome-record.adapters.from_closed_loop`,
which ingests `to_outcome_record_v1()` output and builds a verified
`neuruh.outcome-record.v1`. If the shapes ever drift, that adapter's court
fails — which is the point of putting the validation on the side that owns the
primitive.

Nothing here grants authority. Every projection carries `authority: "NONE"` or
its primitive's equivalent, and no projection can set a calibration-eligible
flag: eligibility is decided by the receiving primitive's own rules.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.attribution import AttributionSet
from recipe_engine.closed_loop.calibration import CalibrationCell, CalibrationReport
from recipe_engine.closed_loop.identity import WorldMode, digest
from recipe_engine.closed_loop.learning import LearningProposal
from recipe_engine.closed_loop.outcome import (
    LoopOutcomeRecord, OutcomeGrade, OutcomeTier,
)
from recipe_engine.closed_loop.promotion import PromotionDecision, PromotionPrerequisites


PROJECTION_VERSION = "neuruh.closed-loop.projection.v1"

#: The producer id the receiving adapter checks. Changing it is a contract
#: change and will fail the cross-repo court, which is intended.
PRODUCER_ID = "recipe-engine.closed_loop.bridges"


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def to_outcome_record_v1(
    record: LoopOutcomeRecord,
    *,
    mode: WorldMode,
    case_id: str,
    entity: Mapping[str, Any] | None = None,
    forecast_probability: Decimal | None = None,
    forecast_recorded_at: str | None = None,
    forecast_version: str | None = None,
    evidence_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Project onto `neuruh.outcome-record.v1`'s constructor arguments.

    Returns the *argument mapping*, not a built record: building it is
    `neuruh_outcome_record.build_record`'s job, and it validates as it builds.

    `is_test` / `is_synthetic` are derived from the world mode rather than
    passed in, so a fixture cannot be relabelled as live on the way across the
    seam. `calibration_eligible` is always False here — the receiving primitive
    decides eligibility on its own rules, and a producer that could assert it
    would be the hole the whole design exists to close.
    """
    process = record.tier(OutcomeTier.PROCESS)
    business = record.tier(OutcomeTier.BUSINESS)
    world = record.tier(OutcomeTier.WORLD)

    metrics: list[dict[str, Any]] = []
    for observation in record.observations:
        for metric in observation.metrics:
            metrics.append({"metric_id": metric.metric_key, "value": _num(metric.value),
                            "unit": metric.unit, "scale": observation.tier.value,
                            "evidence_refs": list(metric.evidence_ids),
                            "incomplete": metric.value is None})

    revenue = record.metric("revenue")
    cost = record.metric("cost_to_resolution")

    return {
        "producer_id": PRODUCER_ID,
        "projection_version": PROJECTION_VERSION,
        "outcome_id": record.outcome_id,
        "case_id": case_id,
        "entity": dict(entity or {}),
        "decision_receipt_digest": record.decision_digest,
        "run_receipt_digest": record.action_receipt_digest,
        "forecast": {
            "forecast_digest": record.forecast_digest,
            "forecast_version": forecast_version,
            "predicted": [],
            **({"probability": _num(forecast_probability)}
               if forecast_probability is not None else {}),
            **({"recorded_at": forecast_recorded_at}
               if forecast_recorded_at is not None else {}),
        },
        "observed": {
            "actual": metrics,
            "result": {
                "process": [o.to_dict() for o in process],
                "business": [o.to_dict() for o in business],
                "world": [o.to_dict() for o in world],
            },
            "accepted": record.grade in (OutcomeGrade.SUCCESS, OutcomeGrade.PARTIAL),
        },
        "observed_at": record.observed_at,
        "economics": {
            "revenue": _num(revenue.value) if revenue is not None else None,
            "cost": _num(cost.value) if cost is not None else None,
            "currency": "USD",
        },
        "variance": {
            "deviations": list(record.notes),
            "external_factors": [],
            "forecast_delta": None,
        },
        "reason_error": {
            "execution_failed": record.action_receipt_digest is None,
            "governance_failed": False,
            "incomplete": bool(record.calibration_blockers()),
            "errors": list(record.calibration_blockers()),
        },
        "evidence_refs": sorted(set(evidence_refs)),
        "grading": {
            "grade": record.grade.value,
            "quality": None,
            "grader_role": record.grader_role,
            "graded_at": record.graded_at,
            # Never asserted by the producer. See docstring.
            "calibration_eligible": False,
        },
        "is_test": mode in (WorldMode.FIXTURE, WorldMode.SHADOW),
        "is_synthetic": mode is WorldMode.SYNTHETIC,
    }


def to_017_summary_shape(report: CalibrationReport, cell: CalibrationCell) -> dict[str, Any]:
    """Project one calibration cell onto the 017 CalibrationSummary field set.

    The 017 primitive recomputes the summary digest and rejects a mismatch, so
    this projection deliberately omits `summary_digest`: minting one here would
    be forging the tamper-check the receiving side depends on.
    """
    if not cell.sufficient:
        raise ValueError(
            f"cell {cell.dimension.value}={cell.key} is not scored: "
            f"{cell.insufficiency_reason}")
    return {
        "calibration_key": f"{cell.dimension.value}:{cell.key}",
        "count": cell.count,
        "mean_predicted": _num(cell.mean_predicted),
        "observed_rate": _num(cell.observed_rate),
        "calibration_gap": _num(cell.calibration_gap),
        "mean_brier": _num(cell.mean_brier),
        "ledger_digest_source": report.digest,
    }


def to_019_proposal_shape(proposal: LearningProposal) -> dict[str, Any]:
    """Project onto the 019 Learning Update Proposal constructor arguments."""
    return {
        "proposal_id": proposal.proposal_id,
        "target_id": proposal.target_id,
        "target_kind": proposal.target_kind,
        "current_version": proposal.current_version,
        "current_artifact_digest": proposal.current_artifact_digest,
        "candidate_version": proposal.candidate_version,
        "candidate_artifact_digest": proposal.candidate_artifact_digest,
        "sample_count": proposal.evidence_window.sample_count,
        "changes": [
            {"change_id": f"{c.change_kind.value}:{c.target_key}",
             "path": c.target_key,
             "from_value": c.current_value,
             "to_value": c.candidate_value,
             "rationale_code": c.rationale_code}
            for c in proposal.changes
        ],
        "metric_projections": [
            {"metric_id": b.metric_key, "current_value": _num(b.current_value),
             "projected_value": _num(b.projected_value),
             "sample_count": b.measured_on_samples}
            for b in proposal.expected_benefit
        ],
        "created_at": proposal.proposed_at,
    }


def to_020_request_shape(proposal: LearningProposal,
                         prerequisites: PromotionPrerequisites,
                         *, request_id: str, requested_stage: str,
                         requested_at: str) -> dict[str, Any]:
    """Project onto the 020 Promotion Gate request field set.

    The 020 gate has no concept of world mode, so a projection alone would let
    a fixture-derived proposal be evaluated as if it were live. `evidence_modes`
    and `non_live_evidence` ride along so the caller must decide explicitly what
    to do with them — and `evaluate_promotion` here has already HELD it.
    """
    return {
        "request_id": request_id,
        "proposal_id": proposal.proposal_id,
        "proposal_digest": proposal.digest,
        "target_id": proposal.target_id,
        "target_kind": proposal.target_kind,
        "current_version": proposal.current_version,
        "candidate_version": proposal.candidate_version,
        "sample_count": proposal.evidence_window.sample_count,
        "tests_passed": prerequisites.tests_passed,
        "test_report_digest": prerequisites.test_report_digest,
        "regression_count": prerequisites.regression_count,
        "critical_regression_count": prerequisites.critical_regression_count,
        "human_approval_digest": prerequisites.human_approval_digest,
        "reversibility_contract_digest": prerequisites.reversibility_contract_digest,
        "requested_stage": requested_stage,
        "requested_at": requested_at,
        "evidence_modes": [m.value for m in prerequisites.evidence_modes],
        "non_live_evidence": [m.value for m in prerequisites.non_live_modes],
    }


def attribution_summary(attribution: AttributionSet) -> dict[str, Any]:
    """A compact, ledger-safe summary of an attribution set."""
    return {
        "attribution_id": attribution.attribution_id,
        "attribution_digest": attribution.digest,
        "strengths": {
            c.strength.value: sorted(
                x.contributor_key for x in attribution.contributions
                if x.strength is c.strength)
            for c in attribution.contributions
        },
        "learnable_count": len(attribution.learnable()),
        "unexplained": list(attribution.unexplained),
    }
