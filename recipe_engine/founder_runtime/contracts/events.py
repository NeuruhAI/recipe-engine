"""MissionEvent contract — append-only history records."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.state.states import MissionState


SCHEMA_EVENT = "fr.event.v1"


class EventType(str, Enum):
    MISSION_CREATED = "mission_created"
    STATE_TRANSITION = "state_transition"
    CONTEXT_RESOLVED = "context_resolved"
    PLAN_COMPILED = "plan_compiled"
    GOVERNANCE_RECORDED = "governance_recorded"
    APPROVAL_RECORDED = "approval_recorded"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    RECEIPT_RECORDED = "receipt_recorded"
    CHECKPOINT_RECORDED = "checkpoint_recorded"
    ARTIFACT_REGISTERED = "artifact_registered"
    COMPLETION_RECORDED = "completion_recorded"
    RECOVERY_PERFORMED = "recovery_performed"
    LOCK_ACQUIRED = "lock_acquired"
    LOCK_RELEASED = "lock_released"
    SIDE_EFFECT_MARKED = "side_effect_marked"
    SIDE_EFFECT_RECONCILED = "side_effect_reconciled"
    EXCEPTION_DISCLOSED = "exception_disclosed"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


@dataclass
class MissionEvent:
    schema_version: str
    event_id: str
    mission_id: str
    prior_state: MissionState | None
    next_state: MissionState
    event_type: EventType
    actor: str
    reason: str
    evidence_refs: list[str]
    timestamp: str
    idempotency_key: str
    causation_id: str | None
    correlation_id: str | None
    event_hash: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    sequence: int | None = None

    def compute_hash(self) -> str:
        body = {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "mission_id": self.mission_id,
            "prior_state": None if self.prior_state is None else self.prior_state.value,
            "next_state": self.next_state.value,
            "event_type": self.event_type.value,
            "actor": self.actor,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "timestamp": self.timestamp,
            "idempotency_key": self.idempotency_key,
            "causation_id": self.causation_id,
            "correlation_id": self.correlation_id,
            "payload": dict(self.payload),
        }
        self.event_hash = hash_payload(body)
        return self.event_hash

    def to_dict(self) -> dict[str, Any]:
        if not self.event_hash:
            self.compute_hash()
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "mission_id": self.mission_id,
            "prior_state": None if self.prior_state is None else self.prior_state.value,
            "next_state": self.next_state.value,
            "event_type": self.event_type.value,
            "actor": self.actor,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "timestamp": self.timestamp,
            "idempotency_key": self.idempotency_key,
            "causation_id": self.causation_id,
            "correlation_id": self.correlation_id,
            "event_hash": self.event_hash,
            "payload": dict(self.payload),
            "sequence": self.sequence,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "MissionEvent":
        prior = d.get("prior_state")
        return MissionEvent(
            schema_version=str(d["schema_version"]),
            event_id=str(d["event_id"]),
            mission_id=str(d["mission_id"]),
            prior_state=None if prior is None else MissionState(prior),
            next_state=MissionState(d["next_state"]),
            event_type=EventType(d["event_type"]),
            actor=str(d["actor"]),
            reason=str(d["reason"]),
            evidence_refs=list(d.get("evidence_refs") or []),
            timestamp=str(d["timestamp"]),
            idempotency_key=str(d["idempotency_key"]),
            causation_id=d.get("causation_id"),
            correlation_id=d.get("correlation_id"),
            event_hash=str(d.get("event_hash") or ""),
            payload=dict(d.get("payload") or {}),
            sequence=d.get("sequence"),
        )

    def verify_hash(self) -> bool:
        expected = self.event_hash
        current = self.compute_hash()
        # restore after recompute same
        return expected == current
