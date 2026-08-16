"""P2-B live read-only adapter suite — deterministic + adversarial.

Real loopback I/O (threaded 127.0.0.1 server + temp git repos).  Covers: per
adapter live success, adversarial endpoint/path, timeout, malformed response,
provenance mismatch, live-to-fixture fallback denial, mutation-shaped input
denial, deterministic replay, and the Qdrant host-recovery gate (sealed + open).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.base import AdapterMode
from recipe_engine.founder_runtime.adapters.config import AdapterConfig, AdapterBinding
from recipe_engine.founder_runtime.adapters.envelope import validate_payload_hash
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, GovernanceAction
from recipe_engine.founder_runtime.adapters.contract_validation import (
    validate_axon_governed_evidence, validate_qdrant_verification,
)
from recipe_engine.founder_runtime.adapters.registry import AdapterRegistry
from recipe_engine.founder_runtime.contracts.authority import AuthorityClass
from recipe_engine.founder_runtime.errors import ConfigurationMissing, LiveFallbackForbidden

from recipe_engine.live_adapters.settings import LiveAdapterSettings, LiveConfigError, LiveEndpoint, RepoRoot
from recipe_engine.live_adapters.host_recovery_gate import HostRecoveryGate
from recipe_engine.live_adapters.registration import build_live_registry, register_live_adapters, live_config
from recipe_engine.live_adapters.live_github import LiveGitHubAdapter
from recipe_engine.live_adapters.live_vault import LiveVaultAdapter
from recipe_engine.live_adapters.live_qdrant import LiveQdrantAdapter
from recipe_engine.live_adapters.live_mother import LiveMotherAdapter
from recipe_engine.live_adapters.live_iar import LiveIARAdapter
from recipe_engine.live_adapters.live_aegis import LiveAegisAdapter
from recipe_engine.live_adapters.live_governance import LiveGovernanceAdapter
from recipe_engine.live_adapters.live_axon import LiveAxonReadonlyAdapter

from tests.founder_runtime import p2b_harness as H

META = AdapterRequestMeta(correlation_id="p2b", observed_at="2026-07-21T00:00:00.000Z",
                          mission_id="m1", plan_id="pl1", step_id="s1")


def _meta(**kw):
    base = dict(correlation_id="p2b", observed_at="2026-07-21T00:00:00.000Z",
               mission_id="m1", plan_id="pl1", step_id="s1")
    base.update(kw)
    return AdapterRequestMeta(**base)


# ===================================================================== GitHub
class GitHubLiveTests(unittest.TestCase):
    def test_live_success_and_redaction(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "r"
            head = H.make_git_repo(repo, origin="https://user:secret@github.com/NeuruhAI/x.git")
            s = H.settings_with(repo_roots=(RepoRoot("NeuruhAI/x", str(repo)),))
            a = LiveGitHubAdapter(s)
            res = a.repo_state_result("NeuruhAI/x", META)
            self.assertEqual(res.status, "success")
            self.assertEqual(res.adapter_mode, "live")
            self.assertFalse(res.fallback_used)
            self.assertEqual(res.payload["head_sha"], head)
            self.assertTrue(res.payload["clean"])
            self.assertNotIn("secret", res.payload["origin"])
            self.assertIn("***@", res.payload["origin"])
            self.assertTrue(validate_payload_hash(res))

    def test_non_allowlisted_repo_denied_no_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "r"
            H.make_git_repo(repo)
            s = H.settings_with(repo_roots=(RepoRoot("NeuruhAI/x", str(repo)),))
            res = LiveGitHubAdapter(s).repo_state_result("NeuruhAI/not-allowed", META)
            self.assertEqual(res.status, "denied")
            self.assertFalse(res.fallback_used)

    def test_dirty_and_active_merge_detected(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "r"
            H.make_git_repo(repo, dirty=True)
            s = H.settings_with(repo_roots=(RepoRoot("NeuruhAI/x", str(repo)),))
            res = LiveGitHubAdapter(s).repo_state_result("NeuruhAI/x", META)
            self.assertFalse(res.payload["clean"])
            self.assertGreaterEqual(res.payload["dirty_count"], 1)

    def test_replay_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "r"
            H.make_git_repo(repo)
            s = H.settings_with(repo_roots=(RepoRoot("NeuruhAI/x", str(repo)),))
            a = LiveGitHubAdapter(s)
            r1 = a.repo_state_result("NeuruhAI/x", META)
            r2 = a.repo_state_result("NeuruhAI/x", META)
            self.assertEqual(r1.payload_hash, r2.payload_hash)


# ====================================================================== Vault
class VaultLiveTests(unittest.TestCase):
    def _roots(self, td, *, same=True, runtime_dirty=False):
        src = Path(td) / "src"
        run = Path(td) / "run"
        h = H.make_git_repo(src)
        if same:
            # clone-like: identical single commit => same sha
            H.make_git_repo(run)
        else:
            H.make_git_repo(run, extra_commit=True, dirty=runtime_dirty)
        return (RepoRoot("vault-src", str(src), role="vault_source"),
                RepoRoot("vault-run", str(run), role="vault_runtime"))

    def test_live_dual_root_success(self):
        with tempfile.TemporaryDirectory() as td:
            s = H.settings_with(repo_roots=self._roots(td, same=False, runtime_dirty=True))
            res = LiveVaultAdapter(s).provenance_state(_meta())
            self.assertEqual(res.status, "success")
            self.assertEqual(res.adapter_mode, "live")
            self.assertIn(res.payload["divergence_state"], ("diverged", "diverged_runtime_ahead_dirty", "matched"))
            self.assertFalse(res.payload["approved_document_availability"]["content_exposed"])
            self.assertTrue(validate_payload_hash(res))

    def test_document_content_denied(self):
        with tempfile.TemporaryDirectory() as td:
            s = H.settings_with(repo_roots=self._roots(td))
            with self.assertRaises(Exception):
                LiveVaultAdapter(s).get_document("any", _meta())

    def test_replay_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            s = H.settings_with(repo_roots=self._roots(td, same=False))
            a = LiveVaultAdapter(s)
            self.assertEqual(a.provenance_state(_meta()).payload_hash, a.provenance_state(_meta()).payload_hash)


# ===================================================================== Qdrant
class QdrantGateTests(unittest.TestCase):
    def test_gate_sealed_denies_without_contact(self):
        # base with only a HOLD audit => gate sealed => denied without contact
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), root_name=H.ROOT_LEGACY, ts="20260721T000000Z", verdict="HOLD", aliases="legacy", io_clear=False)
            ep = H.endpoint("qdrant", 1)  # unreachable port; must NOT be contacted
            s = H.settings_with(endpoints=(ep,), host_recovery_audit_base=str(td))
            a = LiveQdrantAdapter(s, http=H.http_client(ep))
            res = a.verify_collection(_meta())
            self.assertEqual(res.status, "denied")
            self.assertEqual(res.error_code, "DEPENDENCY_UNHEALTHY")
            self.assertFalse(res.payload["live_contacted"])
            self.assertFalse(res.payload["fixture_substituted"])

    def test_gate_open_live_verification_at_ratified_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td))  # GREEN final
            with H.mock_service(H.qdrant_routes()) as (port, _srv):
                ep = H.endpoint("qdrant", port)
                s = H.settings_with(endpoints=(ep,), host_recovery_audit_base=str(td))
                a = LiveQdrantAdapter(s, http=H.http_client(ep))
                self.assertTrue(a.gate_decision().open)
                res = a.verify_collection(_meta())
                self.assertEqual(res.status, "success", res.payload)
                self.assertEqual(res.payload["count_before"], H.QDRANT_TEST_COUNT)
                self.assertEqual(res.payload["count_after"], H.QDRANT_TEST_COUNT)
                self.assertFalse(res.payload["mutation_attempted"])
                # The pin binds to retrieval identity, not just a name.
                self.assertEqual(res.payload["embedding_model"], "nomic-embed-text")
                self.assertEqual(res.payload["vector_size"], 768)
                self.assertEqual(res.payload["distance"], "Cosine")
                self.assertEqual(res.payload["source_tree"], "neuruh-vault")

    def test_gate_open_count_drift_malformed(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td))
            with H.mock_service(H.qdrant_routes(count=H.QDRANT_TEST_COUNT, count_after=H.QDRANT_TEST_COUNT - 1)) as (port, _srv):
                ep = H.endpoint("qdrant", port)
                s = H.settings_with(endpoints=(ep,), host_recovery_audit_base=str(td))
                res = LiveQdrantAdapter(s, http=H.http_client(ep)).verify_collection(_meta())
                self.assertEqual(res.status, "malformed")

    def test_gate_open_wrong_count_baseline_malformed(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td))
            with H.mock_service(H.qdrant_routes(count=99)) as (port, _srv):
                ep = H.endpoint("qdrant", port)
                s = H.settings_with(endpoints=(ep,), host_recovery_audit_base=str(td))
                res = LiveQdrantAdapter(s, http=H.http_client(ep)).verify_collection(_meta())
                self.assertEqual(res.status, "malformed")

    def test_search_gated(self):
        s = H.settings_with(endpoints=(H.endpoint("qdrant", 1),))
        res = LiveQdrantAdapter(s, http=H.http_client(H.endpoint("qdrant", 1))).search_result("q", meta=_meta())
        self.assertNotEqual(res.status, "success")


# ===================================================================== Mother
class MotherLiveTests(unittest.TestCase):
    def test_unavailable_when_down(self):
        ep = H.endpoint("mother", 1)  # nothing listening
        s = H.settings_with(endpoints=(ep,))
        res = LiveMotherAdapter(s, http=H.http_client(ep)).health_result(observed_at=META.observed_at, meta=META)
        self.assertEqual(res.status, "unavailable")
        self.assertFalse(res.fallback_used)

    def test_health_live_success(self):
        routes = {("GET", "/health"): (200, {"overall": "healthy", "dependencies": [{"name": "iar", "status": "healthy"}], "blocked": []})}
        with H.mock_service(routes) as (port, _srv):
            ep = H.endpoint("mother", port)
            s = H.settings_with(endpoints=(ep,))
            res = LiveMotherAdapter(s, http=H.http_client(ep)).health_result(observed_at=META.observed_at, meta=META)
            self.assertEqual(res.status, "success")
            self.assertEqual(res.payload["overall"], "healthy")

    def test_equivalence_receipt_valid(self):
        s = H.settings_with(endpoints=(H.endpoint("mother", 1),))
        res = LiveMotherAdapter(s, http=H.http_client(H.endpoint("mother", 1))).read_equivalence_receipt(H.mother_equivalence_body(), META)
        self.assertEqual(res.status, "success")
        self.assertEqual(res.payload["mutation_path"], "MUTATION_PATH_NOT_RATIFIED")

    def test_equivalence_receipt_unratified_claim_rejected(self):
        bad = H.mother_equivalence_body()
        bad["mutation_path"] = "MUTATION_PATH_RATIFIED"
        s = H.settings_with(endpoints=(H.endpoint("mother", 1),))
        res = LiveMotherAdapter(s, http=H.http_client(H.endpoint("mother", 1))).read_equivalence_receipt(bad, META)
        self.assertIn(res.status, ("unsafe", "malformed", "failed"))


# ======================================================================== IAR
class IARLiveTests(unittest.TestCase):
    def test_health_live_success(self):
        with H.mock_service({("GET", "/health"): (200, H.iar_health_body())}) as (port, _srv):
            ep = H.endpoint("iar", port)
            res = LiveIARAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).health(META)
            self.assertEqual(res.status, "success")
            self.assertEqual(res.payload["provider_view"]["founder_runtime_openrouter_routing"], "NOT_AUTHORIZED")

    def test_health_malformed(self):
        with H.mock_service({("GET", "/health"): (200, {"unexpected": True})}) as (port, _srv):
            ep = H.endpoint("iar", port)
            res = LiveIARAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).health(META)
            self.assertEqual(res.status, "malformed")

    def test_auth_denied(self):
        with H.mock_service({("GET", "/health"): (401, {"error": "unauthorized"})}) as (port, _srv):
            ep = H.endpoint("iar", port)
            res = LiveIARAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).health(META)
            self.assertEqual(res.status, "denied")

    def test_provenance_live(self):
        with H.mock_service({("GET", "/provenance"): (200, H.iar_provenance_body())}) as (port, _srv):
            ep = H.endpoint("iar", port)
            res = LiveIARAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).verify_provenance(META)
            self.assertEqual(res.status, "success")

    def test_interpret_gated(self):
        ep = H.endpoint("iar", 1)
        res = LiveIARAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).interpret_result("hi", meta=META)
        self.assertNotEqual(res.status, "success")


# ====================================================================== AEGIS
class AegisLiveTests(unittest.TestCase):
    def test_validate_world_live(self):
        with H.mock_service({("GET", "/world/neuruh_status/opinion"): (200, H.aegis_world_body())}) as (port, _srv):
            ep = H.endpoint("aegis", port)
            res = LiveAegisAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).validate_world("neuruh_status", META)
            self.assertEqual(res.status, "success")
            self.assertFalse(res.payload["authorizes_execution"])
            self.assertFalse(res.payload["claims_governance_authority"])

    def test_path_like_world_denied(self):
        ep = H.endpoint("aegis", 1)
        res = LiveAegisAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).validate_world("../etc/passwd", META)
        self.assertIn(res.status, ("unsafe", "denied", "failed"))

    def test_authority_claim_rejected(self):
        body = H.aegis_world_body()
        body["authorizes_execution"] = True
        with H.mock_service({("GET", "/world/w/opinion"): (200, body)}) as (port, _srv):
            ep = H.endpoint("aegis", port)
            res = LiveAegisAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).validate_world("w", META)
            self.assertIn(res.status, ("unsafe", "malformed", "failed"))


# ================================================================= Governance
class GovernanceLiveTests(unittest.TestCase):
    def _action(self):
        return GovernanceAction(action_id="act-1", actor="axon", tool="observe", domain="status", summary="read")

    def test_health_identity(self):
        with H.mock_service({("GET", "/health"): (200, {"ok": True, "service": "autonomous-governance-core"})}) as (port, _srv):
            ep = H.endpoint("governance", port)
            res = LiveGovernanceAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).health(META)
            self.assertEqual(res.status, "success")
            self.assertEqual(res.payload["execution_marker"], "NOT_CLAIMED_BY_GOVERNANCE")

    def test_wrong_service_identity_malformed(self):
        with H.mock_service({("GET", "/health"): (200, {"ok": True, "service": "impostor"})}) as (port, _srv):
            ep = H.endpoint("governance", port)
            res = LiveGovernanceAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).health(META)
            self.assertEqual(res.status, "malformed")

    def test_decide_observation_only(self):
        action = self._action()

        def route(_body):
            return (200, H.governance_decision_body(action.to_dict()))

        with H.mock_service({("POST", "/v1/evaluate"): route}) as (port, _srv):
            ep = H.endpoint("governance", port)
            res = LiveGovernanceAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).decide(
                action, trusted_authority_class=AuthorityClass.A0, trusted_policy_version="v1", meta=META)
            self.assertEqual(res.status, "success")
            self.assertEqual(res.payload["decision"], "allow")
            self.assertFalse(res.payload["mutation_authority_consumed"])
            self.assertEqual(res.payload["execution_marker"], "NOT_CLAIMED_BY_GOVERNANCE")

    def test_caller_policy_override_rejected(self):
        action = self._action()
        body = H.governance_decision_body(action.to_dict())
        body["action"]["policy"] = {"caller": "override"}

        with H.mock_service({("POST", "/v1/evaluate"): (200, body)}) as (port, _srv):
            ep = H.endpoint("governance", port)
            res = LiveGovernanceAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).decide(
                action, trusted_authority_class=AuthorityClass.A0, trusted_policy_version="v1", meta=META)
            self.assertIn(res.status, ("unsafe", "malformed", "failed"))


# ======================================================================= AXON
class AxonLiveTests(unittest.TestCase):
    def test_identity_version_pinned(self):
        with H.mock_service({("GET", "/health"): (200, H.axon_identity_body())}) as (port, _srv):
            ep = H.endpoint("axon", port)
            res = LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).identity(META)
            self.assertEqual(res.status, "success")
            self.assertEqual(res.payload["version"], "2.4.0-p1.1")

    def test_tools_census_subset_only(self):
        with H.mock_service({("GET", "/api/tools"): (200, {"tools": ["file_read", "web_fetch"]})}) as (port, _srv):
            ep = H.endpoint("axon", port)
            res = LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).tools_census(META)
            self.assertEqual(res.status, "success")
            self.assertFalse(res.payload["raw_bash_available"])
            self.assertFalse(res.payload["mutation_executor_reachable"])

    def test_tools_census_unsafe_toolset_rejected(self):
        with H.mock_service({("GET", "/api/tools"): (200, {"tools": ["file_read", "run_command", "bash"]})}) as (port, _srv):
            ep = H.endpoint("axon", port)
            res = LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).tools_census(META)
            self.assertIn(res.status, ("unsafe", "malformed", "failed"))

    def test_governed_read_receipt_disclosed(self):
        with H.mock_service({("POST", "/api/tasks/execute"): (200, H.axon_governed_read_body())}) as (port, _srv):
            ep = H.endpoint("axon", port, auth_header="x-axon-secret", auth_secret="k")
            a = LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep))
            res = a.governed_read("axon_readme", META)
            self.assertEqual(res.status, "success")
            self.assertEqual(res.write_class, "operational_metadata")
            self.assertEqual(len(res.upstream_writes), 1)
            self.assertEqual(res.payload["mutation"], False)
            validate_axon_governed_evidence(res, expected_path="README.md")

    def test_governed_read_path_not_allowlisted(self):
        ep = H.endpoint("axon", 1)
        res = LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).governed_read("../etc/passwd", META)
        self.assertIn(res.status, ("unsafe", "denied", "failed"))

    def test_traversal_probe_rejected(self):
        with H.mock_service({("POST", "/api/tasks/execute"): (400, {"ok": False, "error_codes": ["E_TRAVERSAL"]})}) as (port, _srv):
            ep = H.endpoint("axon", port)
            res = LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).traversal_probe(META)
            self.assertEqual(res.status, "success")
            self.assertEqual(res.payload["error_code"], "E_TRAVERSAL")

    def test_auth_fail_closed(self):
        with H.mock_service({("GET", "/health"): (401, {"error": "Unauthorized"})}) as (port, _srv):
            ep = H.endpoint("axon", port)  # no secret configured
            res = LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).identity(META)
            self.assertEqual(res.status, "denied")

    def test_mutation_probe_denied_is_success_condition(self):
        with H.mock_service({("POST", "/api/tasks/execute"): (403, {"error": "MUTATION_NOT_AUTHORIZED"})}) as (port, _srv):
            ep = H.endpoint("axon", port)
            res = LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).mutation_probe(META)
            self.assertEqual(res.status, "denied")
            self.assertEqual(res.error_code, "MUTATION_NOT_AUTHORIZED")
            self.assertTrue(res.payload["mutation_denied"])

    def test_mutation_probe_unreachable_still_denied(self):
        ep = H.endpoint("axon", 1)  # down
        res = LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).mutation_probe(META)
        self.assertEqual(res.status, "denied")


# ====================================================== adversarial transport
class AdversarialTransportTests(unittest.TestCase):
    def test_non_loopback_endpoint_rejected(self):
        with self.assertRaises(LiveConfigError):
            LiveEndpoint("x", "8.8.8.8", 80, "loopback")

    def test_https_scheme_rejected(self):
        with self.assertRaises(LiveConfigError):
            LiveEndpoint("x", "127.0.0.1", 443, "loopback", scheme="https")

    def test_endpoint_injection_via_path_denied(self):
        from recipe_engine.live_adapters.transport import LoopbackHttpClient, TransportDenied
        c = H.http_client(H.endpoint("iar", 8090))
        with self.assertRaises(TransportDenied):
            c.request("iar", "http://evil.example/x", timeout_ms=100)
        with self.assertRaises(TransportDenied):
            c.request("iar", "//evil.example/x", timeout_ms=100)

    def test_unknown_service_denied(self):
        from recipe_engine.live_adapters.transport import TransportDenied
        c = H.http_client(H.endpoint("iar", 8090))
        with self.assertRaises(TransportDenied):
            c.request("qdrant", "/x", timeout_ms=100)

    def test_git_write_subcommand_denied(self):
        from recipe_engine.live_adapters.transport import GitObserver, TransportDenied
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "r"
            H.make_git_repo(repo)
            g = GitObserver({"r": str(repo)})
            for bad in (["commit", "-m", "x"], ["push"], ["checkout", "main"], ["reset", "--hard"], ["clean", "-fd"], ["gc"]):
                with self.assertRaises(TransportDenied):
                    g.run("r", bad, timeout_ms=1000)


# =============================================================== timeout tests
class TimeoutTests(unittest.TestCase):
    def test_http_timeout_classified(self):
        with H.mock_service({("GET", "/health"): (200, H.iar_health_body())}, delay=1.0) as (port, _srv):
            ep = H.endpoint("iar", port)
            res = LiveIARAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).health(_meta(timeout_ms=150))
            self.assertEqual(res.status, "timeout")
            self.assertFalse(res.fallback_used)


# =================================================== live-to-fixture fallback
class NoFallbackTests(unittest.TestCase):
    def test_result_fallback_always_false(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "r"
            H.make_git_repo(repo)
            s = H.settings_with(repo_roots=(RepoRoot("NeuruhAI/x", str(repo)),))
            res = LiveGitHubAdapter(s).repo_state_result("NeuruhAI/x", META)
            self.assertFalse(res.fallback_used)

    def test_registry_live_cannot_downgrade_to_fixture(self):
        s = H.settings_with(repo_roots=(RepoRoot("NeuruhAI/x", "/tmp"),), endpoints=())
        reg = build_live_registry(s)
        with self.assertRaises(LiveFallbackForbidden):
            reg.resolve("github", mode=AdapterMode.FIXTURE)  # binding is live; caller override rejected

    def test_live_requires_allow_live(self):
        # a registry whose config forbids live must refuse live resolution
        cfg = AdapterConfig(bindings=(AdapterBinding("github", AdapterMode.LIVE, "live"),), allow_live=False)
        reg = AdapterRegistry(cfg)
        register_live_adapters(reg, H.settings_with(repo_roots=(RepoRoot("NeuruhAI/x", "/tmp"),)))
        with self.assertRaises(ConfigurationMissing):
            reg.resolve("github", mode=AdapterMode.LIVE)


# ============================================== mutation-shaped input denial
class MutationShapedTests(unittest.TestCase):
    def test_reject_mutation_shaped_keys(self):
        from recipe_engine.live_adapters.base_live import reject_mutation_shaped, MutationShapedInput
        for bad in ({"write": 1}, {"delete": True}, {"upsert": []}, {"content": "x"}, {"approved": True}, {"force": 1}):
            with self.assertRaises(MutationShapedInput):
                reject_mutation_shaped(bad, context="t")

    def test_mother_equivalence_rejects_mutation_shaped(self):
        s = H.settings_with(endpoints=(H.endpoint("mother", 1),))
        res = LiveMotherAdapter(s, http=H.http_client(H.endpoint("mother", 1))).read_equivalence_receipt({"write": True}, META)
        self.assertEqual(res.status, "denied")
        self.assertEqual(res.error_code, "MUTATION_NOT_AUTHORIZED")


if __name__ == "__main__":
    unittest.main()
