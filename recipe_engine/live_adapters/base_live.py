"""Shared scaffolding for live read-only adapters.

Provides the LIVE analogue of ``fixture_mixin.envelope_for_fixture``: identity in
LIVE mode, dependency identity keyed by a *logical* endpoint class, canonical
request/response hashing, a uniform mapping from transport failures to the
AdapterResult status vocabulary, and a mutation-shaped-input rejector.  Adapters
build on this so every one of them fails closed the same way.
"""

from __future__ import annotations

from typing import Any, Mapping

from recipe_engine.founder_runtime.adapters.base import AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, AdapterResultStatus, make_result
from recipe_engine.founder_runtime.adapters.identity import (
    AdapterIdentity,
    DependencyIdentity,
    EvidenceClass,
    SCHEMA_ADAPTER_IDENTITY,
    SCHEMA_DEPENDENCY_IDENTITY,
    SCHEMA_PROVENANCE,
    DivergenceState,
)
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, RetryPolicyDeclaration
from recipe_engine.founder_runtime.adapters.timeouts import OPERATION_POLICIES
from recipe_engine.founder_runtime.errors import (
    FounderRuntimeError,
    MutationNotAuthorized,
    UnsafeCapability,
)
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.live_adapters import LIVE_POLICY_VERSION
from recipe_engine.live_adapters.redaction import redact_text
from recipe_engine.live_adapters.settings import LiveAdapterSettings, LiveConfigError
from recipe_engine.live_adapters.transport import (
    TransportDenied,
    TransportError,
    TransportMalformed,
    TransportTimeout,
    TransportUnavailable,
)

# Keys that, if present in a caller-supplied request/args mapping, indicate a
# mutation-shaped input.  Any of these => the adapter rejects DENIED/UNSAFE and
# performs no I/O.  Read-only adapters observe; they never accept a write intent.
MUTATION_SHAPED_KEYS = frozenset(
    {
        "write", "writes", "mutate", "mutation", "upsert", "insert", "update",
        "delete", "remove", "drop", "create", "put", "post_body", "patch",
        "commit", "push", "merge", "rebase", "reset", "checkout", "content",
        "payload_write", "set", "snapshot_delete", "force", "approval",
        "approved", "authorize_mutation", "execute_mutation",
    }
)

MUTATION_SHAPED_VALUES = frozenset(
    {"write", "mutate", "delete", "upsert", "insert", "update", "drop", "create"}
)


class MutationShapedInput(FounderRuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("MUTATION_NOT_AUTHORIZED", message, details)


def reject_mutation_shaped(data: Mapping[str, Any] | None, *, context: str) -> None:
    """Fail closed on any mutation-shaped caller input BEFORE doing any I/O."""
    if not data:
        return
    if not isinstance(data, Mapping):
        raise MutationShapedInput("read-only adapter requires a mapping input", {"context": context})
    lowered = {str(k).lower() for k in data.keys()}
    bad = lowered & MUTATION_SHAPED_KEYS
    if bad:
        raise MutationShapedInput(
            "mutation-shaped input rejected by read-only adapter",
            {"context": context, "keys": sorted(bad)},
        )
    for value in data.values():
        if isinstance(value, str) and value.strip().lower() in MUTATION_SHAPED_VALUES:
            raise MutationShapedInput(
                "mutation-shaped intent value rejected",
                {"context": context, "value": redact_text(value)[:64]},
            )


class LiveAdapterBase:
    """Common behavior for the P2-B live read-only adapters."""

    name: str = "live"
    adapter_version: str = "p2b.live.1"
    service_name: str = "live"
    endpoint_class: str = "loopback"
    capabilities: tuple[str, ...] = ()

    def __init__(self, settings: LiveAdapterSettings):
        if not settings.allow_live:
            raise UnsafeCapability("live adapter constructed without allow_live", {"adapter": self.name})
        self.settings = settings

    # --- identity ---------------------------------------------------------
    def mode(self) -> AdapterMode:
        return AdapterMode.LIVE

    def mutation_class(self) -> MutationClass:
        return MutationClass.READ_ONLY

    def retry_policy(self) -> RetryPolicyDeclaration:
        return RetryPolicyDeclaration(max_attempts=1)

    def _source_sha(self) -> str:
        return self.settings.recipe_engine_sha or "live-unpinned"

    def adapter_identity(self) -> AdapterIdentity:
        return AdapterIdentity(
            schema_version=SCHEMA_ADAPTER_IDENTITY,
            canonical_name=self.name,
            owner_repository="NeuruhAI/recipe-engine",
            implementation_version=self.adapter_version,
            source_sha=self._source_sha(),
            mode=AdapterMode.LIVE,
            capability_allowlist=self.capabilities,
            mutation_capability=False,
            policy_version=LIVE_POLICY_VERSION,
        )

    def dependency_identity(
        self,
        *,
        expected: str = "",
        observed: str = "",
        identity_verified: bool = False,
        readiness_verified: bool = False,
    ) -> DependencyIdentity:
        return DependencyIdentity(
            schema_version=SCHEMA_DEPENDENCY_IDENTITY,
            service_name=self.service_name,
            endpoint_class=self.endpoint_class,  # logical class ONLY, never a URL
            expected_version_or_sha=expected,
            observed_version_or_sha=observed,
            identity_verified=identity_verified,
            readiness_verified=readiness_verified,
        )

    # --- timeouts ---------------------------------------------------------
    def op_timeout(self, operation: str, meta: AdapterRequestMeta | None) -> int:
        """Effective, ceiling-clamped timeout (ms) for one live operation.

        A caller ``meta.timeout_ms`` override is honored only within the canonical
        policy ceiling; anything larger clamps DOWN to the ceiling (fail closed —
        an adapter never runs longer than its declared bound).  Used for BOTH the
        transport call and the declared/effective timeout on the envelope so the
        two can never disagree.
        """
        policy = OPERATION_POLICIES.get((self.name, operation))
        if policy is None:
            return meta.timeout_ms if (meta and meta.timeout_ms) else 5_000
        requested = meta.timeout_ms if (meta and meta.timeout_ms) else policy.default_timeout_ms
        if isinstance(requested, bool) or not isinstance(requested, int) or requested <= 0:
            requested = policy.default_timeout_ms
        return min(requested, policy.ceiling_timeout_ms)

    # --- hashing ----------------------------------------------------------
    @staticmethod
    def request_hash(obj: Any) -> str:
        return hash_payload(obj)

    @staticmethod
    def response_hash(obj: Any) -> str:
        return hash_payload(obj)

    # --- provenance -------------------------------------------------------
    def live_provenance(
        self,
        *,
        logical: str,
        observed_at: str,
        observed_sha: str = "live",
        evidence_class: EvidenceClass = EvidenceClass.LIVE_OBSERVED,
        divergence: DivergenceState = DivergenceState.NOT_APPLICABLE,
    ) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_PROVENANCE,
            "source_path_or_logical": logical,
            "source_sha_or_version": observed_sha,
            "runtime_sha_or_version": observed_sha,
            "divergence_state": divergence.value,
            "retrieval_timestamp": observed_at,
            "allowlist_policy_version": LIVE_POLICY_VERSION,
            "evidence_class": evidence_class.value,
            "source_dirty": None,
            "runtime_dirty_count": None,
            "ahead": None,
            "behind": None,
            "ancestry": None,
            "manifest_allowlist_version": None,
        }

    # --- envelope ---------------------------------------------------------
    def live_result(
        self,
        *,
        operation: str,
        meta: AdapterRequestMeta,
        status: AdapterResultStatus,
        payload: dict[str, Any],
        dependency_identity: dict[str, Any],
        provenance: dict[str, Any],
        observed_sha: str,
        evidence_refs: list[str] | None = None,
        error_code: str | None = None,
        retryable: bool = False,
        duration_ms: int = 0,
        upstream_writes: list[Any] | None = None,
        write_class: Any | None = None,
    ) -> AdapterResult:
        observed = meta.observed_at or "1970-01-01T00:00:00.000Z"
        return make_result(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            adapter_mode=AdapterMode.LIVE,
            operation=operation,
            status=status,
            observed_at=observed,
            correlation_id=meta.correlation_id,
            authority_class=meta.authority_value(),
            dependency_identity=dependency_identity,
            source_identity=self.adapter_identity().to_dict(),
            source_version_or_sha=observed_sha,
            provenance=provenance,
            payload=payload,
            duration_ms=duration_ms,
            causation_id=meta.causation_id,
            mission_id=meta.mission_id,
            plan_id=meta.plan_id,
            step_id=meta.step_id,
            mutation_attempted=False,
            evidence_refs=evidence_refs or [],
            error_code=error_code,
            retryable=retryable,
            fallback_used=False,
            declared_timeout_ms=self.op_timeout(operation, meta),
            upstream_writes=upstream_writes,
            write_class=write_class,
        )

    # --- uniform failure mapping -----------------------------------------
    def failure_status(self, exc: Exception) -> tuple[AdapterResultStatus, str]:
        """Map any adapter failure to a fail-closed (status, error_code)."""
        if isinstance(exc, (MutationShapedInput, MutationNotAuthorized)):
            return AdapterResultStatus.DENIED, "MUTATION_NOT_AUTHORIZED"
        if isinstance(exc, LiveConfigError):
            # allowlist / confinement violation (repo, root, endpoint)
            return AdapterResultStatus.DENIED, "AUTHORIZATION_DENIED"
        if isinstance(exc, TransportTimeout):
            return AdapterResultStatus.TIMEOUT, "TIMEOUT"
        if isinstance(exc, TransportDenied):
            return AdapterResultStatus.DENIED, "AUTHORIZATION_DENIED"
        if isinstance(exc, TransportMalformed):
            return AdapterResultStatus.MALFORMED, "MALFORMED_RESPONSE"
        if isinstance(exc, TransportUnavailable):
            return AdapterResultStatus.UNAVAILABLE, "DEPENDENCY_UNAVAILABLE"
        if isinstance(exc, TransportError):
            return AdapterResultStatus.UNAVAILABLE, "DEPENDENCY_UNAVAILABLE"
        if isinstance(exc, FounderRuntimeError):
            code = exc.code.value if hasattr(exc.code, "value") else str(exc.code)
            status_map = {
                "MALFORMED_RESPONSE": AdapterResultStatus.MALFORMED,
                "PROVENANCE_INVALID": AdapterResultStatus.MALFORMED,
                "UNSAFE_CAPABILITY": AdapterResultStatus.UNSAFE,
                "IDENTITY_MISMATCH": AdapterResultStatus.MALFORMED,
                "VERSION_MISMATCH": AdapterResultStatus.MALFORMED,
                "AUTHENTICATION_FAILED": AdapterResultStatus.DENIED,
                "AUTHORIZATION_DENIED": AdapterResultStatus.DENIED,
                "AUTHORITY_DENIED": AdapterResultStatus.DENIED,
                "MUTATION_NOT_AUTHORIZED": AdapterResultStatus.DENIED,
                "DEPENDENCY_UNAVAILABLE": AdapterResultStatus.UNAVAILABLE,
            }
            for qk in (
                "QDRANT_COUNT_MISMATCH", "QDRANT_COUNT_CHANGED",
                "QDRANT_COLLECTION_MISMATCH", "QDRANT_PAYLOAD_PROVENANCE_MISMATCH",
            ):
                status_map[qk] = AdapterResultStatus.MALFORMED
            return status_map.get(code, AdapterResultStatus.FAILED), code
        return AdapterResultStatus.FAILED, "INTERNAL_ADAPTER_FAILURE"

    def failure_result(
        self,
        *,
        operation: str,
        meta: AdapterRequestMeta,
        exc: Exception,
        duration_ms: int = 0,
    ) -> AdapterResult:
        status, code = self.failure_status(exc)
        detail = redact_text(str(getattr(exc, "message", exc)))[:300]
        return self.live_result(
            operation=operation,
            meta=meta,
            status=status,
            payload={"error": code, "detail": detail},
            dependency_identity=self.dependency_identity().to_dict(),
            provenance=self.live_provenance(
                logical=f"live://{self.name}/{operation}",
                observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
                observed_sha="unavailable",
                evidence_class=EvidenceClass.UNAVAILABLE,
            ),
            observed_sha="unavailable",
            error_code=code,
            retryable=False,
        )
