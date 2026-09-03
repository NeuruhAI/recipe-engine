"""Approval contract — exact, scoped, expiring, non-transitive."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from recipe_engine.founder_runtime.ids import hash_payload


SCHEMA_APPROVAL = "fr.approval.v1"


class ApprovalDecision(str, Enum):
    PENDING = "pending"
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"
    EXPIRED = "expired"
    REVOKED = "revoked"
    CONSUMED = "consumed"


@dataclass
class Approval:
    schema_version: str
    approval_id: str
    mission_id: str
    action_class: str
    exact_requested_action: str
    scope: str
    requested_by: str
    decided_by: str | None
    decision: ApprovalDecision
    conditions: list[str]
    expiry: str | None
    evidence_refs: list[str]
    approval_hash: str = ""
    created_at: str = ""
    decided_at: str | None = None
    idempotency_key: str = ""
    enhanced: bool = False
    consumed_by: str | None = None

    def expected_hash(self) -> str:
        body = {
            "schema_version": self.schema_version,
            "approval_id": self.approval_id,
            "mission_id": self.mission_id,
            "action_class": self.action_class,
            "exact_requested_action": self.exact_requested_action,
            "scope": self.scope,
            "requested_by": self.requested_by,
            "decided_by": self.decided_by,
            "decision": self.decision.value,
            "conditions": list(self.conditions),
            "expiry": self.expiry,
            "evidence_refs": list(self.evidence_refs),
            "enhanced": self.enhanced,
            "idempotency_key": self.idempotency_key,
        }
        return hash_payload(body)

    def compute_hash(self) -> str:
        self.approval_hash = self.expected_hash()
        return self.approval_hash

    def to_dict(self) -> dict[str, Any]:
        if not self.approval_hash:
            self.compute_hash()
        return {
            "schema_version": self.schema_version,
            "approval_id": self.approval_id,
            "mission_id": self.mission_id,
            "action_class": self.action_class,
            "exact_requested_action": self.exact_requested_action,
            "scope": self.scope,
            "requested_by": self.requested_by,
            "decided_by": self.decided_by,
            "decision": self.decision.value,
            "conditions": list(self.conditions),
            "expiry": self.expiry,
            "evidence_refs": list(self.evidence_refs),
            "approval_hash": self.approval_hash,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "idempotency_key": self.idempotency_key,
            "enhanced": self.enhanced,
            "consumed_by": self.consumed_by,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Approval":
        return Approval(
            schema_version=str(d["schema_version"]),
            approval_id=str(d["approval_id"]),
            mission_id=str(d["mission_id"]),
            action_class=str(d["action_class"]),
            exact_requested_action=str(d["exact_requested_action"]),
            scope=str(d["scope"]),
            requested_by=str(d["requested_by"]),
            decided_by=d.get("decided_by"),
            decision=ApprovalDecision(d["decision"]),
            conditions=list(d.get("conditions") or []),
            expiry=d.get("expiry"),
            evidence_refs=list(d.get("evidence_refs") or []),
            approval_hash=str(d.get("approval_hash") or ""),
            created_at=str(d.get("created_at") or ""),
            decided_at=d.get("decided_at"),
            idempotency_key=str(d.get("idempotency_key") or ""),
            enhanced=bool(d.get("enhanced", False)),
            consumed_by=d.get("consumed_by"),
        )
