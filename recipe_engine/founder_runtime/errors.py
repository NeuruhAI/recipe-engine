"""Explicit typed error taxonomy for Founder Runtime. Fail closed."""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    CONTEXT_NOT_FOUND = "CONTEXT_NOT_FOUND"
    CONTEXT_CONFLICT = "CONTEXT_CONFLICT"
    SOURCE_STALE = "SOURCE_STALE"
    SOURCE_UNTRUSTED = "SOURCE_UNTRUSTED"
    MISSION_SCHEMA_INVALID = "MISSION_SCHEMA_INVALID"
    STATE_TRANSITION_INVALID = "STATE_TRANSITION_INVALID"
    STRATEGY_NOT_VIABLE = "STRATEGY_NOT_VIABLE"
    AUTHORITY_DENIED = "AUTHORITY_DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    DEPENDENCY_UNHEALTHY = "DEPENDENCY_UNHEALTHY"
    EXECUTOR_UNAVAILABLE = "EXECUTOR_UNAVAILABLE"
    RECEIPT_INVALID = "RECEIPT_INVALID"
    RECEIPT_MISSING = "RECEIPT_MISSING"
    SIDE_EFFECT_UNCERTAIN = "SIDE_EFFECT_UNCERTAIN"
    CHECKPOINT_CONFLICT = "CHECKPOINT_CONFLICT"
    LOCK_STALE = "LOCK_STALE"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    COMPLETION_CRITERIA_FAILED = "COMPLETION_CRITERIA_FAILED"
    PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"
    CANONICAL_PATH_MISMATCH = "CANONICAL_PATH_MISMATCH"
    PERSISTENCE_CONFLICT = "PERSISTENCE_CONFLICT"
    ADAPTER_MODE_INVALID = "ADAPTER_MODE_INVALID"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    LOCK_HELD = "LOCK_HELD"
    MISSION_NOT_FOUND = "MISSION_NOT_FOUND"
    UNRECOVERABLE = "UNRECOVERABLE"
    # P2-A adapter foundation codes
    ADAPTER_DISABLED = "ADAPTER_DISABLED"
    ADAPTER_UNAVAILABLE = "ADAPTER_UNAVAILABLE"
    CONFIGURATION_MISSING = "CONFIGURATION_MISSING"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    TIMEOUT = "TIMEOUT"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    PROVENANCE_INVALID = "PROVENANCE_INVALID"
    UNSAFE_CAPABILITY = "UNSAFE_CAPABILITY"
    MUTATION_NOT_AUTHORIZED = "MUTATION_NOT_AUTHORIZED"
    LIVE_FALLBACK_FORBIDDEN = "LIVE_FALLBACK_FORBIDDEN"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    INTERNAL_ADAPTER_FAILURE = "INTERNAL_ADAPTER_FAILURE"
    QDRANT_COUNT_MISMATCH = "QDRANT_COUNT_MISMATCH"
    QDRANT_COUNT_CHANGED = "QDRANT_COUNT_CHANGED"
    QDRANT_COLLECTION_MISMATCH = "QDRANT_COLLECTION_MISMATCH"
    QDRANT_PAYLOAD_PROVENANCE_MISMATCH = "QDRANT_PAYLOAD_PROVENANCE_MISMATCH"
    # R02D execution-outcome write-back
    EXECUTION_OUTCOME_MALFORMED = "EXECUTION_OUTCOME_MALFORMED"
    EXECUTION_OUTCOME_REJECTED = "EXECUTION_OUTCOME_REJECTED"


class FounderRuntimeError(Exception):
    """Base error; never silently coerced to generic Exception at boundaries."""

    def __init__(self, code: ErrorCode | str, message: str, details: dict[str, Any] | None = None):
        self.code = code if isinstance(code, ErrorCode) else ErrorCode(code) if code in ErrorCode.__members__ or code in [e.value for e in ErrorCode] else code
        if isinstance(self.code, str) and self.code in ErrorCode.__members__:
            self.code = ErrorCode[self.code]
        elif isinstance(self.code, str):
            try:
                self.code = ErrorCode(self.code)
            except ValueError:
                pass
        self.message = message
        self.details = details or {}
        super().__init__(f"[{getattr(self.code, 'value', self.code)}] {message}")

    def to_dict(self) -> dict[str, Any]:
        code = self.code.value if isinstance(self.code, ErrorCode) else str(self.code)
        return {"code": code, "message": self.message, "details": dict(self.details)}


class MissionSchemaInvalid(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.MISSION_SCHEMA_INVALID, message, details)


class StateTransitionInvalid(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.STATE_TRANSITION_INVALID, message, details)


class ReceiptInvalid(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.RECEIPT_INVALID, message, details)


class ReceiptMissing(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.RECEIPT_MISSING, message, details)


class ApprovalRequired(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.APPROVAL_REQUIRED, message, details)


class ApprovalExpired(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.APPROVAL_EXPIRED, message, details)


class AuthorityDenied(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.AUTHORITY_DENIED, message, details)


class AdapterModeInvalid(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.ADAPTER_MODE_INVALID, message, details)


class PersistenceConflict(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.PERSISTENCE_CONFLICT, message, details)


class CheckpointConflict(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.CHECKPOINT_CONFLICT, message, details)


class LockStale(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.LOCK_STALE, message, details)


class LockHeld(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.LOCK_HELD, message, details)


class SideEffectUncertain(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.SIDE_EFFECT_UNCERTAIN, message, details)


class CompletionCriteriaFailed(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.COMPLETION_CRITERIA_FAILED, message, details)


class ContextNotFound(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.CONTEXT_NOT_FOUND, message, details)


class ContextConflict(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.CONTEXT_CONFLICT, message, details)


class MissionNotFound(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.MISSION_NOT_FOUND, message, details)


# ---- P2-A adapter errors ----

class AdapterDisabled(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.ADAPTER_DISABLED, message, details)


class AdapterUnavailable(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.ADAPTER_UNAVAILABLE, message, details)


class ConfigurationMissing(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.CONFIGURATION_MISSING, message, details)


class IdentityMismatch(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.IDENTITY_MISMATCH, message, details)


class VersionMismatch(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.VERSION_MISMATCH, message, details)


class ProvenanceInvalid(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.PROVENANCE_INVALID, message, details)


class MutationNotAuthorized(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.MUTATION_NOT_AUTHORIZED, message, details)


class LiveFallbackForbidden(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.LIVE_FALLBACK_FORBIDDEN, message, details)


class UnsafeCapability(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.UNSAFE_CAPABILITY, message, details)


class TimeoutError_(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.TIMEOUT, message, details)


class MalformedResponse(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.MALFORMED_RESPONSE, message, details)


class InternalAdapterFailure(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.INTERNAL_ADAPTER_FAILURE, message, details)


class QdrantCountMismatch(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.QDRANT_COUNT_MISMATCH, message, details)


class QdrantCountChanged(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.QDRANT_COUNT_CHANGED, message, details)


class QdrantCollectionMismatch(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.QDRANT_COLLECTION_MISMATCH, message, details)


class QdrantPayloadProvenanceMismatch(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.QDRANT_PAYLOAD_PROVENANCE_MISMATCH, message, details)


class ExecutionOutcomeMalformed(FounderRuntimeError):
    """The write-back envelope is outside the exact fr.execution_outcome.v1 shape (HTTP 400)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.EXECUTION_OUTCOME_MALFORMED, message, details)


class ExecutionOutcomeRejected(FounderRuntimeError):
    """A well-formed envelope that does not bind to this mission's evidence (HTTP 409).

    ``details["reason_code"]`` names the precise refusal (e.g. OUTCOME_STALE,
    RECEIPT_HASH_MISMATCH); callers surface it verbatim so an adversarial test
    can assert exactly why the runtime refused.
    """

    def __init__(self, reason_code: str, message: str, details: dict[str, Any] | None = None):
        merged = {"reason_code": reason_code, **(details or {})}
        super().__init__(ErrorCode.EXECUTION_OUTCOME_REJECTED, message, merged)
        self.reason_code = reason_code
