"""P2-A adapter foundation tests — modes, registry, envelopes, hard stop."""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.base import (
    AdapterMode, normalize_mode, require_fixture_mode, ALL_ADAPTER_MODES,
)
from recipe_engine.founder_runtime.adapters.envelope import (
    make_result, AdapterResult, AdapterResultStatus, SCHEMA_ADAPTER_RESULT,
)
from recipe_engine.founder_runtime.adapters.identity import (
    fixture_identity, fixture_dependency, vault_provenance,
    DivergenceState, EvidenceClass, VAULT_SOURCE_REPO, VAULT_RUNTIME_CHECKOUT,
    VersionMismatch, ProvenanceInvalid, IdentityMismatch,
)
from recipe_engine.founder_runtime.adapters.config import (
    AdapterConfig, AdapterBinding, fixture_only_config,
)
from recipe_engine.founder_runtime.adapters.registry import (
    AdapterRegistry, default_registry, ADAPTER_TYPES,
)
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.adapters.axon_mutation import (
    AxonMutationHardStop, mutation_authorized, MUTATION_GATES,
)
from recipe_engine.founder_runtime.adapters.stubs import ModeStubAdapter
from recipe_engine.founder_runtime.adapters.fixture_mother import FixtureMother
from recipe_engine.founder_runtime.adapters.fixture_iar import FixtureIAR
from recipe_engine.founder_runtime.errors import (
    AdapterModeInvalid, ConfigurationMissing, LiveFallbackForbidden,
    MutationNotAuthorized, AdapterDisabled, AdapterUnavailable,
)


class AdapterModeTests(unittest.TestCase):
    def test_all_modes_defined(self):
        self.assertEqual(ALL_ADAPTER_MODES, {"fixture", "live", "disabled", "unavailable"})

    def test_normalize(self):
        self.assertEqual(normalize_mode("live"), AdapterMode.LIVE)
        with self.assertRaises(AdapterModeInvalid):
            normalize_mode("prod")

    def test_fixture_requires_fixture_mode(self):
        require_fixture_mode("fixture", "x")
        for m in ("live", "disabled", "unavailable"):
            with self.assertRaises(AdapterModeInvalid):
                require_fixture_mode(m, "x")


class EnvelopeTests(unittest.TestCase):
    def _meta(self):
        return AdapterRequestMeta(correlation_id="c1", causation_id="cause1", observed_at="2026-07-20T00:00:00.000Z")

    def test_success_envelope(self):
        r = make_result(
            adapter_name="mother", adapter_version="v1", adapter_mode=AdapterMode.FIXTURE,
            operation="health", status=AdapterResultStatus.SUCCESS,
            observed_at="t", correlation_id="c", authority_class="A0",
            dependency_identity={"s": "m"}, source_identity={"n": "mother"},
            source_version_or_sha="fixture", provenance={"e": "fixture"},
            payload={"ok": True},
        )
        self.assertEqual(r.schema_version, SCHEMA_ADAPTER_RESULT)
        self.assertFalse(r.fallback_used)
        self.assertTrue(r.payload_hash)
        d = r.to_dict()
        self.assertEqual(d["status"], "success")

    def test_fallback_used_rejected(self):
        with self.assertRaises(LiveFallbackForbidden):
            make_result(
                adapter_name="x", adapter_version="v", adapter_mode="fixture",
                operation="op", status="success", observed_at="t", correlation_id="c",
                authority_class="A0", dependency_identity={}, source_identity={},
                source_version_or_sha="f", provenance={}, fallback_used=True,
            )

    def test_invalid_status_rejected(self):
        with self.assertRaises(Exception):
            make_result(
                adapter_name="x", adapter_version="v", adapter_mode="fixture",
                operation="op", status="kinda_ok", observed_at="t", correlation_id="c",
                authority_class="A0", dependency_identity={}, source_identity={},
                source_version_or_sha="f", provenance={},
            )

    def test_payload_hash_stable(self):
        kw = dict(
            adapter_name="x", adapter_version="v", adapter_mode="fixture",
            operation="op", status="success", observed_at="t", correlation_id="c",
            authority_class="A0", dependency_identity={}, source_identity={},
            source_version_or_sha="f", provenance={}, payload={"b": 1, "a": 2},
        )
        r1 = make_result(**kw)
        r2 = make_result(**kw)
        self.assertEqual(r1.payload_hash, r2.payload_hash)

    def test_correlation_causation_propagated(self):
        meta = self._meta()
        m = FixtureMother()
        res = m.health_result(observed_at=meta.observed_at, meta=meta)
        self.assertEqual(res.correlation_id, "c1")
        self.assertEqual(res.causation_id, "cause1")


class IdentityProvenanceTests(unittest.TestCase):
    def test_version_mismatch(self):
        dep = fixture_dependency("svc")
        # rebuild with mismatch
        from recipe_engine.founder_runtime.adapters.identity import DependencyIdentity, SCHEMA_DEPENDENCY_IDENTITY
        d = DependencyIdentity(
            schema_version=SCHEMA_DEPENDENCY_IDENTITY,
            service_name="svc", endpoint_class="e",
            expected_version_or_sha="aaa", observed_version_or_sha="bbb",
            identity_verified=True, readiness_verified=True,
        )
        with self.assertRaises(VersionMismatch):
            d.assert_identity()

    def test_identity_not_verified(self):
        from recipe_engine.founder_runtime.adapters.identity import DependencyIdentity, SCHEMA_DEPENDENCY_IDENTITY
        d = DependencyIdentity(
            schema_version=SCHEMA_DEPENDENCY_IDENTITY,
            service_name="svc", endpoint_class="e",
            expected_version_or_sha="a", observed_version_or_sha="a",
            identity_verified=False, readiness_verified=True,
        )
        with self.assertRaises(IdentityMismatch):
            d.assert_identity()

    def test_vault_paths(self):
        self.assertIn("neuruh-vault", VAULT_SOURCE_REPO)
        self.assertEqual(VAULT_RUNTIME_CHECKOUT, "~/neuruh-vault")
        p = vault_provenance(source_sha="abc", runtime_sha="xyz", retrieval_timestamp="t")
        self.assertEqual(p.divergence_state, DivergenceState.DIVERGED)
        with self.assertRaises(ProvenanceInvalid):
            p.assert_for_sync_claim()

    def test_fixture_cannot_claim_live(self):
        ident = fixture_identity("mother")
        self.assertEqual(ident.mode, AdapterMode.FIXTURE)
        self.assertNotEqual(ident.mode, AdapterMode.LIVE)


class RegistryTests(unittest.TestCase):
    def test_resolve_fixture(self):
        reg = default_registry()
        m = reg.resolve("mother", mode=AdapterMode.FIXTURE)
        self.assertEqual(m.mode(), AdapterMode.FIXTURE)

    def test_disabled(self):
        cfg = AdapterConfig(bindings=(
            AdapterBinding("mother", AdapterMode.DISABLED, "disabled"),
        ))
        reg = AdapterRegistry(cfg)
        stub = reg.resolve("mother")
        self.assertIsInstance(stub, ModeStubAdapter)
        meta = AdapterRequestMeta(correlation_id="c", observed_at="t")
        res = stub.call("health_snapshot", meta)
        self.assertEqual(res.status, "disabled")
        with self.assertRaises(AdapterDisabled):
            stub.raise_for_mode()

    def test_unavailable(self):
        cfg = AdapterConfig(bindings=(
            AdapterBinding("github", AdapterMode.UNAVAILABLE, "unavailable"),
        ))
        reg = AdapterRegistry(cfg)
        stub = reg.resolve("github")
        res = stub.call("repo_state", AdapterRequestMeta(correlation_id="c", observed_at="t"))
        self.assertEqual(res.status, "unavailable")
        self.assertTrue(res.retryable)
        with self.assertRaises(AdapterUnavailable):
            stub.raise_for_mode()

    def test_live_requires_config(self):
        reg = default_registry()
        with self.assertRaises(ConfigurationMissing):
            reg.resolve("mother", mode=AdapterMode.LIVE)

    def test_live_missing_implementation(self):
        cfg = AdapterConfig(allow_live=True, bindings=(
            AdapterBinding("mother", AdapterMode.LIVE, "live"),
        ))
        reg = AdapterRegistry(cfg)
        with self.assertRaises(ConfigurationMissing):
            reg.resolve("mother", mode=AdapterMode.LIVE)

    def test_no_live_to_fixture_fallback(self):
        cfg = AdapterConfig(allow_live=True, bindings=(
            AdapterBinding("mother", AdapterMode.LIVE, "live"),
        ))
        reg = AdapterRegistry(cfg)
        with self.assertRaises(ConfigurationMissing) as cm:
            reg.resolve("mother", mode=AdapterMode.LIVE)
        self.assertIn("no live implementation", str(cm.exception).lower() or cm.exception.message.lower())

    def test_unknown_adapter(self):
        reg = default_registry()
        with self.assertRaises(AdapterModeInvalid):
            reg.resolve("not_a_real_adapter")

    def test_duplicate_registration(self):
        reg = default_registry()
        with self.assertRaises(AdapterModeInvalid):
            reg.register("mother", "fixture", lambda: None)

    def test_explicit_mode_required_semantics(self):
        # resolve uses binding mode from config when mode omitted
        reg = default_registry()
        m = reg.resolve("iar")
        self.assertEqual(m.mode(), AdapterMode.FIXTURE)

    def test_no_env_selection(self):
        # Even if env set, domain contracts ignore it
        with mock.patch.dict(os.environ, {"FR_ADAPTER_MODE": "live", "ADAPTER_MODE": "live"}, clear=False):
            reg = default_registry()
            m = reg.resolve("mother")
            self.assertEqual(m.mode(), AdapterMode.FIXTURE)


class FixtureRetrofitTests(unittest.TestCase):
    def test_deterministic_iar(self):
        iar = FixtureIAR()
        a = iar.interpret("Tell me where the Neuruh organism is")
        b = iar.interpret("Tell me where the Neuruh organism is")
        self.assertEqual(a["output_hash"], b["output_hash"])
        self.assertEqual(a["backend"], "fixture")
        self.assertEqual(a["model"], "fixture")

    def test_fixture_result_not_live(self):
        m = FixtureMother()
        meta = AdapterRequestMeta(correlation_id="c", observed_at="t")
        res = m.health_result(observed_at="t", meta=meta)
        self.assertEqual(res.adapter_mode, "fixture")
        self.assertNotEqual(res.adapter_mode, "live")
        self.assertFalse(res.fallback_used)
        self.assertFalse(res.mutation_attempted)

    def test_fixture_receipt_cannot_authorize_mutation(self):
        from recipe_engine.founder_runtime.adapters.fixture_axon import FixtureAxon
        ax = FixtureAxon()
        rcpt = ax.execute_readonly_step(
            mission_id="m", plan_id="p", step_id="s", capability="c",
            input_hash="i", authority_reference="a",
            started_at="t0", ended_at="t1",
        )
        self.assertFalse(getattr(rcpt, "mutation_authorized", True) is True and rcpt.mutation_authorized)
        # explicit
        self.assertFalse(rcpt.mutation_authorized)
        self.assertEqual(rcpt.evidence_class, "fixture")


class MutationHardStopTests(unittest.TestCase):
    def test_not_authorized(self):
        self.assertFalse(mutation_authorized())
        self.assertFalse(any(MUTATION_GATES.values()))

    def test_invoke_raises(self):
        stop = AxonMutationHardStop()
        with self.assertRaises(MutationNotAuthorized):
            stop.execute_mutation()

    def test_mode_cannot_enable(self):
        for m in ("fixture", "live", "disabled", "unavailable"):
            stop = AxonMutationHardStop(mode=m)
            with self.assertRaises(MutationNotAuthorized):
                stop.execute_mutation(mode=m, enable=True)

    def test_env_cannot_enable(self):
        with mock.patch.dict(os.environ, {"ENABLE_AXON_MUTATION": "1", "MUTATION_SEAL": "yes"}):
            self.assertFalse(mutation_authorized())
            with self.assertRaises(MutationNotAuthorized):
                AxonMutationHardStop().execute_mutation()

    def test_registry_override_cannot_enable(self):
        reg = default_registry()
        obj = reg.resolve("axon_mutation", mode=AdapterMode.LIVE)
        with self.assertRaises(MutationNotAuthorized):
            obj.execute_mutation(force=True, model_output={"state": "COMPLETED"})

    def test_fixture_substitution_blocked(self):
        # resolving axon_mutation never returns FixtureAxon
        reg = default_registry()
        obj = reg.resolve("axon_mutation")
        self.assertIsInstance(obj, AxonMutationHardStop)

    def test_caller_flags_ignored(self):
        with self.assertRaises(MutationNotAuthorized):
            AxonMutationHardStop().execute_mutation(
                authority_class="A0",
                approval="forged",
                adapter_mode="fixture",
            )


class ConfinementTests(unittest.TestCase):
    def test_no_network_in_new_adapter_modules(self):
        AD = ROOT / "recipe_engine/founder_runtime/adapters"
        bad = []
        for path in AD.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name.split(".")[0] in ("socket", "subprocess", "requests", "http", "urllib"):
                            bad.append((str(path), a.name))
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.split(".")[0] in ("socket", "subprocess", "requests", "http", "urllib"):
                        bad.append((str(path), node.module))
        self.assertEqual(bad, [])

    def test_no_environ_in_domain(self):
        AD = ROOT / "recipe_engine/founder_runtime/adapters"
        for path in AD.rglob("*.py"):
            text = path.read_text()
            # allow comment mentions only — reject real access
            self.assertNotIn("os.environ", text)
            self.assertNotIn("getenv(", text)


if __name__ == "__main__":
    unittest.main()
