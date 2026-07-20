"""Founder Runtime adapters — P2-A foundation (fixture + modes + protocols).

No live network calls. No live-to-fixture fallback.
"""

from .base import (
    AdapterMode, MutationClass, WriteClass, OperationalWriteRef,
    require_fixture_mode, normalize_mode, ALL_ADAPTER_MODES,
)
from .envelope import (
    AdapterResult, AdapterResultStatus, make_result, parse_adapter_result,
    validate_payload_hash, SCHEMA_ADAPTER_RESULT, SCHEMA_ADAPTER_RESULT_V1,
)
from .identity import (
    AdapterIdentity, DependencyIdentity, Provenance,
    fixture_identity, fixture_dependency, vault_provenance,
    VAULT_SOURCE_REPO, VAULT_RUNTIME_CHECKOUT, VaultDualRootConfig,
    normalize_vault_dual_roots,
)
from .timeouts import (
    OperationPolicy, ResolvedOperationPolicy, OPERATION_POLICIES,
    operation_policy, resolve_operation_policy,
)
from .config import AdapterConfig, AdapterBinding, fixture_only_config, REGISTRY_POLICY_VERSION
from .registry import AdapterRegistry, default_registry, ADAPTER_TYPES
from .axon_mutation import AxonMutationHardStop, mutation_authorized, MUTATION_GATES
from .protocols import (
    MotherHealthAdapter, GitHubRepositoryAdapter, VaultDocumentAdapter,
    QdrantRetrievalAdapter, IARInferenceAdapter, AegisEvidenceAdapter,
    GovernanceDecisionAdapter, AxonReadonlyAdapter, AxonMutationAdapter,
    MissionPersistenceAdapter, ClockAdapter, IdentityProvenanceProvider,
    CockpitPort, AdapterRequestMeta, RetryPolicyDeclaration, GovernanceAction,
)

__all__ = [
    "AdapterMode", "MutationClass", "WriteClass", "OperationalWriteRef",
    "require_fixture_mode", "normalize_mode", "ALL_ADAPTER_MODES",
    "AdapterResult", "AdapterResultStatus", "make_result", "parse_adapter_result",
    "validate_payload_hash", "SCHEMA_ADAPTER_RESULT", "SCHEMA_ADAPTER_RESULT_V1",
    "AdapterIdentity", "DependencyIdentity", "Provenance",
    "fixture_identity", "fixture_dependency", "vault_provenance",
    "VAULT_SOURCE_REPO", "VAULT_RUNTIME_CHECKOUT", "VaultDualRootConfig",
    "normalize_vault_dual_roots",
    "OperationPolicy", "ResolvedOperationPolicy", "OPERATION_POLICIES",
    "operation_policy", "resolve_operation_policy",
    "AdapterConfig", "AdapterBinding", "fixture_only_config", "REGISTRY_POLICY_VERSION",
    "AdapterRegistry", "default_registry", "ADAPTER_TYPES",
    "AxonMutationHardStop", "mutation_authorized", "MUTATION_GATES",
    "MotherHealthAdapter", "GitHubRepositoryAdapter", "VaultDocumentAdapter",
    "QdrantRetrievalAdapter", "IARInferenceAdapter", "AegisEvidenceAdapter",
    "GovernanceDecisionAdapter", "AxonReadonlyAdapter", "AxonMutationAdapter",
    "MissionPersistenceAdapter", "ClockAdapter", "IdentityProvenanceProvider", "CockpitPort",
    "AdapterRequestMeta", "RetryPolicyDeclaration", "GovernanceAction",
]
