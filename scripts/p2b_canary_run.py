#!/usr/bin/env python3
"""Run the P2-B live read-only canary against the REAL local environment.

Builds live settings from the real repository roots and the real loopback
service ports, then runs the ordered read-only canary.  Emits the CanaryResult
as JSON.  This is a composition root: it may read the environment to build
settings, but the adapters it drives never do.

Usage:  python3 scripts/p2b_canary_run.py [run_label]

No mutation, no production persistence, no external communication.  The live
Qdrant activation gate is enforced: with no sealed GREEN host recovery audit it
HOLDs at Qdrant and never contacts the service.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from recipe_engine.live_adapters.canary import run_live_readonly_canary, canary_signature
from recipe_engine.live_adapters.canonical_topology import (
    CANONICAL_AEGIS_WORLD,
    AegisWorldError,
    build_canonical_topology,
    read_endpoint_port_from_audit,
)
from recipe_engine.live_adapters.settings import LiveAdapterSettings, RepoRoot


def _newest_audit(root: str) -> str | None:
    if not os.path.isdir(root):
        return None
    for name in sorted((d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))), reverse=True):
        audit = os.path.join(root, name)
        if os.path.isfile(os.path.join(audit, "FINAL_VERDICT.md")):
            return audit
    return None


def build_settings() -> LiveAdapterSettings:
    home = os.path.expanduser("~")
    base = f"{home}/neuruh-workspace/audit-output"
    # Mother + AEGIS endpoints come from their reviewed sealed GREEN audits.
    mother_audit = _newest_audit(f"{base}/mother-p2b-readonly-http")
    aegis_audit = _newest_audit(f"{base}/aegis-p2b-readonly-opinion-http")
    if not mother_audit or not aegis_audit:
        raise SystemExit("Mother/AEGIS GREEN audits not present — canonical topology cannot be built")
    mother_port = read_endpoint_port_from_audit(mother_audit, "mother")
    aegis_port = read_endpoint_port_from_audit(aegis_audit, "aegis")
    # Canonical topology: Qdrant 6333, IAR 8090, Governance 8848, AXON 8300,
    # Mother/AEGIS from audits; promoted SHAs pinned; fixed ports non-overridable.
    return build_canonical_topology(
        repo_roots=(
            RepoRoot("NeuruhAI/recipe-engine", f"{home}/neuruh-repos/NeuruhAI/recipe-engine"),
            RepoRoot("NeuruhAI/neuruh-vault", f"{home}/neuruh-repos/NeuruhAI/neuruh-vault", role="vault_source"),
            RepoRoot("neuruh-vault-runtime", f"{home}/neuruh-vault", role="vault_runtime"),
        ),
        mother_port=mother_port,
        aegis_port=aegis_port,
        host_recovery_audit_base=base,
        recipe_engine_sha="ead8a89e8724f9ba9ea5db8ce44cddf9e62db540",
    )


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    settings = build_settings()
    # Fixed allowlisted CLI/env selection of the AEGIS world, canonical default.
    # Composition root reads the env; the canary allowlist-validates it (a mock or
    # non-allowlisted world fails closed, never a fixture fallback).
    requested_world = os.environ.get("P2B_AEGIS_WORLD")  # None => canonical default (defensenet)
    try:
        result = run_live_readonly_canary(settings, aegis_world=requested_world)
    except AegisWorldError as exc:
        print(json.dumps({"error": "AEGIS_WORLD_REJECTED", "detail": str(exc), "run_label": label}, indent=2))
        return 2
    doc = result.to_dict()
    doc["run_label"] = label
    doc["aegis_world"] = requested_world or CANONICAL_AEGIS_WORLD
    doc["signature"] = canary_signature(result)
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
