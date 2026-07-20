"""Canonical adapter result envelope."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from recipe_engine.founder_runtime.adapters.base import (
    AdapterMode,
    OperationalWriteRef,
    WriteClass,
    reject_fallback,
)
from recipe_engine.founder_runtime.adapters.timeouts import resolve_operation_policy
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.errors import (
    ConfigurationMissing,
    LiveFallbackForbidden,
    MalformedResponse,
    UnsafeCapability,
)


SCHEMA_ADAPTER_RESULT_V1 = "fr.adapter_result.v1"
SCHEMA_ADAPTER_RESULT = "fr.adapter_result.v1.1"


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
    declared_timeout_ms: int | None = None
    effective_timeout_ms: int | None = None
    retry_policy: dict[str, Any] | None = None
    write_class: str | None = None
    upstream_writes: list[dict[str, Any]] = field(default_factory=list)

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
        if self.schema_version not in (SCHEMA_ADAPTER_RESULT_V1, SCHEMA_ADAPTER_RESULT):
            raise MalformedResponse(
                "unsupported adapter result schema",
                {"schema_version": self.schema_version},
            )
        if self.schema_version == SCHEMA_ADAPTER_RESULT_V1 and self.adapter_mode == AdapterMode.LIVE.value:
            raise MalformedResponse("legacy v1 live evidence has ambiguous security fields", {})
        if self.schema_version == SCHEMA_ADAPTER_RESULT and self.adapter_mode == AdapterMode.LIVE.value:
            if self.declared_timeout_ms is None or self.effective_timeout_ms is None:
                raise MalformedResponse(
                    "live v1.1 result lacks explicit timeout policy",
                    {"adapter": self.adapter_name, "operation": self.operation},
                )
            resolved = resolve_operation_policy(
                self.adapter_name,
                self.operation,
                self.declared_timeout_ms,
            )
            if self.effective_timeout_ms != resolved.effective_timeout_ms:
                raise MalformedResponse("live result timeout does not match canonical policy", {})
            if self.retry_policy != resolved.retry_dict():
                raise MalformedResponse("live result retry policy does not match canonical policy", {})
        if self.declared_timeout_ms is not None:
            if (
                isinstance(self.declared_timeout_ms, bool)
                or not isinstance(self.declared_timeout_ms, int)
                or self.declared_timeout_ms <= 0
            ):
                raise MalformedResponse("invalid declared timeout", {})
        if self.effective_timeout_ms is not None:
            if (
                isinstance(self.effective_timeout_ms, bool)
                or not isinstance(self.effective_timeout_ms, int)
                or self.effective_timeout_ms <= 0
            ):
                raise MalformedResponse("invalid effective timeout", {})
        if (
            self.declared_timeout_ms is not None
            and self.effective_timeout_ms is not None
            and self.effective_timeout_ms > self.declared_timeout_ms
        ):
            raise MalformedResponse("effective timeout exceeds declared timeout", {})
        _validate_write_declaration(
            adapter_mode=self.adapter_mode,
            write_class=self.write_class,
            upstream_writes=self.upstream_writes,
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
            "declared_timeout_ms": self.declared_timeout_ms,
            "effective_timeout_ms": self.effective_timeout_ms,
            "retry_policy": deepcopy(self.retry_policy),
            "write_class": self.write_class,
            "upstream_writes": deepcopy(self.upstream_writes),
        }

    def recompute_payload_hash(self) -> str:
        """Return the payload hash without mutating evidence.

        Kept for source compatibility with P2-A emitters.  Validators must use
        :func:`validate_payload_hash` and must never backfill this field.
        """
        return hash_payload(deepcopy(self.payload))


def _normalize_write_refs(
    values: list[OperationalWriteRef | Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for value in values or []:
        ref = value if isinstance(value, OperationalWriteRef) else OperationalWriteRef.from_mapping(value)
        refs.append(ref.to_dict())
    return refs


def _validate_write_declaration(
    *,
    adapter_mode: str,
    write_class: str | WriteClass | None,
    upstream_writes: list[Mapping[str, Any]],
) -> None:
    normalized: WriteClass | None
    if write_class is None:
        normalized = None
    else:
        try:
            normalized = write_class if isinstance(write_class, WriteClass) else WriteClass(str(write_class))
        except ValueError as exc:
            raise UnsafeCapability("unknown write classification", {"write_class": str(write_class)}) from exc

    refs = [OperationalWriteRef.from_mapping(ref) for ref in upstream_writes]
    if normalized is None and refs:
        raise UnsafeCapability("upstream writes require an explicit write classification", {})
    if normalized == WriteClass.OPERATIONAL_METADATA and not refs:
        raise UnsafeCapability("operational metadata classification requires bounded evidence refs", {})
    if normalized not in (None, WriteClass.OPERATIONAL_METADATA) and refs:
        raise UnsafeCapability("upstream write references may only describe operational metadata", {})
    if normalized == WriteClass.MISSION_TRUTH:
        raise UnsafeCapability("read adapter results cannot author mission-truth writes", {})
    if normalized in (WriteClass.EXTERNAL_MUTATION, WriteClass.PROHIBITED):
        raise UnsafeCapability("P2-A.1 forbids external or prohibited writes", {"write_class": normalized.value})
    if adapter_mode == AdapterMode.FIXTURE.value and (normalized is not None or refs):
        raise LiveFallbackForbidden("fixture evidence cannot claim upstream writes", {})


def validate_payload_hash(result: AdapterResult | Mapping[str, Any]) -> bool:
    """Pure validation: compare a defensive-copy hash without modifying evidence."""
    if isinstance(result, AdapterResult):
        stored = result.payload_hash
        payload = deepcopy(result.payload)
    else:
        snapshot = deepcopy(dict(result))
        stored = snapshot.get("payload_hash")
        payload = snapshot.get("payload")
    if not isinstance(stored, str) or not stored:
        return False
    if not isinstance(payload, Mapping):
        return False
    return hash_payload(deepcopy(dict(payload))) == stored


_V1_FIELDS = (
    "schema_version", "adapter_name", "adapter_version", "adapter_mode", "operation",
    "status", "observed_at", "duration_ms", "correlation_id", "causation_id",
    "mission_id", "plan_id", "step_id", "authority_class", "mutation_attempted",
    "dependency_identity", "source_identity", "source_version_or_sha", "provenance",
    "evidence_refs", "payload_hash", "error_code", "retryable", "fallback_used", "payload",
)
_V11_FIELDS = _V1_FIELDS + (
    "declared_timeout_ms", "effective_timeout_ms", "retry_policy", "write_class",
    "upstream_writes",
)


def parse_adapter_result(
    raw: Mapping[str, Any],
    *,
    allow_v1_compatibility: bool = False,
) -> AdapterResult:
    """Parse a result without inventing security claims.

    V1 parsing is intentionally opt-in.  Missing v1.1 security fields are not
    defaulted; the whole exact additive shape is required.
    """
    data = deepcopy(dict(raw))
    schema = data.get("schema_version")
    if schema == SCHEMA_ADAPTER_RESULT_V1:
        if not allow_v1_compatibility:
            raise MalformedResponse("v1 compatibility was not explicitly authorized", {})
        if set(data) != set(_V1_FIELDS):
            raise MalformedResponse("v1 result has an invalid field set", {})
        data.update(
            {
                "declared_timeout_ms": None,
                "effective_timeout_ms": None,
                "retry_policy": None,
                "write_class": None,
                "upstream_writes": [],
            }
        )
    elif schema == SCHEMA_ADAPTER_RESULT:
        if set(data) != set(_V11_FIELDS):
            raise MalformedResponse("v1.1 result has an invalid field set", {})
    else:
        raise MalformedResponse("unsupported adapter result schema", {"schema_version": schema})
    result = AdapterResult(**data)
    if not validate_payload_hash(result):
        raise MalformedResponse("adapter result payload hash is missing or mismatched", {})
    return result


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
    declared_timeout_ms: int | None = None,
    effective_timeout_ms: int | None = None,
    retry_policy: dict[str, Any] | None = None,
    write_class: WriteClass | str | None = None,
    upstream_writes: list[OperationalWriteRef | Mapping[str, Any]] | None = None,
) -> AdapterResult:
    reject_fallback(fallback_used, context=f"{adapter_name}:{operation}")
    mode = adapter_mode.value if isinstance(adapter_mode, AdapterMode) else str(adapter_mode)
    st = status.value if isinstance(status, AdapterResultStatus) else str(status)
    pl = dict(payload or {})
    resolved = None
    if effective_timeout_ms is None or retry_policy is None:
        try:
            resolved = resolve_operation_policy(adapter_name, operation, declared_timeout_ms)
        except ConfigurationMissing:
            # Legacy/fixture-only operations may be nullable.  Live v1.1
            # results are rejected by AdapterResult.__post_init__.
            resolved = None
    if resolved is not None:
        declared_timeout_ms = resolved.declared_timeout_ms
        effective_timeout_ms = resolved.effective_timeout_ms
        retry_policy = resolved.retry_dict()
    refs = _normalize_write_refs(upstream_writes)
    write_value = write_class.value if isinstance(write_class, WriteClass) else write_class
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
        declared_timeout_ms=declared_timeout_ms,
        effective_timeout_ms=effective_timeout_ms,
        retry_policy=deepcopy(retry_policy),
        write_class=write_value,
        upstream_writes=refs,
    )
