"""Canonical completion verifier. No generic exception can waive truth gates."""

from __future__ import annotations

from typing import Any, Sequence

from recipe_engine.founder_runtime.errors import CompletionCriteriaFailed
from recipe_engine.founder_runtime.contracts.mission import Mission, DeliverableStatus
from recipe_engine.founder_runtime.contracts.completion import CompletionReport, AcceptanceResult, SCHEMA_COMPLETION
from recipe_engine.founder_runtime.contracts.receipts import ExecutionReceipt, ReceiptStatus
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.state.transitions import is_transition_allowed
from recipe_engine.founder_runtime.ids import hash_payload


def verify_completion(
    mission: Mission,
    *,
    receipts: Sequence[ExecutionReceipt],
    artifacts: Sequence[dict[str, Any]],
    approvals: Sequence[Any],
    acceptance: Sequence[AcceptanceResult],
    exceptions: Sequence[str] = (),
    outcome: dict[str, Any] | None = None,
    require_outcome: bool = True,
    outcome_present: bool = False,
    clock_now: str = "",
    decision_packet_id: str | None = None,
    plan_id: str | None = None,
    remaining_risks: Sequence[str] | None = None,
    recommended_follow_on: Sequence[str] | None = None,
) -> CompletionReport:
    target = MissionState.COMPLETED
    if not is_transition_allowed(mission.current_state, target) and mission.current_state not in (
        MissionState.VERIFYING, MissionState.RECONCILING,
    ):
        raise CompletionCriteriaFailed("terminal transition not allowed", {"state": mission.current_state.value})
    missing = [d.deliverable_id for d in mission.deliverables if d.required and d.status != DeliverableStatus.DELIVERED]
    if missing:
        raise CompletionCriteriaFailed("required deliverables missing", {"missing": missing})
    if not acceptance or any(not a.passed or not a.evidence_ref for a in acceptance):
        raise CompletionCriteriaFailed("acceptance requires passing stored evidence", {})
    if not receipts:
        raise CompletionCriteriaFailed("missing receipts", {})
    if any(r.status != ReceiptStatus.SUCCESS or not r.receipt_hash or not r.output_hash for r in receipts):
        raise CompletionCriteriaFailed("all mandatory receipts must be validated successes", {})
    evidence_ids = {r.receipt_id for r in receipts} | {
        str(a.get("artifact_id")) for a in artifacts if a.get("artifact_id")
    }
    if any(a.evidence_ref not in evidence_ids for a in acceptance):
        raise CompletionCriteriaFailed("acceptance evidence reference unresolved", {})
    if exceptions:
        raise CompletionCriteriaFailed("generic completion exceptions cannot waive gates", {})
    if require_outcome and not outcome:
        raise CompletionCriteriaFailed("validated outcome required", {})
    if outcome:
        if outcome.get("mission_id") != mission.mission_id or outcome.get("plan_id") != (plan_id or mission.plan_id):
            raise CompletionCriteriaFailed("outcome binding mismatch", {})
        if not outcome.get("outcome_id") or not outcome.get("outcome_hash") or outcome.get("status") != "verified":
            raise CompletionCriteriaFailed("outcome is not independently verifiable", {})
        material = dict(outcome)
        supplied = material.pop("outcome_hash")
        if supplied != hash_payload(material):
            raise CompletionCriteriaFailed("outcome hash mismatch", {})
    report = CompletionReport(
        schema_version=SCHEMA_COMPLETION,
        mission_id=mission.mission_id,
        objective=mission.objective,
        final_state=target,
        delivered_artifacts=[a.get("artifact_id", "") for a in artifacts],
        acceptance_criteria_results=list(acceptance),
        evidence_refs=[r.receipt_id for r in receipts],
        code_changes=[], operational_changes=[],
        approvals_used=[getattr(a, "approval_id", str(a)) for a in approvals],
        unresolved_items=[],
        # Callers completing on live evidence name their own residual risk and
        # follow-on (e.g. a declared continuation); the fixture defaults stand
        # for every existing caller.
        remaining_risks=(list(remaining_risks) if remaining_risks is not None
                         else ["fixture_evidence_cannot_authorize_live_mutation"]),
        recommended_follow_on=(list(recommended_follow_on) if recommended_follow_on is not None
                               else ["p2_live_executor_signing_after_governance"]),
        proposed_memory_updates=[], completed_at=clock_now,
        exceptions=[], decision_packet_id=decision_packet_id,
        plan_id=plan_id or mission.plan_id,
        idempotency_key=f"completion:{mission.mission_id}",
        outcome_ref=None if not outcome else str(outcome["outcome_id"]),
        authority_refs=sorted({r.authority_reference for r in receipts}),
    )
    report.compute_hash()
    return report
