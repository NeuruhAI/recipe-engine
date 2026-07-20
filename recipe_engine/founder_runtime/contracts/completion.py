"""CompletionReport contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.state.states import MissionState


SCHEMA_COMPLETION = "fr.completion.v1"


@dataclass(frozen=True)
class AcceptanceResult:
    criterion: str
    passed: bool
    evidence_ref: str | None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "passed": self.passed,
            "evidence_ref": self.evidence_ref,
            "detail": self.detail,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "AcceptanceResult":
        return AcceptanceResult(
            criterion=str(d["criterion"]),
            passed=bool(d["passed"]),
            evidence_ref=d.get("evidence_ref"),
            detail=str(d.get("detail") or ""),
        )


@dataclass
class CompletionReport:
    schema_version: str
    mission_id: str
    objective: str
    final_state: MissionState
    delivered_artifacts: list[str]
    acceptance_criteria_results: list[AcceptanceResult]
    evidence_refs: list[str]
    code_changes: list[str]
    operational_changes: list[str]
    approvals_used: list[str]
    unresolved_items: list[str]
    remaining_risks: list[str]
    recommended_follow_on: list[str]
    proposed_memory_updates: list[str]
    completed_at: str
    completion_hash: str = ""
    exceptions: list[str] = field(default_factory=list)
    decision_packet_id: str | None = None
    plan_id: str | None = None
    idempotency_key: str = ""
    outcome_ref: str | None = None
    authority_refs: list[str] = field(default_factory=list)

    def hash_material(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mission_id": self.mission_id,
            "objective": self.objective,
            "final_state": self.final_state.value,
            "delivered_artifacts": list(self.delivered_artifacts),
            "acceptance_criteria_results": [a.to_dict() for a in self.acceptance_criteria_results],
            "evidence_refs": list(self.evidence_refs),
            "code_changes": list(self.code_changes),
            "operational_changes": list(self.operational_changes),
            "approvals_used": list(self.approvals_used),
            "unresolved_items": list(self.unresolved_items),
            "remaining_risks": list(self.remaining_risks),
            "recommended_follow_on": list(self.recommended_follow_on),
            "proposed_memory_updates": list(self.proposed_memory_updates),
            "completed_at": self.completed_at,
            "exceptions": list(self.exceptions),
            "decision_packet_id": self.decision_packet_id,
            "plan_id": self.plan_id,
            "idempotency_key": self.idempotency_key,
            "outcome_ref": self.outcome_ref,
            "authority_refs": list(self.authority_refs),
        }

    def computed_hash(self) -> str:
        return hash_payload(self.hash_material())

    def compute_hash(self) -> str:
        self.completion_hash = self.computed_hash()
        return self.completion_hash

    def to_dict(self) -> dict[str, Any]:
        return {**self.hash_material(), "completion_hash": self.completion_hash}

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "CompletionReport":
        return CompletionReport(
            schema_version=str(d["schema_version"]),
            mission_id=str(d["mission_id"]),
            objective=str(d["objective"]),
            final_state=MissionState(d["final_state"]),
            delivered_artifacts=list(d.get("delivered_artifacts") or []),
            acceptance_criteria_results=[AcceptanceResult.from_dict(x) for x in d.get("acceptance_criteria_results") or []],
            evidence_refs=list(d.get("evidence_refs") or []),
            code_changes=list(d.get("code_changes") or []),
            operational_changes=list(d.get("operational_changes") or []),
            approvals_used=list(d.get("approvals_used") or []),
            unresolved_items=list(d.get("unresolved_items") or []),
            remaining_risks=list(d.get("remaining_risks") or []),
            recommended_follow_on=list(d.get("recommended_follow_on") or []),
            proposed_memory_updates=list(d.get("proposed_memory_updates") or []),
            completed_at=str(d["completed_at"]),
            completion_hash=str(d.get("completion_hash") or ""),
            exceptions=list(d.get("exceptions") or []),
            decision_packet_id=d.get("decision_packet_id"),
            plan_id=d.get("plan_id"),
            idempotency_key=str(d.get("idempotency_key") or ""),
            outcome_ref=d.get("outcome_ref"),
            authority_refs=list(d.get("authority_refs") or []),
        )
