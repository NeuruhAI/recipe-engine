"""Typed, caller-supplied configuration for the live read-only adapters.

The composition root (canary / operator / tests) builds a
:class:`LiveAdapterSettings` and hands it to the adapters.  Adapters never read
process environment; they never accept a raw URL or a raw filesystem path from a
caller at operation time.  Everything an adapter may reach — repository roots,
loopback endpoints, the Qdrant collection, the host-recovery audit root — is
pinned here, once, as data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class LiveConfigError(ValueError):
    """Raised when caller configuration is unsafe or malformed (fail closed)."""


@dataclass(frozen=True)
class RepoRoot:
    """One allowlisted local repository the Git/Vault adapters may observe."""

    repo_id: str          # logical selector, e.g. "NeuruhAI/recipe-engine"
    path: str             # absolute, canonical (realpath) root on disk
    role: str = "repo"    # "repo" | "vault_source" | "vault_runtime"

    def __post_init__(self) -> None:
        if not self.repo_id:
            raise LiveConfigError("repo_id required")
        if not self.path or not Path(self.path).is_absolute():
            raise LiveConfigError(f"repo root must be an absolute path: {self.path!r}")


@dataclass(frozen=True)
class LiveEndpoint:
    """One allowlisted loopback service endpoint.

    ``auth_secret`` is held only in memory and is NEVER serialized or hashed; the
    disclosed identity is always the logical ``endpoint_class``.
    """

    service_id: str
    host: str
    port: int
    endpoint_class: str            # logical class disclosed in evidence
    scheme: str = "http"
    auth_header: str | None = None
    auth_secret: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.host not in LOOPBACK_HOSTS:
            raise LiveConfigError(
                f"endpoint {self.service_id} host {self.host!r} is not loopback"
            )
        if self.scheme != "http":
            raise LiveConfigError(f"endpoint {self.service_id} must be plain-http loopback")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not (0 < self.port < 65536):
            raise LiveConfigError(f"endpoint {self.service_id} port invalid")
        if not self.endpoint_class:
            raise LiveConfigError("endpoint_class required")

    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


@dataclass(frozen=True)
class LiveAdapterSettings:
    """Everything the live adapters may reach.  Pure data, caller-owned."""

    repo_roots: tuple[RepoRoot, ...] = ()
    endpoints: tuple[LiveEndpoint, ...] = ()
    qdrant_collection: str = "neuruh_vault_canon"
    qdrant_point_baseline: int = 3049
    host_recovery_audit_root: str | None = None
    axon_source_sha: str = ""          # expected AXON ratified read-only main SHA
    governance_source_sha: str = ""    # expected Governance promoted main SHA
    recipe_engine_sha: str = ""        # this adapter package's owning source sha
    allow_live: bool = True

    def repo_root(self, repo_id: str) -> RepoRoot:
        for r in self.repo_roots:
            if r.repo_id == repo_id:
                return r
        raise LiveConfigError(f"repository {repo_id!r} is not on the live root allowlist")

    def repo_root_by_role(self, role: str) -> RepoRoot:
        for r in self.repo_roots:
            if r.role == role:
                return r
        raise LiveConfigError(f"no repo root configured for role {role!r}")

    def endpoint(self, service_id: str) -> LiveEndpoint:
        for e in self.endpoints:
            if e.service_id == service_id:
                return e
        raise LiveConfigError(f"service {service_id!r} is not on the loopback endpoint allowlist")

    def endpoint_map(self) -> Mapping[str, LiveEndpoint]:
        return {e.service_id: e for e in self.endpoints}
