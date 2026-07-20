"""Typed adapter Protocols (ABCs) — contracts only; no live I/O in P2-A."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from recipe_engine.founder_runtime.adapters.base import AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult
from recipe_engine.founder_runtime.adapters.identity import (
    AdapterIdentity, DependencyIdentity, Provenance,
)
from recipe_engine.founder_runtime.contracts.authority import AuthorityClass
from recipe_engine.founder_runtime.contracts.health import HealthSnapshot
from recipe_engine.founder_runtime.contracts.receipts import ExecutionReceipt


@dataclass(frozen=True)
class RetryPolicyDeclaration:
    """Declared retry policy — adapters do not auto-retry across modes."""

    max_attempts: int = 1
    backoff_ms: int = 0
    retry_on: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "backoff_ms": self.backoff_ms,
            "retry_on": list(self.retry_on),
        }


@dataclass(frozen=True)
class AdapterRequestMeta:
    """Common request metadata for all adapter operations."""

    correlation_id: str
    causation_id: str | None = None
    mission_id: str | None = None
    plan_id: str | None = None
    step_id: str | None = None
    authority_class: AuthorityClass | str = AuthorityClass.A0
    timeout_ms: int = 5000
    observed_at: str = ""

    def authority_value(self) -> str:
        a = self.authority_class
        return a.value if isinstance(a, AuthorityClass) else str(a)


@runtime_checkable
class AdapterProtocol(Protocol):
    """Base adapter surface."""

    name: str

    def adapter_identity(self) -> AdapterIdentity: ...
    def dependency_identity(self) -> DependencyIdentity: ...
    def mode(self) -> AdapterMode: ...
    def mutation_class(self) -> MutationClass: ...
    def retry_policy(self) -> RetryPolicyDeclaration: ...


@runtime_checkable
class MotherHealthAdapter(Protocol):
    name: str

    def adapter_identity(self) -> AdapterIdentity: ...
    def mode(self) -> AdapterMode: ...
    def health_snapshot(self, *, observed_at: str, meta: AdapterRequestMeta | None = None) -> HealthSnapshot: ...
    def health_result(self, *, observed_at: str, meta: AdapterRequestMeta) -> AdapterResult: ...


@runtime_checkable
class GitHubRepositoryAdapter(Protocol):
    name: str

    def adapter_identity(self) -> AdapterIdentity: ...
    def mode(self) -> AdapterMode: ...
    def repo_state(self, repo: str, meta: AdapterRequestMeta | None = None) -> dict[str, Any]: ...
    def repo_state_result(self, repo: str, meta: AdapterRequestMeta) -> AdapterResult: ...


@runtime_checkable
class VaultDocumentAdapter(Protocol):
    name: str

    def adapter_identity(self) -> AdapterIdentity: ...
    def mode(self) -> AdapterMode: ...
    def get_document(self, doc_id: str, meta: AdapterRequestMeta | None = None) -> dict[str, Any]: ...
    def get_document_result(self, doc_id: str, meta: AdapterRequestMeta) -> AdapterResult: ...
    def provenance(self, *, observed_at: str) -> Provenance: ...


@runtime_checkable
class QdrantRetrievalAdapter(Protocol):
    name: str

    def adapter_identity(self) -> AdapterIdentity: ...
    def mode(self) -> AdapterMode: ...
    def search(self, query: str, *, limit: int = 5, meta: AdapterRequestMeta | None = None) -> list[dict[str, Any]]: ...
    def search_result(self, query: str, *, limit: int = 5, meta: AdapterRequestMeta | None = None) -> AdapterResult: ...


@runtime_checkable
class IARInferenceAdapter(Protocol):
    name: str

    def adapter_identity(self) -> AdapterIdentity: ...
    def mode(self) -> AdapterMode: ...
    def interpret(self, request_text: str, *, principal_id: str = "jeramie", meta: AdapterRequestMeta | None = None) -> dict[str, Any]: ...
    def interpret_result(self, request_text: str, *, principal_id: str = "jeramie", meta: AdapterRequestMeta | None = None) -> AdapterResult: ...


@runtime_checkable
class AegisEvidenceAdapter(Protocol):
    name: str

    def adapter_identity(self) -> AdapterIdentity: ...
    def mode(self) -> AdapterMode: ...
    def assess_risk(self, subject: dict[str, Any], meta: AdapterRequestMeta | None = None) -> dict[str, Any]: ...
    def assess_risk_result(self, subject: dict[str, Any], meta: AdapterRequestMeta) -> AdapterResult: ...


@runtime_checkable
class GovernanceDecisionAdapter(Protocol):
    name: str

    def adapter_identity(self) -> AdapterIdentity: ...
    def mode(self) -> AdapterMode: ...
    def decide(self, authority_class: str | Any, *, action: str, scope: str, meta: AdapterRequestMeta | None = None) -> dict[str, Any]: ...
    def decide_result(self, authority_class: str | Any, *, action: str, scope: str, meta: AdapterRequestMeta) -> AdapterResult: ...


@runtime_checkable
class AxonReadonlyAdapter(Protocol):
    name: str

    def adapter_identity(self) -> AdapterIdentity: ...
    def mode(self) -> AdapterMode: ...
    def execute_readonly_step(self, **kwargs: Any) -> ExecutionReceipt: ...
    def execute_readonly_result(self, **kwargs: Any) -> AdapterResult: ...


@runtime_checkable
class AxonMutationAdapter(Protocol):
    """Mutation contract only — no active implementation in P2-A."""

    name: str

    def adapter_identity(self) -> AdapterIdentity: ...
    def mode(self) -> AdapterMode: ...
    def execute_mutation(self, **kwargs: Any) -> AdapterResult: ...


@runtime_checkable
class MissionPersistenceAdapter(Protocol):
    name: str

    def adapter_identity(self) -> AdapterIdentity: ...
    def mode(self) -> AdapterMode: ...
    def backend_class(self) -> str: ...


@runtime_checkable
class ClockAdapter(Protocol):
    def now_iso(self) -> str: ...


@runtime_checkable
class IdentityProvenanceProvider(Protocol):
    def adapter_identity_for(self, name: str) -> AdapterIdentity: ...
    def dependency_identity_for(self, name: str) -> DependencyIdentity: ...
    def provenance_for(self, name: str, *, observed_at: str) -> Provenance: ...
