"""P2-B end-to-end read-only canary tests.

Two worlds:
  * REAL environment (this host): git+vault live-observe real repos; the Qdrant
    host-recovery gate is sealed (HOLD audit only) -> the canary HOLDs at Qdrant,
    deterministically, with NO fixture fallback.
  * FULLY-LIVE (mocked organs on loopback + green host audit) -> the canary goes
    GREEN across all 8 organs, with a deterministic receipt chain, and produces
    an identical signature on replay (the "run twice" proof).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.live_adapters.canary import run_live_readonly_canary, canary_signature, ORGAN_ORDER
from recipe_engine.live_adapters.settings import LiveAdapterSettings, RepoRoot

from tests.founder_runtime import p2b_harness as H


def _real_settings(td: Path) -> LiveAdapterSettings:
    HOME = os.path.expanduser("~")
    return LiveAdapterSettings(
        repo_roots=(
            RepoRoot("NeuruhAI/recipe-engine", f"{HOME}/neuruh-repos/NeuruhAI/recipe-engine"),
            RepoRoot("NeuruhAI/neuruh-vault", f"{HOME}/neuruh-repos/NeuruhAI/neuruh-vault", role="vault_source"),
            RepoRoot("neuruh-vault-runtime", f"{HOME}/neuruh-vault", role="vault_runtime"),
        ),
        endpoints=(
            H.endpoint("qdrant", 1), H.endpoint("mother", 1), H.endpoint("iar", 1),
            H.endpoint("aegis", 1), H.endpoint("governance", 1), H.endpoint("axon", 1),
        ),
        host_recovery_audit_root=f"{HOME}/neuruh-workspace/audit-output/host-colima-integrity-recovery",
        recipe_engine_sha="ead8a89e8724f9ba9ea5db8ce44cddf9e62db540",
    )


class CanaryRealEnvHoldTests(unittest.TestCase):
    def test_holds_at_qdrant_gate_no_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            s = _real_settings(Path(td))
            r1 = run_live_readonly_canary(s)
            self.assertEqual(r1.verdict, "HOLD")
            self.assertEqual(r1.hold_organ, "qdrant")
            self.assertIn("qdrant", (r1.hold_reason or ""))
            # organs BEFORE qdrant were live successes (git + vault)
            statuses = {o.organ: o.status for o in r1.observations}
            self.assertEqual(statuses.get("github"), "success")
            self.assertEqual(statuses.get("vault"), "success")
            self.assertEqual(statuses.get("qdrant"), "denied")
            # honest HOLD invariants
            self.assertFalse(r1.fixture_fallback_used)
            self.assertFalse(r1.mutation_observed)
            self.assertFalse(r1.production_persistence)
            self.assertFalse(r1.external_communication)
            for o in r1.observations:
                self.assertEqual(o.adapter_mode, "live")
                self.assertFalse(o.fallback_used)

    def test_real_env_hold_deterministic_on_replay(self):
        with tempfile.TemporaryDirectory() as td:
            s = _real_settings(Path(td))
            r1 = run_live_readonly_canary(s)
            r2 = run_live_readonly_canary(s)
            self.assertEqual(canary_signature(r1), canary_signature(r2))


class CanaryFullyLiveGreenTests(unittest.TestCase):
    def _governance_route(self):
        def route(body: bytes):
            action = json.loads(body or b"{}").get("action", {})
            return (200, H.governance_decision_body(action))
        return route

    def _build_all_live(self, stack: ExitStack, td: Path) -> LiveAdapterSettings:
        # real temp git repos for github + vault
        gh = Path(td) / "recipe-engine"
        vs = Path(td) / "vault-src"
        vr = Path(td) / "vault-run"
        H.make_git_repo(gh, origin="https://github.com/NeuruhAI/recipe-engine.git")
        H.make_git_repo(vs)
        H.make_git_repo(vr, extra_commit=True)
        # green host recovery audit
        H.make_green_host_audit(Path(td) / "host")
        # one mock server per organ (distinct ports avoid path collisions)
        qport, _ = stack.enter_context(H.mock_service(H.qdrant_routes()))
        mport, _ = stack.enter_context(H.mock_service({("GET", "/health"): (200, {"overall": "healthy", "dependencies": [], "blocked": []})}))
        iport, _ = stack.enter_context(H.mock_service({("GET", "/provenance"): (200, H.iar_provenance_body())}))
        aport, _ = stack.enter_context(H.mock_service({("GET", "/world/neuruh_status/opinion"): (200, H.aegis_world_body())}))
        gport, _ = stack.enter_context(H.mock_service({("POST", "/v1/evaluate"): self._governance_route()}))
        xport, _ = stack.enter_context(H.mock_service({("GET", "/health"): (200, H.axon_identity_body())}))
        return LiveAdapterSettings(
            repo_roots=(
                RepoRoot("NeuruhAI/recipe-engine", str(gh)),
                RepoRoot("NeuruhAI/neuruh-vault", str(vs), role="vault_source"),
                RepoRoot("neuruh-vault-runtime", str(vr), role="vault_runtime"),
            ),
            endpoints=(
                H.endpoint("qdrant", qport), H.endpoint("mother", mport), H.endpoint("iar", iport),
                H.endpoint("aegis", aport), H.endpoint("governance", gport), H.endpoint("axon", xport),
            ),
            host_recovery_audit_root=str(Path(td) / "host"),
            recipe_engine_sha="ead8a89",
        )

    def test_all_live_goes_green(self):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            s = self._build_all_live(stack, Path(td))
            r = run_live_readonly_canary(s)
            self.assertEqual(r.verdict, "GREEN", r.hold_reason)
            self.assertEqual([o.organ for o in r.observations], list(ORGAN_ORDER))
            self.assertTrue(all(o.status == "success" for o in r.observations))
            self.assertTrue(all(o.adapter_mode == "live" for o in r.observations))
            self.assertFalse(r.fixture_fallback_used)
            self.assertFalse(r.mutation_observed)
            # receipt chain integrity: prev_hash links + 8 entries
            self.assertEqual(len(r.receipt_chain), 8)
            prev = "0" * 64
            for entry in r.receipt_chain:
                self.assertEqual(entry["prev_hash"], prev)
                prev = entry["entry_hash"]

    def test_green_deterministic_replay_runs_twice(self):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            s = self._build_all_live(stack, Path(td))
            r1 = run_live_readonly_canary(s)
            r2 = run_live_readonly_canary(s)
            self.assertEqual(r1.verdict, "GREEN")
            self.assertEqual(r2.verdict, "GREEN")
            # identical result on replay without repeating unsafe side effects
            self.assertEqual(canary_signature(r1), canary_signature(r2))
            self.assertEqual(r1.chain_root_hash, r2.chain_root_hash)


if __name__ == "__main__":
    unittest.main()
