"""Checkpoint contract for recovery boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from recipe_engine.founder_runtime.ids import hash_payload


SCHEMA_CHECKPOINT = "fr.checkpoint.v1"


@dataclass
class Checkpoint:
    schema_version: str
    checkpoint_id: str
    mission_id: str
    plan_id: str | None
    completed_steps: list[str]
    active_step: str | None
    pending_steps: list[str]
    last_receipt: str | None
    state_hash: str
    resumable: bool
    created_at: str
    kind: str = "generic"  # before_side_effect | after_step | after_approval | ...
    idempotency_key: str = ""
    mission_state: str = ""
    mission_version: int = 0
    projection_hash: str = ""

    def compute_state_hash(self, mission_dict: Mapping[str, Any] | None = None) -> str:
        body = {
            "mission_id": self.mission_id,
            "plan_id": self.plan_id,
            "completed_steps": list(self.completed_steps),
            "active_step": self.active_step,
            "pending_steps": list(self.pending_steps),
            "last_receipt": self.last_receipt,
            "mission_state": self.mission_state,
            "mission_version": self.mission_version,
            "mission": dict(mission_dict) if mission_dict else None,
        }
        self.state_hash = hash_payload(body)
        return self.state_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "checkpoint_id": self.checkpoint_id,
            "mission_id": self.mission_id,
            "plan_id": self.plan_id,
            "completed_steps": list(self.completed_steps),
            "active_step": self.active_step,
            "pending_steps": list(self.pending_steps),
            "last_receipt": self.last_receipt,
            "state_hash": self.state_hash,
            "resumable": self.resumable,
            "created_at": self.created_at,
            "kind": self.kind,
            "idempotency_key": self.idempotency_key,
            "mission_state": self.mission_state,
            "mission_version": self.mission_version,
            "projection_hash": self.projection_hash,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Checkpoint":
        return Checkpoint(
            schema_version=str(d["schema_version"]),
            checkpoint_id=str(d["checkpoint_id"]),
            mission_id=str(d["mission_id"]),
            plan_id=d.get("plan_id"),
            completed_steps=list(d.get("completed_steps") or []),
            active_step=d.get("active_step"),
            pending_steps=list(d.get("pending_steps") or []),
            last_receipt=d.get("last_receipt"),
            state_hash=str(d.get("state_hash") or ""),
            resumable=bool(d.get("resumable", True)),
            created_at=str(d["created_at"]),
            kind=str(d.get("kind") or "generic"),
            idempotency_key=str(d.get("idempotency_key") or ""),
            mission_state=str(d.get("mission_state") or ""),
            mission_version=int(d.get("mission_version") or 0),
            projection_hash=str(d.get("projection_hash") or ""),
        )
