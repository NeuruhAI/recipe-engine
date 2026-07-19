"""Exact, scoped, expiring, non-transitive approval consumption."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.contracts.approval import Approval, ApprovalDecision
from recipe_engine.founder_runtime.errors import (
    ApprovalExpired,
    ApprovalRequired,
    AuthorityDenied,
    PersistenceConflict,
)
from recipe_engine.founder_runtime.state.states import MissionState


# Transitions that require a consumed, valid approval
APPROVAL_GATED_TRANSITIONS: frozenset[tuple[MissionState, MissionState]] = frozenset({
    (MissionState.AWAITING_APPROVAL, MissionState.QUEUED),
    (MissionState.AWAITING_APPROVAL, MissionState.PLANNING),
    (MissionState.BLOCKED_POLICY, MissionState.AWAITING_APPROVAL),
    (MissionState.BLOCKED_POLICY, MissionState.PLANNING),
})


def is_approval_gated(prior: MissionState, next_state: MissionState) -> bool:
    return (prior, next_state) in APPROVAL_GATED_TRANSITIONS


def validate_approval_record(approval: Approval, *, now_iso: str) -> None:
    """Validate approval at record time (not yet consumed)."""
    if approval.decision not in (
        ApprovalDecision.PENDING,
        ApprovalDecision.ALLOW,
        ApprovalDecision.DENY,
        ApprovalDecision.ESCALATE,
    ):
        raise AuthorityDenied(
            "invalid approval decision for record",
            {"decision": approval.decision.value},
        )
    if approval.decision == ApprovalDecision.ALLOW and approval.expiry:
        if approval.expiry <= now_iso:
            raise ApprovalExpired(
                "cannot record already-expired allow approval",
                {"expiry": approval.expiry, "now": now_iso},
            )


def assert_approval_usable(
    approval: Approval,
    *,
    mission_id: str,
    action: str,
    scope: str,
    now_iso: str,
) -> None:
    """Fail-closed checks at consumption time."""
    if approval.mission_id != mission_id:
        raise AuthorityDenied("approval mission mismatch", {
            "approval_mission": approval.mission_id, "mission_id": mission_id,
        })
    if approval.decision == ApprovalDecision.DENY:
        raise AuthorityDenied("approval denied", {"approval_id": approval.approval_id})
    if approval.decision == ApprovalDecision.REVOKED:
        raise AuthorityDenied("approval revoked", {"approval_id": approval.approval_id})
    if approval.decision == ApprovalDecision.EXPIRED:
        raise ApprovalExpired("approval expired", {"approval_id": approval.approval_id})
    if approval.decision == ApprovalDecision.CONSUMED:
        raise AuthorityDenied("approval already consumed", {"approval_id": approval.approval_id})
    if approval.decision != ApprovalDecision.ALLOW:
        raise AuthorityDenied(
            "approval decision not ALLOW",
            {"decision": approval.decision.value},
        )
    if approval.expiry and approval.expiry <= now_iso:
        raise ApprovalExpired(
            "approval expired at consumption",
            {"expiry": approval.expiry, "now": now_iso},
        )
    if approval.exact_requested_action != action:
        raise AuthorityDenied(
            "approval action mismatch",
            {"approved": approval.exact_requested_action, "requested": action},
        )
    # Exact scope match — no expansion (prefix/superset rejected)
    if approval.scope != scope:
        raise AuthorityDenied(
            "approval scope mismatch",
            {"approved_scope": approval.scope, "requested_scope": scope},
        )
    if approval.consumed_by:
        raise AuthorityDenied(
            "approval already marked consumed",
            {"consumed_by": approval.consumed_by},
        )


def gated_action_for(prior: MissionState, next_state: MissionState) -> tuple[str, str]:
    """Return (exact_action, scope) expected for a gated transition."""
    action = f"transition:{prior.value}->{next_state.value}"
    scope = f"mission_transition:{prior.value}:{next_state.value}"
    return action, scope
