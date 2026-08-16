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

from typing import Any

from recipe_engine.live_adapters.live_axon import LiveAxonReadonlyAdapter
from recipe_engine.live_adapters.live_github import LiveGitHubAdapter
from recipe_engine.live_adapters.live_mother import LiveMotherAdapter
from recipe_engine.live_adapters.live_qdrant import LiveQdrantAdapter
from recipe_engine.live_adapters.live_vault import LiveVaultAdapter
from recipe_engine.live_adapters.settings import LiveAdapterSettings


def build_live_organs(settings: LiveAdapterSettings) -> dict[str, Any]:
    """The four read-only organs a long-horizon mission observes.

    Every one is read-only by construction: Mother and GitHub expose inspection
    only, Vault serves provenance metadata but never document content, and
    Qdrant offers bounded verification behind the host-recovery gate and refuses
    semantic search outright.
    """
    return {
        "mother": LiveMotherAdapter(settings),
        "github": LiveGitHubAdapter(settings),
        "vault": LiveVaultAdapter(settings),
        "qdrant": LiveQdrantAdapter(settings),
    }


def build_live_executor(settings: LiveAdapterSettings) -> LiveAxonReadonlyAdapter:
    """The read-only step executor that issues each stage's receipt.

    AXON read-only only.  It never claims governance authority and never carries
    mutation authorization; the mutation adapter is a hard stop in every
    configuration and is not constructed here at all.
    """
    return LiveAxonReadonlyAdapter(settings)
