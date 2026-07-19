"""Completion verifier."""

from __future__ import annotations

from typing import Any, Sequence

from recipe_engine.founder_runtime.errors import CompletionCriteriaFailed
from recipe_engine.founder_runtime.contracts.mission import Mission, DeliverableStatus
from recipe_engine.founder_runtime.contracts.completion import CompletionReport, AcceptanceResult, SCHEMA_COMPLETION
from recipe_engine.founder_runtime.contracts.receipts import ExecutionReceipt, ReceiptStatus
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.state.transitions import is_transition_allowed
from recipe_engine.founder_runtime.ids import new_uuid


def verify_completion(
    mission: Mission,
    *,
    receipts: Sequence[ExecutionReceipt],
    artifacts: Sequence[dict[str, Any]],
    approvals: Sequence[Any],
    acceptance: Sequence[AcceptanceResult],
    exceptions: Sequence[str] = (),
    require_outcome: bool = False,
    outcome_present: bool = False,
    clock_now: str = "",
    decision_packet_id: str | None = None,
    plan_id: str | None = None,
) -> CompletionReport:
    # terminal transition allowed?
    target = MissionState.COMPLETED_WITH_EXCEPTIONS if exceptions else MissionState.COMPLETED
    if not is_transition_allowed(mission.current_state, target) and mission.current_state not in (
        MissionState.VERIFYING, MissionState.RECONCILING
    ):
        # allow if already verifying/reconciling path checked by caller transitioning first
        if mission.current_state not in (MissionState.VERIFYING, MissionState.RECONCILING, MissionState.EXECUTING):
            raise CompletionCriteriaFailed(
                "terminal transition not allowed from current state",
                {"state": mission.current_state.value, "target": target.value},
            )

    required = [d for d in mission.deliverables if d.required]
    missing_del = [d.deliverable_id for d in required if d.status != DeliverableStatus.DELIVERED]
    if missing_del and not exceptions:
        raise CompletionCriteriaFailed("required deliverables missing", {"missing": missing_del})

    failed_acceptance = [a for a in acceptance if not a.passed]
    if failed_acceptance and not exceptions:
        raise CompletionCriteriaFailed(
            "acceptance criteria failed",
            {"failed": [a.criterion for a in failed_acceptance]},
        )

    # receipts
    if not receipts:
        raise CompletionCriteriaFailed("missing receipts", {})
    invalid = [r.receipt_id for r in receipts if r.status == ReceiptStatus.FAILURE]
    # failures only ok if disclosed in exceptions
    if invalid and not exceptions:
        raise CompletionCriteriaFailed("failed receipts without exceptions", {"receipts": invalid})

    if require_outcome and not outcome_present:
        raise CompletionCriteriaFailed("outcome reconciliation required", {})

    if target == MissionState.COMPLETED_WITH_EXCEPTIONS and not exceptions:
        raise CompletionCriteriaFailed("COMPLETED_WITH_EXCEPTIONS requires exception list", {})

    report = CompletionReport(
        schema_version=SCHEMA_COMPLETION,
        mission_id=mission.mission_id,
        objective=mission.objective,
        final_state=target,
        delivered_artifacts=[a.get("artifact_id", a.get("pointer", "")) for a in artifacts],
        acceptance_criteria_results=list(acceptance),
        evidence_refs=[r.receipt_id for r in receipts],
        code_changes=[],
        operational_changes=[],
        approvals_used=[getattr(a, "approval_id", str(a)) for a in approvals],
        unresolved_items=list(exceptions),
        remaining_risks=["fixture_only_no_live_verification"],
        recommended_follow_on=["p2_live_adapters_after_governance"],
        proposed_memory_updates=[],
        completed_at=clock_now,
        exceptions=list(exceptions),
        decision_packet_id=decision_packet_id,
        plan_id=plan_id or mission.plan_id,
        idempotency_key=f"completion:{mission.mission_id}",
    )
    report.compute_hash()
    return report
