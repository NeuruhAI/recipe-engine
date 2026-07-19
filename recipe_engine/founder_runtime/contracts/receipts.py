"""ExecutionReceipt and related evidence structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from recipe_engine.founder_runtime.ids import hash_payload


SCHEMA_RECEIPT = "fr.receipt.v1"


class ReceiptStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    RECONCILED = "reconciled"


@dataclass(frozen=True)
class TestResult:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "TestResult":
        return TestResult(name=str(d["name"]), passed=bool(d["passed"]), detail=str(d.get("detail") or ""))


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    kind: str
    content_hash: str
    pointer: str  # opaque fixture pointer only

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "content_hash": self.content_hash,
            "pointer": self.pointer,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "ArtifactRef":
        return ArtifactRef(
            artifact_id=str(d["artifact_id"]),
            kind=str(d["kind"]),
            content_hash=str(d["content_hash"]),
            pointer=str(d["pointer"]),
        )


@dataclass
class ExecutionReceipt:
    schema_version: str
    receipt_id: str
    mission_id: str
    plan_id: str
    step_id: str
    executor: str
    capability: str
    started_at: str
    ended_at: str
    status: ReceiptStatus
    input_hash: str
    output_hash: str
    repository_metadata: dict[str, Any]
    files_changed: list[str]
    tests: list[TestResult]
    artifacts: list[ArtifactRef]
    error: str | None
    authority_reference: str
    idempotency_key: str
    receipt_hash: str = ""
    side_effect_key: str | None = None

    def compute_hash(self) -> str:
        body = {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "mission_id": self.mission_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "executor": self.executor,
            "capability": self.capability,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status.value,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "repository_metadata": dict(self.repository_metadata),
            "files_changed": list(self.files_changed),
            "tests": [t.to_dict() for t in self.tests],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "error": self.error,
            "authority_reference": self.authority_reference,
            "idempotency_key": self.idempotency_key,
            "side_effect_key": self.side_effect_key,
        }
        self.receipt_hash = hash_payload(body)
        return self.receipt_hash

    def to_dict(self) -> dict[str, Any]:
        if not self.receipt_hash:
            self.compute_hash()
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "mission_id": self.mission_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "executor": self.executor,
            "capability": self.capability,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status.value,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "repository_metadata": dict(self.repository_metadata),
            "files_changed": list(self.files_changed),
            "tests": [t.to_dict() for t in self.tests],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "error": self.error,
            "authority_reference": self.authority_reference,
            "idempotency_key": self.idempotency_key,
            "receipt_hash": self.receipt_hash,
            "side_effect_key": self.side_effect_key,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "ExecutionReceipt":
        return ExecutionReceipt(
            schema_version=str(d["schema_version"]),
            receipt_id=str(d["receipt_id"]),
            mission_id=str(d["mission_id"]),
            plan_id=str(d["plan_id"]),
            step_id=str(d["step_id"]),
            executor=str(d["executor"]),
            capability=str(d["capability"]),
            started_at=str(d["started_at"]),
            ended_at=str(d["ended_at"]),
            status=ReceiptStatus(d["status"]),
            input_hash=str(d["input_hash"]),
            output_hash=str(d["output_hash"]),
            repository_metadata=dict(d.get("repository_metadata") or {}),
            files_changed=list(d.get("files_changed") or []),
            tests=[TestResult.from_dict(t) for t in d.get("tests") or []],
            artifacts=[ArtifactRef.from_dict(a) for a in d.get("artifacts") or []],
            error=d.get("error"),
            authority_reference=str(d.get("authority_reference") or ""),
            idempotency_key=str(d["idempotency_key"]),
            receipt_hash=str(d.get("receipt_hash") or ""),
            side_effect_key=d.get("side_effect_key"),
        )
