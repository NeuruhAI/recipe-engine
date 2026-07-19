"""Receipt helpers."""

from __future__ import annotations

from recipe_engine.founder_runtime.ids import hash_payload


def expected_step_input_hash(mission_id: str, plan_id: str, step_id: str, payload: dict | None = None) -> str:
    return hash_payload({
        "mission_id": mission_id,
        "plan_id": plan_id,
        "step_id": step_id,
        "payload": payload or {},
    })
