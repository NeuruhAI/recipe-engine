"""P2-B canary AEGIS world-selection repair tests.

The canary previously hardcoded the MOCK world ``neuruh_status`` (404 against the
real AEGIS candidate).  The repair defaults to the canonical real world
``defensenet`` from a fixed allowlist, rejects the mock world and any arbitrary
name fail-closed, and never falls back to a fixture/mock world.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.live_adapters.canary import run_live_readonly_canary, ORGAN_ORDER
from recipe_engine.live_adapters.canonical_topology import (
    CANONICAL_AEGIS_WORLD,
    LIVE_AEGIS_WORLD_ALLOWLIST,
    MOCK_AEGIS_WORLDS,
    AegisWorldError,
    resolve_aegis_world,
)
from recipe_engine.live_adapters.live_aegis import LiveAegisAdapter
from recipe_engine.live_adapters.settings import LiveAdapterSettings

from tests.founder_runtime import p2b_harness as H

META = AdapterRequestMeta(correlation_id="world", observed_at="2026-07-21T00:00:00.000Z")


# ------------------------------------------------------------ resolver unit
class ResolveAegisWorldTests(unittest.TestCase):
    def test_default_is_canonical_real_world(self):
        self.assertEqual(resolve_aegis_world(None), "defensenet")
        self.assertEqual(CANONICAL_AEGIS_WORLD, "defensenet")
        self.assertIn("defensenet", LIVE_AEGIS_WORLD_ALLOWLIST)

    def test_allowlisted_worlds_pass_through(self):
        for w in ("aegis", "defensenet", "signal-iq", "taxshield"):
            self.assertEqual(resolve_aegis_world(w), w)

    def test_mock_world_rejected_in_live(self):
        self.assertIn("neuruh_status", MOCK_AEGIS_WORLDS)
        self.assertNotIn("neuruh_status", LIVE_AEGIS_WORLD_ALLOWLIST)
        with self.assertRaises(AegisWorldError):
            resolve_aegis_world("neuruh_status")

    def test_arbitrary_world_fails_closed(self):
        for w in ("arbitrary", "prod", "foo", "NEURUH", "defensenet2"):
            with self.assertRaises(AegisWorldError):
                resolve_aegis_world(w)

    def test_path_like_world_rejected(self):
        for w in ("../etc/passwd", "a/b", "world/../x"):
            with self.assertRaises(AegisWorldError):
                resolve_aegis_world(w)


# ------------------------------------------------- hermetic AEGIS reproduction
class AegisWorldReproductionTests(unittest.TestCase):
    """A mock AEGIS that serves ONLY real worlds (404 otherwise), mirroring the
    real candidate — reproduces the old-default 404 and proves the new default."""

    def _mock_real_aegis(self, stack: ExitStack):
        # serve the canonical real world; everything else 404 (like real AEGIS)
        routes = {("GET", "/world/defensenet/opinion"): (200, H.aegis_world_body("defensenet"))}
        port, _ = stack.enter_context(H.mock_service(routes))
        ep = H.endpoint("aegis", port)
        return LiveAegisAdapter(LiveAdapterSettings(endpoints=(ep,)), http=H.http_client(ep))

    def test_old_default_reproduces_404(self):
        with ExitStack() as stack:
            a = self._mock_real_aegis(stack)
            res = a.validate_world("neuruh_status", META)  # the old hardcoded default
            self.assertEqual(res.status, "malformed")  # 404 -> fail closed
            self.assertEqual(res.error_code, "MALFORMED_RESPONSE")

    def test_repaired_canonical_default_reaches_aegis(self):
        with ExitStack() as stack:
            a = self._mock_real_aegis(stack)
            res = a.validate_world(resolve_aegis_world(None), META)  # defensenet
            self.assertEqual(res.status, "success")
            self.assertEqual(res.payload["world"], "defensenet")
            self.assertFalse(res.fallback_used)
            self.assertFalse(res.mutation_attempted)


# ------------------------------------------------- canary-level world selection
class CanaryWorldSelectionTests(unittest.TestCase):
    def _min_settings(self):
        # github/vault repos not needed: the canary fails fast on the world before
        # any organ runs when the world is invalid.
        return LiveAdapterSettings(endpoints=(H.endpoint("aegis", 1),))

    def test_canary_rejects_mock_world_before_running(self):
        with self.assertRaises(AegisWorldError):
            run_live_readonly_canary(self._min_settings(), aegis_world="neuruh_status")

    def test_canary_rejects_arbitrary_world_before_running(self):
        with self.assertRaises(AegisWorldError):
            run_live_readonly_canary(self._min_settings(), aegis_world="prod_world")

    def test_canary_default_world_not_rejected(self):
        # default None resolves to defensenet; the canary starts and holds on a
        # down organ (github, since repo_roots empty) — it does NOT raise
        # AegisWorldError and does NOT use the mock world.
        try:
            r = run_live_readonly_canary(self._min_settings(), aegis_world=None)
        except AegisWorldError:
            self.fail("default world must not be rejected")
        self.assertEqual(r.verdict, "HOLD")  # holds at github (no repo roots)
        self.assertNotIn("neuruh_status", r.hold_reason or "")


# ------------------------------------------------- full-stack GREEN with canonical world
class CanaryCanonicalWorldGreenTests(unittest.TestCase):
    def _governance_route(self):
        import json

        def route(body: bytes):
            a = json.loads(body or b"{}").get("action", {})
            return (200, H.governance_decision_body(a))
        return route

    def _all_live(self, stack: ExitStack, td: Path) -> LiveAdapterSettings:
        gh = Path(td) / "recipe-engine"; vs = Path(td) / "vault-src"; vr = Path(td) / "vault-run"
        H.make_git_repo(gh, origin="https://github.com/NeuruhAI/recipe-engine.git")
        H.make_git_repo(vs); H.make_git_repo(vr, extra_commit=True)
        H.write_sealed_audit(Path(td) / "host")
        qport, _ = stack.enter_context(H.mock_service(H.qdrant_routes()))
        mport, _ = stack.enter_context(H.mock_service({("GET", "/health"): (200, {"overall": "healthy", "dependencies": [], "blocked": []})}))
        iport, _ = stack.enter_context(H.mock_service({("GET", "/provenance"): (200, H.iar_provenance_body())}))
        # AEGIS serves ONLY the canonical real world (mirrors real candidate)
        aport, _ = stack.enter_context(H.mock_service({("GET", "/world/defensenet/opinion"): (200, H.aegis_world_body("defensenet"))}))
        gport, _ = stack.enter_context(H.mock_service({("POST", "/v1/evaluate"): self._governance_route()}))
        xport, _ = stack.enter_context(H.mock_service({("GET", "/health"): (200, H.axon_identity_body())}))
        from recipe_engine.live_adapters.settings import RepoRoot
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
        )

    def test_canonical_world_canary_green_no_fallback_no_mutation(self):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            s = self._all_live(stack, Path(td))
            r = run_live_readonly_canary(s)  # default world => defensenet
            self.assertEqual(r.verdict, "GREEN", r.hold_reason)
            self.assertEqual([o.organ for o in r.observations], list(ORGAN_ORDER))
            aegis_obs = next(o for o in r.observations if o.organ == "aegis")
            self.assertEqual(aegis_obs.status, "success")
            self.assertFalse(r.fixture_fallback_used)
            self.assertFalse(r.mutation_observed)

    def test_mock_world_holds_404_in_full_stack_if_forced_at_adapter(self):
        # If AEGIS is only serving real worlds, requesting the mock world 404s.
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            s = self._all_live(stack, Path(td))
            # forcing the mock world is rejected fail-closed BEFORE the run
            with self.assertRaises(AegisWorldError):
                run_live_readonly_canary(s, aegis_world="neuruh_status")


if __name__ == "__main__":
    unittest.main()
