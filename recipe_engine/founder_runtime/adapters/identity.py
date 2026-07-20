"""Adapter identity, dependency identity, and provenance contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from recipe_engine.founder_runtime.adapters.base import AdapterMode, MutationClass
from recipe_engine.founder_runtime.errors import (
    ConfigurationMissing, IdentityMismatch, VersionMismatch, ProvenanceInvalid,
)
from recipe_engine.founder_runtime.ids import hash_payload


SCHEMA_ADAPTER_IDENTITY = "fr.adapter_identity.v1"
SCHEMA_DEPENDENCY_IDENTITY = "fr.dependency_identity.v1"
SCHEMA_PROVENANCE = "fr.provenance.v1"

# Vault path doctrine (P2-A contract; no live access)
VAULT_SOURCE_REPO = "~/neuruh-repos/NeuruhAI/neuruh-vault"
VAULT_RUNTIME_CHECKOUT = "~/neuruh-vault"


class DivergenceState(str, Enum):
    MATCHED = "matched"
    DIVERGED = "diverged"
    DIVERGED_RUNTIME_AHEAD_DIRTY = "diverged_runtime_ahead_dirty"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class EvidenceClass(str, Enum):
    FIXTURE = "fixture"
    LIVE_OBSERVED = "live_observed"
    DERIVED = "derived"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AdapterIdentity:
    schema_version: str
    canonical_name: str
    owner_repository: str
    implementation_version: str
    source_sha: str
    mode: AdapterMode
    capability_allowlist: tuple[str, ...]
    mutation_capability: bool
    policy_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "canonical_name": self.canonical_name,
            "owner_repository": self.owner_repository,
            "implementation_version": self.implementation_version,
            "source_sha": self.source_sha,
            "mode": self.mode.value if isinstance(self.mode, AdapterMode) else str(self.mode),
            "capability_allowlist": list(self.capability_allowlist),
            "mutation_capability": self.mutation_capability,
            "policy_version": self.policy_version,
        }

    def content_hash(self) -> str:
        return hash_payload(self.to_dict())


@dataclass(frozen=True)
class DependencyIdentity:
    schema_version: str
    service_name: str
    endpoint_class: str  # logical class only — never secret URL material
    expected_version_or_sha: str
    observed_version_or_sha: str
    identity_verified: bool
    readiness_verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "service_name": self.service_name,
            "endpoint_class": self.endpoint_class,
            "expected_version_or_sha": self.expected_version_or_sha,
            "observed_version_or_sha": self.observed_version_or_sha,
            "identity_verified": self.identity_verified,
            "readiness_verified": self.readiness_verified,
        }

    def assert_identity(self) -> None:
        if self.expected_version_or_sha and self.observed_version_or_sha:
            if self.expected_version_or_sha != self.observed_version_or_sha:
                raise VersionMismatch(
                    "dependency version/SHA mismatch",
                    {
                        "service": self.service_name,
                        "expected": self.expected_version_or_sha,
                        "observed": self.observed_version_or_sha,
                    },
                )
        if not self.identity_verified:
            raise IdentityMismatch(
                "dependency identity not verified",
                {"service": self.service_name},
            )


@dataclass(frozen=True)
class Provenance:
    schema_version: str
    source_path_or_logical: str
    source_sha_or_version: str
    runtime_sha_or_version: str
    divergence_state: DivergenceState
    retrieval_timestamp: str
    allowlist_policy_version: str
    evidence_class: EvidenceClass
    source_dirty: bool | None = None
    runtime_dirty_count: int | None = None
    ahead: int | None = None
    behind: int | None = None
    ancestry: str | None = None
    manifest_allowlist_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_path_or_logical": self.source_path_or_logical,
            "source_sha_or_version": self.source_sha_or_version,
            "runtime_sha_or_version": self.runtime_sha_or_version,
            "divergence_state": self.divergence_state.value,
            "retrieval_timestamp": self.retrieval_timestamp,
            "allowlist_policy_version": self.allowlist_policy_version,
            "evidence_class": self.evidence_class.value,
            "source_dirty": self.source_dirty,
            "runtime_dirty_count": self.runtime_dirty_count,
            "ahead": self.ahead,
            "behind": self.behind,
            "ancestry": self.ancestry,
            "manifest_allowlist_version": self.manifest_allowlist_version,
        }

    def assert_for_sync_claim(self) -> None:
        """Fail closed when provenance cannot support a synchronization claim."""
        if self.divergence_state != DivergenceState.MATCHED:
            raise ProvenanceInvalid(
                "only positively matched source/runtime provenance supports a synchronization claim",
                self.to_dict(),
            )


VAULT_ROOT_ALIASES = (
    "NEURUH_VAULT_ROOT",
    "NEURUH_VAULT",
    "VAULT_ROOT",
    "NEURUH_VAULT_PATH",
)
REJECTED_VAULT_CLONE_SHA_PREFIXES = ("d970ceb", "c2f84a")


@dataclass(frozen=True)
class VaultDualRootConfig:
    """Normalized doctrine roots; source is authority, runtime is substrate."""

    source_root: str
    runtime_root: str
    selected_aliases: tuple[str, ...]
    selected_role: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_root": self.source_root,
            "runtime_root": self.runtime_root,
            "selected_aliases": list(self.selected_aliases),
            "selected_role": self.selected_role,
            "source_is_canonical": True,
            "runtime_is_deployed_substrate": True,
        }


def normalize_vault_dual_roots(
    values: Mapping[str, str],
    *,
    source_sha: str | None = None,
    runtime_sha: str | None = None,
) -> VaultDualRootConfig:
    """Normalize legacy aliases without reading process environment state."""
    supplied = {
        key: str(values[key]).rstrip("/")
        for key in VAULT_ROOT_ALIASES
        if key in values and str(values[key]).strip()
    }
    if not supplied:
        raise ConfigurationMissing(
            "Vault dual-root configuration is unavailable",
            {"accepted_aliases": list(VAULT_ROOT_ALIASES)},
        )
    unique = set(supplied.values())
    if len(unique) != 1:
        raise ProvenanceInvalid(
            "conflicting Vault root aliases",
            {"aliases": sorted(supplied)},
        )
    selected = next(iter(unique))
    if any(token in selected for token in REJECTED_VAULT_CLONE_SHA_PREFIXES):
        raise ProvenanceInvalid("rejected historical clone root", {})
    for observed_sha in (source_sha, runtime_sha):
        if observed_sha and any(observed_sha.startswith(token) for token in REJECTED_VAULT_CLONE_SHA_PREFIXES):
            raise ProvenanceInvalid("rejected historical clone identity", {})
    if selected == VAULT_SOURCE_REPO:
        role = "source"
    elif selected == VAULT_RUNTIME_CHECKOUT:
        role = "runtime"
    else:
        raise ProvenanceInvalid(
            "Vault root is not one of the canonical dual roots",
            {"selected_role": "unknown"},
        )
    return VaultDualRootConfig(
        source_root=VAULT_SOURCE_REPO,
        runtime_root=VAULT_RUNTIME_CHECKOUT,
        selected_aliases=tuple(sorted(supplied)),
        selected_role=role,
    )


def vault_provenance(
    *,
    source_sha: str,
    runtime_sha: str,
    retrieval_timestamp: str,
    policy_version: str = "p2a.vault.v1",
    evidence_class: EvidenceClass = EvidenceClass.FIXTURE,
    source_dirty: bool | None = None,
    runtime_dirty_count: int | None = None,
    ahead: int | None = None,
    behind: int | None = None,
    ancestry: str | None = None,
    manifest_allowlist_version: str | None = None,
) -> Provenance:
    """Build Vault provenance using canonical source vs runtime checkout paths."""
    if source_sha == runtime_sha and source_sha not in ("", "unknown"):
        div = DivergenceState.MATCHED
    elif source_sha in ("fixture",) or runtime_sha in ("fixture",):
        div = DivergenceState.NOT_APPLICABLE
    elif source_sha and runtime_sha and source_sha != runtime_sha:
        if (ahead or 0) > 0 and (runtime_dirty_count or 0) > 0:
            div = DivergenceState.DIVERGED_RUNTIME_AHEAD_DIRTY
        else:
            div = DivergenceState.DIVERGED
    else:
        div = DivergenceState.UNKNOWN
    return Provenance(
        schema_version=SCHEMA_PROVENANCE,
        source_path_or_logical=VAULT_SOURCE_REPO,
        source_sha_or_version=source_sha,
        runtime_sha_or_version=runtime_sha,
        divergence_state=div,
        retrieval_timestamp=retrieval_timestamp,
        allowlist_policy_version=policy_version,
        evidence_class=evidence_class,
        source_dirty=source_dirty,
        runtime_dirty_count=runtime_dirty_count,
        ahead=ahead,
        behind=behind,
        ancestry=ancestry,
        manifest_allowlist_version=manifest_allowlist_version,
    )


def fixture_identity(
    name: str,
    *,
    capabilities: tuple[str, ...] = (),
    mutation: bool = False,
    version: str = "p2a.fixture.1",
    policy_version: str = "p2a.policy.v1",
) -> AdapterIdentity:
    return AdapterIdentity(
        schema_version=SCHEMA_ADAPTER_IDENTITY,
        canonical_name=name,
        owner_repository="NeuruhAI/recipe-engine",
        implementation_version=version,
        source_sha="fixture",
        mode=AdapterMode.FIXTURE,
        capability_allowlist=capabilities,
        mutation_capability=mutation,
        policy_version=policy_version,
    )


def fixture_dependency(service_name: str, *, endpoint_class: str = "fixture_local") -> DependencyIdentity:
    return DependencyIdentity(
        schema_version=SCHEMA_DEPENDENCY_IDENTITY,
        service_name=service_name,
        endpoint_class=endpoint_class,
        expected_version_or_sha="fixture",
        observed_version_or_sha="fixture",
        identity_verified=True,
        readiness_verified=True,
    )
