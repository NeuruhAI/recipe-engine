"""Disabled and unavailable adapter stubs — explicit, no fixture substitution."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.adapters.base import AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, AdapterResultStatus, make_result
from recipe_engine.founder_runtime.adapters.identity import (
    fixture_identity, SCHEMA_DEPENDENCY_IDENTITY, DependencyIdentity,
    EvidenceClass, DivergenceState, Provenance, SCHEMA_PROVENANCE,
)
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, RetryPolicyDeclaration
from recipe_engine.founder_runtime.errors import (
    AdapterDisabled, AdapterUnavailable, ErrorCode,
)


class ModeStubAdapter:
    """Generic disabled/unavailable stub for any adapter type."""

    def __init__(self, name: str, mode: AdapterMode, *, reason: str = ""):
        if mode not in (AdapterMode.DISABLED, AdapterMode.UNAVAILABLE):
            raise ValueError("ModeStubAdapter only for disabled/unavailable")
        self.name = name
        self._mode = mode
        self.reason = reason or mode.value
        self.adapter_version = f"p2a.stub.{mode.value}.1"

    def mode(self) -> AdapterMode:
        return self._mode

    def mutation_class(self) -> MutationClass:
        return MutationClass.NONE

    def retry_policy(self) -> RetryPolicyDeclaration:
        return RetryPolicyDeclaration(max_attempts=1)

    def adapter_identity(self):
        return fixture_identity(
            self.name,
            capabilities=(),
            mutation=False,
            version=self.adapter_version,
        ).__class__(  # rebuild with correct mode
            schema_version="fr.adapter_identity.v1",
            canonical_name=self.name,
            owner_repository="NeuruhAI/recipe-engine",
            implementation_version=self.adapter_version,
            source_sha="stub",
            mode=self._mode,
            capability_allowlist=(),
            mutation_capability=False,
            policy_version="p2a.stub.v1",
        )

    def dependency_identity(self) -> DependencyIdentity:
        return DependencyIdentity(
            schema_version=SCHEMA_DEPENDENCY_IDENTITY,
            service_name=self.name,
            endpoint_class=f"stub_{self._mode.value}",
            expected_version_or_sha="n/a",
            observed_version_or_sha="n/a",
            identity_verified=False,
            readiness_verified=False,
        )

    def _result(self, operation: str, meta: AdapterRequestMeta) -> AdapterResult:
        code = (
            ErrorCode.ADAPTER_DISABLED.value
            if self._mode == AdapterMode.DISABLED
            else ErrorCode.ADAPTER_UNAVAILABLE.value
        )
        status = (
            AdapterResultStatus.DISABLED
            if self._mode == AdapterMode.DISABLED
            else AdapterResultStatus.UNAVAILABLE
        )
        return make_result(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            adapter_mode=self._mode,
            operation=operation,
            status=status,
            observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            correlation_id=meta.correlation_id,
            authority_class=meta.authority_value(),
            dependency_identity=self.dependency_identity().to_dict(),
            source_identity=self.adapter_identity().to_dict(),
            source_version_or_sha="stub",
            provenance={
                "schema_version": SCHEMA_PROVENANCE,
                "source_path_or_logical": f"stub://{self.name}",
                "source_sha_or_version": "stub",
                "runtime_sha_or_version": "stub",
                "divergence_state": DivergenceState.NOT_APPLICABLE.value,
                "retrieval_timestamp": meta.observed_at or "",
                "allowlist_policy_version": "p2a.stub.v1",
                "evidence_class": EvidenceClass.UNAVAILABLE.value,
            },
            payload={"reason": self.reason},
            error_code=code,
            retryable=self._mode == AdapterMode.UNAVAILABLE,
            causation_id=meta.causation_id,
            mission_id=meta.mission_id,
            plan_id=meta.plan_id,
            step_id=meta.step_id,
            mutation_attempted=False,
            declared_timeout_ms=meta.timeout_ms,
        )

    def call(self, operation: str, meta: AdapterRequestMeta) -> AdapterResult:
        """Return explicit status envelope without invoking any dependency."""
        return self._result(operation, meta)

    def raise_for_mode(self) -> None:
        if self._mode == AdapterMode.DISABLED:
            raise AdapterDisabled(
                f"adapter {self.name} is disabled",
                {"adapter": self.name, "reason": self.reason},
            )
        raise AdapterUnavailable(
            f"adapter {self.name} is unavailable",
            {"adapter": self.name, "reason": self.reason},
        )
