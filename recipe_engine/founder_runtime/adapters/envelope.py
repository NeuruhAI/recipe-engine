"""Canonical adapter result envelope."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from recipe_engine.founder_runtime.adapters.base import AdapterMode, reject_fallback
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.errors import LiveFallbackForbidden, MalformedResponse


SCHEMA_ADAPTER_RESULT = "fr.adapter_result.v1"


class AdapterResultStatus(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    UNSAFE = "unsafe"
    FAILED = "failed"


ALLOWED_STATUSES = frozenset(s.value for s in AdapterResultStatus)


@dataclass
class AdapterResult:
    """Standard result envelope for all adapters."""

    schema_version: str
    adapter_name: str
    adapter_version: str
    adapter_mode: str
    operation: str
    status: str
    observed_at: str
    duration_ms: int
    correlation_id: str
    causation_id: str | None
    mission_id: str | None
    plan_id: str | None
    step_id: str | None
    authority_class: str
    mutation_attempted: bool
    dependency_identity: dict[str, Any]
    source_identity: dict[str, Any]
    source_version_or_sha: str
    provenance: dict[str, Any]
    evidence_refs: list[str]
    payload_hash: str
    error_code: str | None
    retryable: bool
    fallback_used: bool
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.fallback_used:
            raise LiveFallbackForbidden(
                "fallback_used must be false in P2-A",
                {"adapter": self.adapter_name, "operation": self.operation},
            )
        if self.status not in ALLOWED_STATUSES:
            raise MalformedResponse(
                "invalid adapter result status",
                {"status": self.status, "allowed": sorted(ALLOWED_STATUSES)},
            )
        if self.adapter_mode == AdapterMode.FIXTURE.value and self.mutation_attempted:
            # fixtures must not attempt mutation
            raise LiveFallbackForbidden(
                "fixture adapter cannot attempt mutation",
                {"adapter": self.adapter_name},
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
            "adapter_mode": self.adapter_mode,
            "operation": self.operation,
            "status": self.status,
            "observed_at": self.observed_at,
            "duration_ms": self.duration_ms,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "mission_id": self.mission_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "authority_class": self.authority_class,
            "mutation_attempted": self.mutation_attempted,
            "dependency_identity": dict(self.dependency_identity),
            "source_identity": dict(self.source_identity),
            "source_version_or_sha": self.source_version_or_sha,
            "provenance": dict(self.provenance),
            "evidence_refs": list(self.evidence_refs),
            "payload_hash": self.payload_hash,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "fallback_used": self.fallback_used,
            "payload": dict(self.payload),
        }

    def recompute_payload_hash(self) -> str:
        self.payload_hash = hash_payload(self.payload)
        return self.payload_hash


def make_result(
    *,
    adapter_name: str,
    adapter_version: str,
    adapter_mode: str | AdapterMode,
    operation: str,
    status: str | AdapterResultStatus,
    observed_at: str,
    correlation_id: str,
    authority_class: str,
    dependency_identity: dict[str, Any],
    source_identity: dict[str, Any],
    source_version_or_sha: str,
    provenance: dict[str, Any],
    payload: dict[str, Any] | None = None,
    duration_ms: int = 0,
    causation_id: str | None = None,
    mission_id: str | None = None,
    plan_id: str | None = None,
    step_id: str | None = None,
    mutation_attempted: bool = False,
    evidence_refs: list[str] | None = None,
    error_code: str | None = None,
    retryable: bool = False,
    fallback_used: bool = False,
) -> AdapterResult:
    reject_fallback(fallback_used, context=f"{adapter_name}:{operation}")
    mode = adapter_mode.value if isinstance(adapter_mode, AdapterMode) else str(adapter_mode)
    st = status.value if isinstance(status, AdapterResultStatus) else str(status)
    pl = dict(payload or {})
    return AdapterResult(
        schema_version=SCHEMA_ADAPTER_RESULT,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        adapter_mode=mode,
        operation=operation,
        status=st,
        observed_at=observed_at,
        duration_ms=duration_ms,
        correlation_id=correlation_id,
        causation_id=causation_id,
        mission_id=mission_id,
        plan_id=plan_id,
        step_id=step_id,
        authority_class=authority_class,
        mutation_attempted=mutation_attempted,
        dependency_identity=dict(dependency_identity),
        source_identity=dict(source_identity),
        source_version_or_sha=source_version_or_sha,
        provenance=dict(provenance),
        evidence_refs=list(evidence_refs or []),
        payload_hash=hash_payload(pl),
        error_code=error_code,
        retryable=retryable,
        fallback_used=False,
        payload=pl,
    )
