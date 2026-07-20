"""Founder Runtime adapters — P2-A foundation (fixture + modes + protocols).

No live network calls. No live-to-fixture fallback.
"""

from .base import AdapterMode, MutationClass, require_fixture_mode, normalize_mode, ALL_ADAPTER_MODES
from .envelope import AdapterResult, AdapterResultStatus, make_result, SCHEMA_ADAPTER_RESULT
from .identity import (
    AdapterIdentity, DependencyIdentity, Provenance,
    fixture_identity, fixture_dependency, vault_provenance,
    VAULT_SOURCE_REPO, VAULT_RUNTIME_CHECKOUT,
)
from .config import AdapterConfig, AdapterBinding, fixture_only_config, REGISTRY_POLICY_VERSION
from .registry import AdapterRegistry, default_registry, ADAPTER_TYPES
from .axon_mutation import AxonMutationHardStop, mutation_authorized, MUTATION_GATES
from .protocols import (
    MotherHealthAdapter, GitHubRepositoryAdapter, VaultDocumentAdapter,
    QdrantRetrievalAdapter, IARInferenceAdapter, AegisEvidenceAdapter,
    GovernanceDecisionAdapter, AxonReadonlyAdapter, AxonMutationAdapter,
    MissionPersistenceAdapter, ClockAdapter, IdentityProvenanceProvider,
    AdapterRequestMeta, RetryPolicyDeclaration,
)

__all__ = [
    "AdapterMode", "MutationClass", "require_fixture_mode", "normalize_mode", "ALL_ADAPTER_MODES",
    "AdapterResult", "AdapterResultStatus", "make_result", "SCHEMA_ADAPTER_RESULT",
    "AdapterIdentity", "DependencyIdentity", "Provenance",
    "fixture_identity", "fixture_dependency", "vault_provenance",
    "VAULT_SOURCE_REPO", "VAULT_RUNTIME_CHECKOUT",
    "AdapterConfig", "AdapterBinding", "fixture_only_config", "REGISTRY_POLICY_VERSION",
    "AdapterRegistry", "default_registry", "ADAPTER_TYPES",
    "AxonMutationHardStop", "mutation_authorized", "MUTATION_GATES",
    "MotherHealthAdapter", "GitHubRepositoryAdapter", "VaultDocumentAdapter",
    "QdrantRetrievalAdapter", "IARInferenceAdapter", "AegisEvidenceAdapter",
    "GovernanceDecisionAdapter", "AxonReadonlyAdapter", "AxonMutationAdapter",
    "MissionPersistenceAdapter", "ClockAdapter", "IdentityProvenanceProvider",
    "AdapterRequestMeta", "RetryPolicyDeclaration",
]
