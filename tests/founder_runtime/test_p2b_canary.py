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
        # Post-repair: search BOTH allowlisted roots under the real workspace base.
        host_recovery_audit_base=f"{HOME}/neuruh-workspace/audit-output",
        recipe_engine_sha="ead8a89e8724f9ba9ea5db8ce44cddf9e62db540",
    )


class CanaryRealEnvHoldTests(unittest.TestCase):
    def test_gate_opens_but_canary_holds_on_service_availability(self):
        # Post-repair the host gate OPENS against the real authoritative GREEN
        # audit; with the organ endpoints closed (port 1) the canary still HOLDs
        # on service availability — no fixture fallback, no mutation.
        from recipe_engine.live_adapters.live_qdrant import LiveQdrantAdapter
        s = _real_settings(Path("/tmp"))
        self.assertTrue(LiveQdrantAdapter(s).gate_decision().open, "real GREEN audit must open the gate")
        r1 = run_live_readonly_canary(s)
        self.assertEqual(r1.verdict, "HOLD")
        statuses = {o.organ: o.status for o in r1.observations}
        self.assertEqual(statuses.get("github"), "success")
        self.assertEqual(statuses.get("vault"), "success")
        # qdrant gate is OPEN now; the closed endpoint yields UNAVAILABLE, not the
        # DENIED-by-seal of the pre-repair world.
        self.assertEqual(statuses.get("qdrant"), "unavailable")
        self.assertEqual(r1.hold_organ, "qdrant")
        self.assertFalse(r1.fixture_fallback_used)
        self.assertFalse(r1.mutation_observed)
        self.assertFalse(r1.production_persistence)
        self.assertFalse(r1.external_communication)
        for o in r1.observations:
            self.assertEqual(o.adapter_mode, "live")
            self.assertFalse(o.fallback_used)

    def test_real_env_hold_deterministic_on_replay(self):
        s = _real_settings(Path("/tmp"))
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
        # sealed GREEN host recovery audit under a hermetic temp base
        H.write_sealed_audit(Path(td) / "host")
        # one mock server per organ (distinct ports avoid path collisions)
        qport, _ = stack.enter_context(H.mock_service(H.qdrant_routes()))
        mport, _ = stack.enter_context(H.mock_service({("GET", "/health"): (200, {"overall": "healthy", "dependencies": [], "blocked": []})}))
        iport, _ = stack.enter_context(H.mock_service({("GET", "/provenance"): (200, H.iar_provenance_body())}))
        aport, _ = stack.enter_context(H.mock_service({("GET", "/world/defensenet/opinion"): (200, H.aegis_world_body("defensenet"))}))
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
            host_recovery_audit_base=str(Path(td) / "host"),
            recipe_engine_sha="ead8a89",
            iar_ledger_baseline_count=H.iar_fixture_ledger()["baseline_count"],
            iar_ledger_baseline_sha256=H.iar_fixture_ledger()["baseline_sha256"],
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
