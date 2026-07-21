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
from recipe_engine.live_adapters.settings import LiveAdapterSettings, LiveEndpoint, RepoRoot


def build_settings() -> LiveAdapterSettings:
    home = os.path.expanduser("~")
    return LiveAdapterSettings(
        repo_roots=(
            RepoRoot("NeuruhAI/recipe-engine", f"{home}/neuruh-repos/NeuruhAI/recipe-engine"),
            RepoRoot("NeuruhAI/neuruh-vault", f"{home}/neuruh-repos/NeuruhAI/neuruh-vault", role="vault_source"),
            RepoRoot("neuruh-vault-runtime", f"{home}/neuruh-vault", role="vault_runtime"),
        ),
        endpoints=(
            LiveEndpoint("qdrant", "127.0.0.1", 6333, "loopback"),
            LiveEndpoint("mother", "127.0.0.1", 8077, "loopback"),
            LiveEndpoint("iar", "127.0.0.1", 8090, "loopback"),
            LiveEndpoint("aegis", "127.0.0.1", 8055, "loopback"),
            LiveEndpoint("governance", "127.0.0.1", 8848, "loopback"),
            LiveEndpoint("axon", "127.0.0.1", 8080, "loopback"),
        ),
        host_recovery_audit_root=f"{home}/neuruh-workspace/audit-output/host-colima-integrity-recovery",
        axon_source_sha="f3a94d1a68ac6b07525e962692901411bd43e6bf",
        governance_source_sha="8e03131b628ba611d038551a47741623c3a80f4b",
        recipe_engine_sha="ead8a89e8724f9ba9ea5db8ce44cddf9e62db540",
    )


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    settings = build_settings()
    result = run_live_readonly_canary(settings)
    doc = result.to_dict()
    doc["run_label"] = label
    doc["signature"] = canary_signature(result)
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
