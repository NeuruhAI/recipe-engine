"""Checkpoint creation helpers."""

from __future__ import annotations

from typing import Any, Sequence

from recipe_engine.founder_runtime.contracts.checkpoint import Checkpoint, SCHEMA_CHECKPOINT
from recipe_engine.founder_runtime.contracts.mission import Mission
from recipe_engine.founder_runtime.ids import new_uuid, hash_payload


def make_checkpoint(
    mission: Mission,
    *,
    clock_now: str,
    completed_steps: Sequence[str],
    pending_steps: Sequence[str],
    active_step: str | None,
    last_receipt: str | None,
    kind: str,
    plan_id: str | None = None,
    idempotency_key: str = "",
) -> Checkpoint:
    ck = Checkpoint(
        schema_version=SCHEMA_CHECKPOINT,
        checkpoint_id=new_uuid("ckpt"),
        mission_id=mission.mission_id,
        plan_id=plan_id or mission.plan_id,
        completed_steps=list(completed_steps),
        active_step=active_step,
        pending_steps=list(pending_steps),
        last_receipt=last_receipt,
        state_hash="",
        resumable=True,
        created_at=clock_now,
        kind=kind,
        idempotency_key=idempotency_key or f"{kind}:{mission.mission_id}:{len(completed_steps)}:{active_step}",
        mission_state=mission.current_state.value,
        mission_version=mission.version,
        projection_hash=hash_payload(mission.to_dict()),
    )
    ck.compute_state_hash(mission.to_dict())
    return ck
