"""Outcome reconciliation and calibration proposals. Never mutates weights. Pure."""

from __future__ import annotations

from decimal import Decimal

from .config import EngineConfig, validate_config
from .errors import ReconciliationError, ValidationError
from .hashing import content_hash, deterministic_id
from .models import (
    SV_CALIBRATION,
    SV_RECONCILE,
    CalibrationProposal,
    CalibrationType,
    DecisionPacket,
    ErrorClass,
    OutcomeRecord,
    ReconciliationResult,
)
from .validation import _decimal_unit


def reconcile_outcome(
    packet: DecisionPacket,
    outcome: OutcomeRecord,
    config: EngineConfig | None = None,
) -> ReconciliationResult:
    cfg = validate_config(config or EngineConfig())
    if outcome.schema_version != "autonomy.outcome.v1":
        raise ReconciliationError(f"unsupported outcome schema: {outcome.schema_version}")
    if outcome.opportunity_id != packet.opportunity.opportunity_id:
        raise ReconciliationError("opportunity_id mismatch")
    if outcome.scenario_id not in packet.selected_strategy_ids and outcome.scenario_id not in {
        e.strategy.strategy_id for e in packet.evaluated
    }:
        raise ReconciliationError("scenario_id not in packet")

    pred_by = {p.metric_id: p for p in outcome.predicted}
    act_by = {a.metric_id: a for a in outcome.actual}

    prediction_errors: list[dict] = []
    data_quality: list[str] = []
    missing_evidence: list[str] = []
    error_classes: list[ErrorClass] = []

    for mid, pred in sorted(pred_by.items()):
        act = act_by.get(mid)
        if act is None:
            data_quality.append(f"missing_actual:{mid}")
            error_classes.append(ErrorClass.INCOMPLETE)
            missing_evidence.append(f"actual:{mid}")
            continue
        if act.incomplete or act.value is None:
            data_quality.append(f"incomplete_actual:{mid}")
            error_classes.append(ErrorClass.INCOMPLETE)
            if not act.evidence_refs:
                missing_evidence.append(f"actual_evidence:{mid}")
            continue
        if not act.evidence_refs:
            data_quality.append(f"actual_without_evidence:{mid}")
            error_classes.append(ErrorClass.DATA_QUALITY)
            missing_evidence.append(f"actual_evidence:{mid}")
            continue
        if act.unit != pred.unit or act.scale != pred.scale:
            data_quality.append(
                f"unit_mismatch:{mid}:pred={pred.unit}/{pred.scale}:act={act.unit}/{act.scale}"
            )
            error_classes.append(ErrorClass.DATA_QUALITY)
            continue
        try:
            pv = _decimal_unit(pred.value, f"predicted.{mid}")
            av = _decimal_unit(act.value, f"actual.{mid}")
        except ValidationError as exc:
            data_quality.append(f"invalid_numeric:{mid}:{exc.message}")
            error_classes.append(ErrorClass.DATA_QUALITY)
            continue
        err = av - pv
        direction = "exact" if err == 0 else ("over" if err > 0 else "under")
        # over means actual > predicted
        prediction_errors.append(
            {
                "metric_id": mid,
                "predicted": str(pv),
                "actual": str(av),
                "error": str(err),
                "direction": direction,
                "unit": pred.unit,
                "scale": pred.scale,
            }
        )
        if err != 0:
            error_classes.append(ErrorClass.PREDICTION)

    if outcome.execution_failed:
        error_classes.append(ErrorClass.EXECUTION)
    if outcome.governance_failed:
        error_classes.append(ErrorClass.GOVERNANCE)
    if outcome.external_shocks:
        error_classes.append(ErrorClass.EXTERNAL_SHOCK)
    if outcome.incomplete:
        error_classes.append(ErrorClass.INCOMPLETE)

    # failed assumptions: assumption claims that appear in notes or strategy
    strategy = next(
        (e.strategy for e in packet.evaluated if e.strategy.strategy_id == outcome.scenario_id),
        None,
    )
    failed_assumptions: list[str] = []
    if strategy is not None:
        for note in outcome.notes:
            if note.startswith("failed_assumption:"):
                failed_assumptions.append(note.split(":", 1)[1])
        for aid in strategy.assumption_claim_ids:
            if any(aid in n for n in outcome.notes):
                if aid not in failed_assumptions:
                    failed_assumptions.append(aid)
    if failed_assumptions:
        error_classes.append(ErrorClass.STRATEGY)

    # outcomes
    if outcome.execution_failed:
        process_outcome = "execution_failure"
    elif outcome.incomplete:
        process_outcome = "incomplete"
    else:
        process_outcome = "completed"

    if prediction_errors and any(p["direction"] != "exact" for p in prediction_errors):
        strategy_outcome = "prediction_miss"
    elif failed_assumptions:
        strategy_outcome = "assumption_failure"
    else:
        strategy_outcome = "aligned"

    governance_outcome = "governance_failure" if outcome.governance_failed else "ok"

    # Calibration proposals — scoped, promotion prohibited
    proposals: list[CalibrationProposal] = []
    scope = f"strategy:{outcome.scenario_id}|domain:{packet.opportunity.domain}"

    if any(p["direction"] != "exact" for p in prediction_errors):
        proposals.append(
            CalibrationProposal(
                schema_version=SV_CALIBRATION,
                proposal_id=deterministic_id("cal", packet.packet_id, outcome.scenario_id, "conf"),
                proposal_type=CalibrationType.CONFIDENCE_CALIBRATION,
                scope=scope,
                evidence=tuple(f"{p['metric_id']}:{p['error']}" for p in prediction_errors),
                required_reviewer="domain_owner",
                confidence=Decimal("0.55"),
                promotion_prohibited=True,
                detail="Single-outcome confidence calibration proposal; not global.",
                suggested_delta={"apply_globally": False, "scope": scope},
            )
        )
    if failed_assumptions:
        proposals.append(
            CalibrationProposal(
                schema_version=SV_CALIBRATION,
                proposal_id=deterministic_id("cal", packet.packet_id, outcome.scenario_id, "elig"),
                proposal_type=CalibrationType.STRATEGY_ELIGIBILITY_CHANGE,
                scope=scope,
                evidence=tuple(failed_assumptions),
                required_reviewer="strategy_owner",
                confidence=Decimal("0.6"),
                promotion_prohibited=True,
                detail="Failed assumptions may justify eligibility review for this strategy family only.",
                suggested_delta={
                    "apply_globally": False,
                    "strategy_family": strategy.strategy_family if strategy else "unknown",
                },
            )
        )
    if any(x.startswith("unit_mismatch:") for x in data_quality):
        proposals.append(
            CalibrationProposal(
                schema_version=SV_CALIBRATION,
                proposal_id=deterministic_id("cal", packet.packet_id, outcome.scenario_id, "dq"),
                proposal_type=CalibrationType.DATA_SOURCE_DOWNGRADE,
                scope=scope,
                evidence=tuple(x for x in data_quality if x.startswith("unit_mismatch:")),
                required_reviewer="data_steward",
                confidence=Decimal("0.7"),
                promotion_prohibited=True,
                detail="Unit/scale mismatches indicate source quality issues; scoped downgrade only.",
                suggested_delta={"apply_globally": False},
            )
        )
    if outcome.external_shocks:
        proposals.append(
            CalibrationProposal(
                schema_version=SV_CALIBRATION,
                proposal_id=deterministic_id("cal", packet.packet_id, outcome.scenario_id, "stop"),
                proposal_type=CalibrationType.STOP_CONDITION_ADDITION,
                scope=scope,
                evidence=tuple(outcome.external_shocks),
                required_reviewer="operator",
                confidence=Decimal("0.5"),
                promotion_prohibited=True,
                detail="External shocks observed; propose additional stop conditions for this scope.",
                suggested_delta={"apply_globally": False, "shocks": list(outcome.external_shocks)},
            )
        )

    # Ensure no proposal is global
    for p in proposals:
        if p.suggested_delta.get("apply_globally") is True:
            raise ReconciliationError("global calibration promotion forbidden")
        if not p.promotion_prohibited:
            raise ReconciliationError("promotion_prohibited must be true")

    unique_classes = tuple(sorted(set(error_classes), key=lambda e: e.value))
    result_id = deterministic_id("rec", packet.packet_id, outcome.scenario_id, prediction_errors)
    receipt = content_hash(
        {
            "result_id": result_id,
            "prediction_errors": prediction_errors,
            "proposals": [p.to_dict() for p in proposals],
            "error_classes": [e.value for e in unique_classes],
        }
    )

    prop_conf = (
        min(p.confidence for p in proposals) if proposals else Decimal("0")
    )

    return ReconciliationResult(
        schema_version=SV_RECONCILE,
        result_id=result_id,
        opportunity_id=outcome.opportunity_id,
        scenario_id=outcome.scenario_id,
        prediction_errors=tuple(prediction_errors),
        failed_assumptions=tuple(failed_assumptions),
        missing_evidence=tuple(sorted(set(missing_evidence))),
        error_classes=unique_classes,
        strategy_outcome=strategy_outcome,
        process_outcome=process_outcome,
        governance_outcome=governance_outcome,
        data_quality_findings=tuple(data_quality),
        calibration_proposals=tuple(proposals),
        proposal_confidence=prop_conf,
        required_reviewer="domain_owner",
        promotion_prohibited=True,
        receipt=receipt,
        timestamp=cfg.fixed_timestamp,
        engine_version=cfg.engine_version,
    )
