"""Startup recovery kernel."""

from __future__ import annotations

from enum import Enum
from typing import Any

from recipe_engine.founder_runtime.errors import SideEffectUncertain
from recipe_engine.founder_runtime.events.replay import replay_mission
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.state.states import MissionState, TERMINAL_STATES
from recipe_engine.founder_runtime.evidence.validation import validate_receipt


class RecoveryStatus(str, Enum):
    SAFE_TO_RESUME = "safe_to_resume"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED_DEPENDENCY = "blocked_dependency"
    UNCERTAIN_SIDE_EFFECT = "uncertain_side_effect"
    INVALID_RECEIPT = "invalid_receipt"
    INVALID_CHECKPOINT = "invalid_checkpoint"
    UNRECOVERABLE = "unrecoverable"


# States that park a mission on something outside the runtime.  Recovery must
# report the reason it is parked rather than declaring it safe to resume — the
# boundary is real, and a caller that resumed from here would be resuming past
# an authority or dependency gate that was never satisfied.
_PARKED_STATES: dict[MissionState, RecoveryStatus] = {
    MissionState.AWAITING_APPROVAL: RecoveryStatus.APPROVAL_REQUIRED,
    MissionState.APPROVED_NO_EXECUTION: RecoveryStatus.APPROVAL_REQUIRED,
    MissionState.BLOCKED_DEPENDENCY: RecoveryStatus.BLOCKED_DEPENDENCY,
    MissionState.BLOCKED_INPUT: RecoveryStatus.BLOCKED_DEPENDENCY,
    MissionState.BLOCKED_POLICY: RecoveryStatus.BLOCKED_DEPENDENCY,
}


def verify_checkpoint(store, ckpt, mission) -> list[str]:
    """Return the reasons a checkpoint may not be trusted as a resume boundary.

    The checkpoint carries ``state_hash`` and ``projection_hash``, computed at
    capture time and — before this — never read back, so a stale or tampered
    checkpoint was trusted verbatim as the resume boundary.

    The anchor is the event log rather than a recomputed hash.  Every checkpoint
    is persisted only after a ``CHECKPOINT_RECORDED`` event carrying a full copy
    of it (``sqlite.save_checkpoint`` -> ``_require_logged_object``), and that
    event is inside the hash chain ``verify_mission_chain`` already validates.
    A checkpoint row that disagrees with its logged twin has been altered
    out-of-band; one with no logged twin was never legitimately written.

    Recomputing ``state_hash`` directly is not available here: it is taken over
    the mission body *before* the checkpoint's own event increments the version,
    so the live projection is always one version ahead of it.  The equality
    branch below therefore fires only in the rare case where they coincide.
    """
    issues: list[str] = []
    if ckpt.mission_id != mission.mission_id:
        issues.append("checkpoint_mission_mismatch")
    if ckpt.mission_version > mission.version:
        issues.append("checkpoint_ahead_of_projection")

    logged = _logged_checkpoint(store, ckpt)
    if logged is None:
        issues.append("checkpoint_not_logged")
    elif logged != ckpt.to_dict():
        issues.append("checkpoint_diverges_from_event_log")

    if ckpt.mission_version == mission.version:
        body = mission.to_dict()
        if ckpt.projection_hash and ckpt.projection_hash != hash_payload(body):
            issues.append("checkpoint_projection_hash_mismatch")
        if ckpt.mission_state and ckpt.mission_state != mission.current_state.value:
            issues.append("checkpoint_state_mismatch")

    if ckpt.active_step and ckpt.active_step in ckpt.completed_steps:
        issues.append("checkpoint_active_step_already_completed")
    if len(set(ckpt.completed_steps)) != len(ckpt.completed_steps):
        issues.append("checkpoint_duplicate_completed_steps")
    if not ckpt.resumable:
        issues.append("checkpoint_marked_not_resumable")
    return issues


def _logged_checkpoint(store, ckpt) -> dict[str, Any] | None:
    """The copy of this checkpoint carried by its hash-chained event, if any."""
    for ev in store.list_events(ckpt.mission_id):
        payload = getattr(ev, "payload", None) or {}
        candidate = payload.get("checkpoint")
        if isinstance(candidate, dict) and candidate.get("checkpoint_id") == ckpt.checkpoint_id:
            return candidate
    return None


def recover_mission(store, mission_id: str, *, clock_now: str) -> dict[str, Any]:
    integrity = store.verify_integrity()
    if not integrity.get("ok"):
        return {
            "status": RecoveryStatus.UNRECOVERABLE.value,
            "reason": "persistence_integrity_failed",
            "integrity": integrity,
        }

    mission = store.get_mission(mission_id)
    if mission is None:
        return {"status": RecoveryStatus.UNRECOVERABLE.value, "reason": "mission_not_found"}

    if mission.current_state in TERMINAL_STATES:
        return {
            "status": RecoveryStatus.SAFE_TO_RESUME.value,
            "reason": "already_terminal",
            "mission_id": mission_id,
            "state": mission.current_state.value,
            "next_step": None,
        }

    try:
        replay = replay_mission(store, mission_id)
    except Exception as e:
        return {
            "status": RecoveryStatus.UNRECOVERABLE.value,
            "reason": "replay_failed",
            "error": str(e),
        }

    ckpt = store.latest_checkpoint(mission_id)
    if ckpt is not None:
        ckpt_issues = verify_checkpoint(store, ckpt, mission)
        if ckpt_issues:
            return {
                "status": RecoveryStatus.INVALID_CHECKPOINT.value,
                "reason": "checkpoint_not_trustworthy",
                "issues": ckpt_issues,
                "mission_id": mission_id,
                "state": mission.current_state.value,
                "checkpoint": ckpt.to_dict(),
            }

    receipts = store.list_receipts(mission_id)
    for r in receipts:
        errs = validate_receipt(
            r,
            expected_mission_id=mission_id,
            # Bind against the mission's own plan, not the receipt's copy of it.
            # Passing r.plan_id compared the receipt to itself, so the plan-binding
            # check could never fire and a receipt carrying a foreign plan_id
            # passed recovery unchallenged.
            expected_plan_id=mission.plan_id or r.plan_id,
            expected_step_id=r.step_id,
            expected_input_hash=None,
            require_authority=True,
        )
        if errs:
            return {
                "status": RecoveryStatus.INVALID_RECEIPT.value,
                "reason": "invalid_receipt",
                "errors": errs,
                "receipt_id": r.receipt_id,
            }

    # R-M is deliberately deferred: recovery observes but never reconciles an
    # external side effect.  No dispatcher consumes this plan in P1.2.
    side_effects = store.list_side_effects(mission_id)
    uncertain = [s for s in side_effects if s["status"] in ("proposed", "uncertain")]
    if uncertain:
        # if receipt exists for same step, reconcile; else escalate
        for se in uncertain:
            matching = [r for r in receipts if r.step_id == se["step_id"]]
            if matching:
                continue
            else:
                return {
                    "status": RecoveryStatus.UNCERTAIN_SIDE_EFFECT.value,
                    "reason": "side_effect_without_receipt",
                    "side_effect": se,
                    "checkpoint": None if ckpt is None else ckpt.to_dict(),
                    "replay": {"final_state": replay["final_state"], "event_count": replay["event_count"]},
                }

    completed = set(ckpt.completed_steps) if ckpt else set()
    pending = list(ckpt.pending_steps) if ckpt else []
    next_step = None
    for s in pending:
        if s not in completed:
            next_step = s
            break
    if next_step is None and ckpt and ckpt.active_step and ckpt.active_step not in completed:
        # check side effect for active step
        se_for_active = [s for s in store.list_side_effects(mission_id) if s["step_id"] == ckpt.active_step]
        if se_for_active and se_for_active[0]["status"] in ("acknowledged", "reconciled"):
            # do not rerun
            completed.add(ckpt.active_step)
            next_step = pending[0] if pending else None
        elif se_for_active and se_for_active[0]["status"] == "proposed":
            return {
                "status": RecoveryStatus.UNCERTAIN_SIDE_EFFECT.value,
                "reason": "active_step_side_effect_unacked",
                "step": ckpt.active_step,
            }
        else:
            next_step = ckpt.active_step

    # stale locks
    lock_name = f"mission:{mission_id}"
    lock = store.get_lock(lock_name)
    stale_lock = False
    if lock and lock["expires_at"] <= clock_now:
        store.force_expire_lock(lock_name, clock_now)
        stale_lock = True

    # A mission parked on an approval or an unmet dependency is not "safe to
    # resume" — the gate it is waiting on has not been satisfied by anything
    # recovery did.  Report what it is parked on and withhold the next step.
    parked = _PARKED_STATES.get(mission.current_state)
    if parked is not None:
        return {
            "status": parked.value,
            "reason": f"mission_parked_in_{mission.current_state.value.lower()}",
            "mission_id": mission_id,
            "state": mission.current_state.value,
            "checkpoint": None if ckpt is None else ckpt.to_dict(),
            "completed_steps": sorted(completed),
            "next_step": None,
            "stale_lock_cleared": stale_lock,
            "replay": {
                "final_state": replay["final_state"],
                "event_count": replay["event_count"],
                "projection_hash": replay["projection_hash"],
            },
            "do_not_rerun": sorted(completed),
            "execution_resume_authorized": False,
            "p2_gate": "R-L",
            "reconciliation_mutation_authorized": False,
        }

    return {
        "status": RecoveryStatus.SAFE_TO_RESUME.value,
        "mission_id": mission_id,
        "state": mission.current_state.value,
        "checkpoint": None if ckpt is None else ckpt.to_dict(),
        "completed_steps": sorted(completed),
        "next_step": next_step,
        "stale_lock_cleared": stale_lock,
        "replay": {
            "final_state": replay["final_state"],
            "event_count": replay["event_count"],
            "projection_hash": replay["projection_hash"],
        },
        "do_not_rerun": sorted(completed),
        "execution_resume_authorized": False,
        "p2_gate": "R-L",
        "reconciliation_mutation_authorized": False,
    }
