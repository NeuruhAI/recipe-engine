"""AXON mutation Protocol hard stop — no active implementation in P2-A."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.adapters.base import AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, AdapterResultStatus, make_result
from recipe_engine.founder_runtime.adapters.identity import (
    AdapterIdentity, DependencyIdentity, SCHEMA_ADAPTER_IDENTITY, SCHEMA_DEPENDENCY_IDENTITY,
    EvidenceClass, DivergenceState, Provenance, SCHEMA_PROVENANCE,
)
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, RetryPolicyDeclaration
from recipe_engine.founder_runtime.errors import MutationNotAuthorized, ErrorCode


# Future gate requirements (none satisfied in P2-A)
MUTATION_GATES = {
    "GOVERNANCE_UP2": False,
    "AXON_UP3": False,
    "CONTENT_BOUND_APPROVAL": False,
    "MUTATION_RECEIPT_CONTRACT": False,
    "RUNTIME_MUTATION_SEAL": False,
}


def mutation_authorized() -> bool:
    return all(MUTATION_GATES.values())


class AxonMutationHardStop:
    """Any resolve/invoke path returns MUTATION_NOT_AUTHORIZED."""

    name = "axon_mutation"
    adapter_version = "p2a.hard_stop.1"

    def __init__(self, mode: str | AdapterMode = AdapterMode.DISABLED, **_kwargs: Any):
        # Mode cannot enable mutation — always hard-stopped
        self._requested_mode = mode
        self.mode_value = AdapterMode.DISABLED

    def mode(self) -> AdapterMode:
        return AdapterMode.DISABLED

    def mutation_class(self) -> MutationClass:
        return MutationClass.MUTATING

    def retry_policy(self) -> RetryPolicyDeclaration:
        return RetryPolicyDeclaration(max_attempts=1)

    def adapter_identity(self) -> AdapterIdentity:
        return AdapterIdentity(
            schema_version=SCHEMA_ADAPTER_IDENTITY,
            canonical_name=self.name,
            owner_repository="NeuruhAI/recipe-engine",
            implementation_version=self.adapter_version,
            source_sha="hard_stop",
            mode=AdapterMode.DISABLED,
            capability_allowlist=(),
            mutation_capability=False,  # never true until gates
            policy_version="p2a.mutation.hard_stop.v1",
        )

    def dependency_identity(self) -> DependencyIdentity:
        return DependencyIdentity(
            schema_version=SCHEMA_DEPENDENCY_IDENTITY,
            service_name="axon",
            endpoint_class="mutation_disabled",
            expected_version_or_sha="n/a",
            observed_version_or_sha="n/a",
            identity_verified=False,
            readiness_verified=False,
        )

    def execute_mutation(self, **kwargs: Any) -> AdapterResult:
        meta: AdapterRequestMeta | None = kwargs.get("meta")
        observed = (meta.observed_at if meta and meta.observed_at else "1970-01-01T00:00:00.000Z")
        corr = (meta.correlation_id if meta else kwargs.get("correlation_id") or "mutation_denied")
        raise MutationNotAuthorized(
            "AXON mutation not authorized until UP-2/UP-3 and mutation seal",
            {
                "gates": dict(MUTATION_GATES),
                "requested_mode": str(self._requested_mode),
                "caller_flags": {k: type(v).__name__ for k, v in kwargs.items() if k != "meta"},
            },
        )

    def invoke(self, **kwargs: Any) -> AdapterResult:
        return self.execute_mutation(**kwargs)


def deny_mutation_result(*, observed_at: str, correlation_id: str, meta: AdapterRequestMeta | None = None) -> AdapterResult:
    """Envelope form of denial (for registry resolve without raising until invoke)."""
    return make_result(
        adapter_name="axon_mutation",
        adapter_version="p2a.hard_stop.1",
        adapter_mode=AdapterMode.DISABLED,
        operation="execute_mutation",
        status=AdapterResultStatus.DENIED,
        observed_at=observed_at,
        correlation_id=correlation_id,
        authority_class="A5",
        dependency_identity={"service_name": "axon", "endpoint_class": "mutation_disabled"},
        source_identity={"canonical_name": "axon_mutation", "mode": "disabled"},
        source_version_or_sha="hard_stop",
        provenance={
            "schema_version": SCHEMA_PROVENANCE,
            "source_path_or_logical": "hard_stop",
            "source_sha_or_version": "hard_stop",
            "runtime_sha_or_version": "hard_stop",
            "divergence_state": DivergenceState.NOT_APPLICABLE.value,
            "retrieval_timestamp": observed_at,
            "allowlist_policy_version": "p2a.mutation.hard_stop.v1",
            "evidence_class": EvidenceClass.UNAVAILABLE.value,
        },
        payload={"gates": dict(MUTATION_GATES), "authorized": False},
        mutation_attempted=False,
        error_code=ErrorCode.MUTATION_NOT_AUTHORIZED.value,
        retryable=False,
        causation_id=meta.causation_id if meta else None,
        mission_id=meta.mission_id if meta else None,
    )
