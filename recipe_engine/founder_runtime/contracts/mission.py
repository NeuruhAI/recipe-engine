"""Mission, Principal, Deliverable contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.state.states import MissionState
from .authority import AuthorityRequirement, AuthorityClass


SCHEMA_MISSION = "fr.mission.v1"
SCHEMA_PRINCIPAL = "fr.principal.v1"
SCHEMA_DELIVERABLE = "fr.deliverable.v1"


class MissionClass(str, Enum):
    SYSTEM_STATUS = "system_status"
    ANALYSIS = "analysis"
    PLANNING = "planning"
    EXECUTION = "execution"
    RECOVERY = "recovery"
    GENERAL = "general"


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class RiskClass(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AutonomyLevel(str, Enum):
    L0_HUMAN = "L0_human"
    L1_ASSISTED = "L1_assisted"
    L2_GUARDED = "L2_guarded"
    L3_BOUNDED = "L3_bounded"


class DeliverableStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    FAILED = "failed"
    WAIVED = "waived"


@dataclass(frozen=True)
class Principal:
    schema_version: str
    principal_id: str
    principal_type: str
    interface: str
    authority_profile_id: str
    context_profile_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "principal_id": self.principal_id,
            "principal_type": self.principal_type,
            "interface": self.interface,
            "authority_profile_id": self.authority_profile_id,
            "context_profile_id": self.context_profile_id,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Principal":
        return Principal(
            schema_version=str(d["schema_version"]),
            principal_id=str(d["principal_id"]),
            principal_type=str(d["principal_type"]),
            interface=str(d["interface"]),
            authority_profile_id=str(d["authority_profile_id"]),
            context_profile_id=str(d["context_profile_id"]),
        )

    @staticmethod
    def jeramie_fixture() -> "Principal":
        return Principal(
            schema_version=SCHEMA_PRINCIPAL,
            principal_id="jeramie",
            principal_type="founder",
            interface="cli",
            authority_profile_id="founder_default",
            context_profile_id="founder_core",
        )


@dataclass(frozen=True)
class Deliverable:
    schema_version: str
    deliverable_id: str
    description: str
    required: bool
    acceptance_criteria: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    status: DeliverableStatus = DeliverableStatus.PENDING

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "deliverable_id": self.deliverable_id,
            "description": self.description,
            "required": self.required,
            "acceptance_criteria": list(self.acceptance_criteria),
            "evidence_requirements": list(self.evidence_requirements),
            "status": self.status.value,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Deliverable":
        return Deliverable(
            schema_version=str(d["schema_version"]),
            deliverable_id=str(d["deliverable_id"]),
            description=str(d["description"]),
            required=bool(d["required"]),
            acceptance_criteria=tuple(d.get("acceptance_criteria") or ()),
            evidence_requirements=tuple(d.get("evidence_requirements") or ()),
            status=DeliverableStatus(d.get("status", "pending")),
        )


@dataclass
class Mission:
    schema_version: str
    mission_id: str
    version: int
    principal: Principal
    title: str
    objective: str
    mission_class: MissionClass
    priority: Priority
    risk_class: RiskClass
    autonomy_level: AutonomyLevel
    current_state: MissionState
    desired_outcome: str
    deliverables: list[Deliverable]
    constraints: list[str]
    authority_requirements: list[AuthorityRequirement]
    context_requirements: list[str]
    verification_criteria: list[str]
    completion_rule: str
    created_at: str
    updated_at: str
    completed_at: str | None = None
    superseded_by: str | None = None
    input_hash: str = ""
    creation_idempotency_key: str = ""
    plan_id: str | None = None
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mission_id": self.mission_id,
            "version": self.version,
            "principal": self.principal.to_dict(),
            "title": self.title,
            "objective": self.objective,
            "mission_class": self.mission_class.value,
            "priority": self.priority.value,
            "risk_class": self.risk_class.value,
            "autonomy_level": self.autonomy_level.value,
            "current_state": self.current_state.value,
            "desired_outcome": self.desired_outcome,
            "deliverables": [d.to_dict() for d in self.deliverables],
            "constraints": list(self.constraints),
            "authority_requirements": [a.to_dict() for a in self.authority_requirements],
            "context_requirements": list(self.context_requirements),
            "verification_criteria": list(self.verification_criteria),
            "completion_rule": self.completion_rule,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "superseded_by": self.superseded_by,
            "input_hash": self.input_hash,
            "creation_idempotency_key": self.creation_idempotency_key,
            "plan_id": self.plan_id,
            "extensions": dict(self.extensions),
        }

    def recompute_input_hash(self) -> str:
        payload = {
            "principal": self.principal.to_dict(),
            "title": self.title,
            "objective": self.objective,
            "mission_class": self.mission_class.value,
            "desired_outcome": self.desired_outcome,
            "constraints": list(self.constraints),
            "completion_rule": self.completion_rule,
        }
        self.input_hash = hash_payload(payload)
        return self.input_hash

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Mission":
        return Mission(
            schema_version=str(d["schema_version"]),
            mission_id=str(d["mission_id"]),
            version=int(d["version"]),
            principal=Principal.from_dict(d["principal"]),
            title=str(d["title"]),
            objective=str(d["objective"]),
            mission_class=MissionClass(d["mission_class"]),
            priority=Priority(d["priority"]),
            risk_class=RiskClass(d["risk_class"]),
            autonomy_level=AutonomyLevel(d["autonomy_level"]),
            current_state=MissionState(d["current_state"]),
            desired_outcome=str(d["desired_outcome"]),
            deliverables=[Deliverable.from_dict(x) for x in d.get("deliverables") or []],
            constraints=list(d.get("constraints") or []),
            authority_requirements=[AuthorityRequirement.from_dict(x) for x in d.get("authority_requirements") or []],
            context_requirements=list(d.get("context_requirements") or []),
            verification_criteria=list(d.get("verification_criteria") or []),
            completion_rule=str(d["completion_rule"]),
            created_at=str(d["created_at"]),
            updated_at=str(d["updated_at"]),
            completed_at=d.get("completed_at"),
            superseded_by=d.get("superseded_by"),
            input_hash=str(d.get("input_hash") or ""),
            creation_idempotency_key=str(d.get("creation_idempotency_key") or ""),
            plan_id=d.get("plan_id"),
            extensions=dict(d.get("extensions") or {}),
        )


VALID_AUTONOMY = {a.value for a in AutonomyLevel}
VALID_AUTHORITY = {a.value for a in AuthorityClass}
VALID_COMPLETION_RULES = {
    "all_required_deliverables",
    "all_required_with_exceptions_disclosed",
    "manual_operator_signoff",
}


def validate_mission_dict(d: Mapping[str, Any]) -> list[str]:
    """Return list of schema errors; empty means valid."""
    errs: list[str] = []
    if not d.get("schema_version"):
        errs.append("missing schema_version")
    elif not str(d["schema_version"]).startswith("fr.mission"):
        errs.append(f"invalid schema_version: {d['schema_version']}")
    if not d.get("mission_id"):
        errs.append("missing mission_id")
    principal = d.get("principal")
    if not principal or not isinstance(principal, Mapping):
        errs.append("missing principal")
    elif not principal.get("principal_id"):
        errs.append("missing principal.principal_id")
    al = d.get("autonomy_level")
    if al not in VALID_AUTONOMY:
        errs.append(f"invalid autonomy_level: {al}")
    cr = d.get("completion_rule")
    if cr not in VALID_COMPLETION_RULES:
        errs.append(f"invalid completion_rule: {cr}")
    for ar in d.get("authority_requirements") or []:
        ac = ar.get("authority_class") if isinstance(ar, Mapping) else None
        if ac not in VALID_AUTHORITY:
            errs.append(f"invalid authority_class: {ac}")
    return errs
