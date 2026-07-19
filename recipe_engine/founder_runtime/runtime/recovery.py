"""Startup recovery kernel."""

from __future__ import annotations

from enum import Enum
from typing import Any

from recipe_engine.founder_runtime.errors import SideEffectUncertain
from recipe_engine.founder_runtime.events.replay import replay_mission
from recipe_engine.founder_runtime.state.states import TERMINAL_STATES
from recipe_engine.founder_runtime.evidence.validation import validate_receipt


class RecoveryStatus(str, Enum):
    SAFE_TO_RESUME = "safe_to_resume"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED_DEPENDENCY = "blocked_dependency"
    UNCERTAIN_SIDE_EFFECT = "uncertain_side_effect"
    INVALID_RECEIPT = "invalid_receipt"
    UNRECOVERABLE = "unrecoverable"


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
    receipts = store.list_receipts(mission_id)
    for r in receipts:
        errs = validate_receipt(
            r,
            expected_mission_id=mission_id,
            expected_plan_id=r.plan_id,
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

    # side-effect ledger reconciliation
    side_effects = store.list_side_effects(mission_id)
    uncertain = [s for s in side_effects if s["status"] in ("proposed", "uncertain")]
    if uncertain:
        # if receipt exists for same step, reconcile; else escalate
        for se in uncertain:
            matching = [r for r in receipts if r.step_id == se["step_id"]]
            if matching:
                store.mark_side_effect(
                    se["side_effect_key"],
                    mission_id,
                    se["step_id"],
                    "reconciled",
                    se.get("marker") or {},
                    clock_now,
                    receipt_id=matching[0].receipt_id,
                )
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
    }
