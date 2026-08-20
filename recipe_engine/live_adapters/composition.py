"""Composition root: build the live read-only organ set for a mission.

``founder_runtime`` must never import ``live_adapters``.  The dependency is
one-way, and the AST confinement scan over the founder-runtime reachable graph
is only meaningful while it stays that way — a mission module that reached into
this package would drag sockets, subprocess and filesystem I/O into the scanned
graph.

So the mission declares what it needs (an organ set and a read-only executor)
and something outside the package supplies it.  This is that supplier.  It may
read settings and construct live adapters; the mission it hands them to cannot.
"""

from __future__ import annotations

import os
from typing import Any

from recipe_engine.live_adapters.live_axon import LiveAxonReadonlyAdapter
from recipe_engine.live_adapters.live_github import LiveGitHubAdapter
from recipe_engine.live_adapters.live_governance import LiveGovernanceAdapter
from recipe_engine.live_adapters.live_mother import LiveMotherAdapter
from recipe_engine.live_adapters.live_qdrant import LiveQdrantAdapter
from recipe_engine.live_adapters.live_vault import LiveVaultAdapter
from recipe_engine.live_adapters.settings import LiveAdapterSettings


def _newest_audit(root: str) -> str | None:
    if not os.path.isdir(root):
        return None
    for name in sorted((d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))), reverse=True):
        audit = os.path.join(root, name)
        if os.path.isfile(os.path.join(audit, "FINAL_VERDICT.md")):
            return audit
    return None


def build_default_live_settings() -> LiveAdapterSettings:
    """Canonical live settings from the sealed GREEN audits on this host.

    Fails closed when the Mother/AEGIS audits are absent — a live organ set is
    never built from guessed endpoints.
    """
    from recipe_engine.live_adapters.canonical_topology import (
        build_canonical_topology,
        read_endpoint_port_from_audit,
    )
    from recipe_engine.live_adapters.settings import RepoRoot

    home = os.path.expanduser("~")
    base = f"{home}/neuruh-workspace/audit-output"
    mother_audit = _newest_audit(f"{base}/mother-p2b-readonly-http")
    aegis_audit = _newest_audit(f"{base}/aegis-p2b-readonly-opinion-http")
    if not mother_audit or not aegis_audit:
        raise RuntimeError(
            "Mother/AEGIS GREEN audits not present — canonical topology cannot be built"
        )
    return build_canonical_topology(
        repo_roots=(
            RepoRoot("NeuruhAI/recipe-engine", f"{home}/neuruh-repos/NeuruhAI/recipe-engine"),
            RepoRoot("NeuruhAI/neuruh-vault", f"{home}/neuruh-repos/NeuruhAI/neuruh-vault", role="vault_source"),
            RepoRoot("neuruh-vault-runtime", f"{home}/neuruh-vault", role="vault_runtime"),
        ),
        mother_port=read_endpoint_port_from_audit(mother_audit, "mother"),
        aegis_port=read_endpoint_port_from_audit(aegis_audit, "aegis"),
        host_recovery_audit_base=base,
    )


def build_live_organs(settings: LiveAdapterSettings) -> dict[str, Any]:
    """The read-only organs a long-horizon mission observes.

    Every one is read-only by construction: Mother and GitHub expose inspection
    only, Vault serves provenance metadata but never document content, and
    Qdrant offers bounded verification behind the host-recovery gate and refuses
    semantic search outright.

    Governance is one of them.  It was built, tested and then left out of this
    dict, which is precisely why the live mission had no way to reach real
    authority and fell back to a self-issued one.  A mission cannot construct it
    (``founder_runtime`` must not import ``live_adapters``), so if the
    composition root omits it there is no authority in the live path at all.
    It is read-only here too: the adapter observes a decision and stamps
    NOT_CLAIMED_BY_GOVERNANCE; it never requests or consumes mutation authority.
    """
    return {
        "mother": LiveMotherAdapter(settings),
        "github": LiveGitHubAdapter(settings),
        "vault": LiveVaultAdapter(settings),
        "qdrant": LiveQdrantAdapter(settings),
        "governance": LiveGovernanceAdapter(settings),
    }


def build_live_executor(settings: LiveAdapterSettings) -> LiveAxonReadonlyAdapter:
    """The read-only step executor that issues each stage's receipt.

    AXON read-only only.  It never claims governance authority and never carries
    mutation authorization; the mutation adapter is a hard stop in every
    configuration and is not constructed here at all.
    """
    return LiveAxonReadonlyAdapter(settings)
