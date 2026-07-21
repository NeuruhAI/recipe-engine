"""P2-B canonical organ-topology reconciliation tests.

Governance required-key-subset health validation; canonical fixed endpoint
allowlist (Qdrant 6333, IAR 8090, Governance 8848, AXON 8300) with obsolete-port
and override rejection; non-loopback rejection; promoted AXON/Governance SHA
pinning; and the reviewed Mother/AEGIS endpoints from their sealed GREEN audits.
No fixture fallback; no mutation; eight-organ order unchanged.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.contract_validation import validate_governance_health
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, GovernanceAction
from recipe_engine.founder_runtime.contracts.authority import AuthorityClass
from recipe_engine.founder_runtime.errors import MalformedResponse
from recipe_engine.live_adapters.canonical_topology import (
    CANONICAL_AXON_SHA,
    CANONICAL_AXON_VERSION,
    CANONICAL_GOVERNANCE_SHA,
    CANONICAL_ORGAN_ORDER,
    CANONICAL_PORTS,
    OBSOLETE_AXON_PORTS,
    TopologyError,
    build_canonical_topology,
    read_endpoint_port_from_audit,
    verify_canonical_topology,
)
from recipe_engine.live_adapters.canary import ORGAN_ORDER
from recipe_engine.live_adapters.live_axon import LiveAxonReadonlyAdapter
from recipe_engine.live_adapters.live_governance import LiveGovernanceAdapter
from recipe_engine.live_adapters.settings import LiveConfigError, LiveEndpoint, RepoRoot

from tests.founder_runtime import p2b_harness as H

META = AdapterRequestMeta(correlation_id="topo", observed_at="2026-07-21T00:00:00.000Z")
MOTHER_AUDIT_ROOT = os.path.expanduser("~/neuruh-workspace/audit-output/mother-p2b-readonly-http")
AEGIS_AUDIT_ROOT = os.path.expanduser("~/neuruh-workspace/audit-output/aegis-p2b-readonly-opinion-http")


def _newest_audit(root: str):
    if not os.path.isdir(root):
        return None
    subs = sorted((d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))), reverse=True)
    for name in subs:
        audit = os.path.join(root, name)
        if os.path.isfile(os.path.join(audit, "FINAL_VERDICT.md")):
            return audit
    return None


def _topology(mother_port=8077, aegis_port=8055, **kw):
    return build_canonical_topology(
        repo_roots=(), mother_port=mother_port, aegis_port=aegis_port, **kw
    )


# ============================================================ Governance subset
class GovernanceHealthSubsetTests(unittest.TestCase):
    def test_required_keys_only_ok(self):
        v = validate_governance_health({"ok": True, "service": "autonomous-governance-core"})
        self.assertEqual(v, {"ok": True, "service": "autonomous-governance-core"})

    def test_required_keys_plus_safe_extra_metadata_ok(self):
        v = validate_governance_health({
            "ok": True, "service": "autonomous-governance-core",
            "version": "3.1.0", "uptime_s": 4242, "policy_version": "up2.v1", "extra": {"x": 1},
        })
        # extra metadata is permitted but NOT carried forward as authority
        self.assertEqual(v, {"ok": True, "service": "autonomous-governance-core"})

    def test_missing_required_key_fails_closed(self):
        with self.assertRaises(MalformedResponse):
            validate_governance_health({"service": "autonomous-governance-core"})
        with self.assertRaises(MalformedResponse):
            validate_governance_health({"ok": True})

    def test_wrong_value_or_type_fails_closed(self):
        with self.assertRaises(MalformedResponse):
            validate_governance_health({"ok": "yes", "service": "autonomous-governance-core"})
        with self.assertRaises(MalformedResponse):
            validate_governance_health({"ok": False, "service": "autonomous-governance-core"})
        with self.assertRaises(MalformedResponse):
            validate_governance_health({"ok": True, "service": "impostor-core"})

    def test_live_governance_health_accepts_extra_metadata(self):
        body = {"ok": True, "service": "autonomous-governance-core", "version": "3.1", "uptime_s": 10}
        with H.mock_service({("GET", "/health"): (200, body)}) as (port, _srv):
            ep = H.endpoint("governance", port)
            res = LiveGovernanceAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).health(META)
            self.assertEqual(res.status, "success")
            self.assertEqual(res.payload["execution_marker"], "NOT_CLAIMED_BY_GOVERNANCE")

    def test_live_governance_health_rejects_wrong_service_with_extra(self):
        body = {"ok": True, "service": "impostor", "version": "3.1"}
        with H.mock_service({("GET", "/health"): (200, body)}) as (port, _srv):
            ep = H.endpoint("governance", port)
            res = LiveGovernanceAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).health(META)
            self.assertEqual(res.status, "malformed")


# ============================================================ Canonical topology
class CanonicalTopologyTests(unittest.TestCase):
    def test_canonical_ports_pinned(self):
        s = _topology()
        eps = s.endpoint_map()
        self.assertEqual(eps["qdrant"].port, 6333)
        self.assertEqual(eps["iar"].port, 8090)
        self.assertEqual(eps["governance"].port, 8848)
        self.assertEqual(eps["axon"].port, 8300)

    def test_eight_organ_order_unchanged(self):
        self.assertEqual(CANONICAL_ORGAN_ORDER, ORGAN_ORDER)
        self.assertEqual(len(ORGAN_ORDER), 8)

    def test_canonical_axon_gateway_port_is_8300(self):
        self.assertEqual(CANONICAL_PORTS["axon"], 8300)
        self.assertIn(8080, OBSOLETE_AXON_PORTS)

    def test_obsolete_axon_port_rejected(self):
        s = _topology()
        eps = list(s.endpoints)
        # replace axon with the obsolete 8080
        eps = tuple(LiveEndpoint(e.service_id, e.host, 8080 if e.service_id == "axon" else e.port, e.endpoint_class) for e in eps)
        bad = H.settings_with(endpoints=eps, axon_source_sha=CANONICAL_AXON_SHA, governance_source_sha=CANONICAL_GOVERNANCE_SHA)
        with self.assertRaises(TopologyError):
            verify_canonical_topology(bad)

    def test_endpoint_override_rejected(self):
        # a caller pinning IAR to a non-canonical port is rejected
        eps = list(_topology().endpoints)
        eps = tuple(LiveEndpoint(e.service_id, e.host, 9999 if e.service_id == "iar" else e.port, e.endpoint_class) for e in eps)
        bad = H.settings_with(endpoints=eps, axon_source_sha=CANONICAL_AXON_SHA, governance_source_sha=CANONICAL_GOVERNANCE_SHA)
        with self.assertRaises(TopologyError):
            verify_canonical_topology(bad)

    def test_non_loopback_endpoint_rejected(self):
        with self.assertRaises(LiveConfigError):
            LiveEndpoint("governance", "10.0.0.5", 8848, "loopback")

    def test_wrong_axon_sha_rejected(self):
        eps = _topology().endpoints
        bad = H.settings_with(endpoints=eps, axon_source_sha="deadbeef" * 5, governance_source_sha=CANONICAL_GOVERNANCE_SHA)
        with self.assertRaises(TopologyError):
            verify_canonical_topology(bad)

    def test_wrong_governance_sha_rejected(self):
        eps = _topology().endpoints
        bad = H.settings_with(endpoints=eps, axon_source_sha=CANONICAL_AXON_SHA, governance_source_sha="0" * 40)
        with self.assertRaises(TopologyError):
            verify_canonical_topology(bad)

    def test_builder_pins_promoted_shas(self):
        s = _topology()
        self.assertEqual(s.axon_source_sha, CANONICAL_AXON_SHA)
        self.assertEqual(s.governance_source_sha, CANONICAL_GOVERNANCE_SHA)


# ============================================================ AXON identity/SHA
class AxonCanonicalTests(unittest.TestCase):
    def test_axon_identity_promoted_version_ok(self):
        with H.mock_service({("GET", "/health"): (200, H.axon_identity_body())}) as (port, _srv):
            ep = H.endpoint("axon", port)
            s = H.settings_with(endpoints=(ep,), axon_source_sha=CANONICAL_AXON_SHA)
            res = LiveAxonReadonlyAdapter(s, http=H.http_client(ep)).identity(META)
            self.assertEqual(res.status, "success")
            self.assertEqual(res.payload["version"], CANONICAL_AXON_VERSION)

    def test_axon_wrong_sha_rejected_before_request(self):
        # a wrong pinned SHA is rejected without contacting the service
        ep = H.endpoint("axon", 1)  # unreachable; must not be needed
        s = H.settings_with(endpoints=(ep,), axon_source_sha="cafebabe" * 5)
        res = LiveAxonReadonlyAdapter(s, http=H.http_client(ep)).identity(META)
        self.assertNotEqual(res.status, "success")
        self.assertEqual(res.error_code, "PROVENANCE_INVALID")

    def test_axon_wrong_version_rejected(self):
        body = dict(H.axon_identity_body()); body["version"] = "9.9.9-bad"
        with H.mock_service({("GET", "/health"): (200, body)}) as (port, _srv):
            ep = H.endpoint("axon", port)
            s = H.settings_with(endpoints=(ep,), axon_source_sha=CANONICAL_AXON_SHA)
            res = LiveAxonReadonlyAdapter(s, http=H.http_client(ep)).identity(META)
            self.assertNotEqual(res.status, "success")

    def test_axon_mutation_absent(self):
        with H.mock_service({("POST", "/api/tasks/execute"): (403, {"error": "MUTATION_NOT_AUTHORIZED"})}) as (port, _srv):
            ep = H.endpoint("axon", port)
            s = H.settings_with(endpoints=(ep,), axon_source_sha=CANONICAL_AXON_SHA)
            res = LiveAxonReadonlyAdapter(s, http=H.http_client(ep)).mutation_probe(META)
            self.assertEqual(res.status, "denied")
            self.assertEqual(res.error_code, "MUTATION_NOT_AUTHORIZED")


# ============================================================ Reviewed Mother/AEGIS
class MotherAegisEndpointTests(unittest.TestCase):
    @unittest.skipUnless(_newest_audit(MOTHER_AUDIT_ROOT), "Mother GREEN audit not present")
    def test_canonical_mother_endpoint_from_audit(self):
        audit = _newest_audit(MOTHER_AUDIT_ROOT)
        port = read_endpoint_port_from_audit(audit, "mother")
        self.assertTrue(0 < port < 65536)
        s = _topology(mother_port=port)
        self.assertEqual(s.endpoint("mother").port, port)
        verify_canonical_topology(s)  # must not raise

    @unittest.skipUnless(_newest_audit(AEGIS_AUDIT_ROOT), "AEGIS GREEN audit not present")
    def test_canonical_aegis_endpoint_from_audit(self):
        audit = _newest_audit(AEGIS_AUDIT_ROOT)
        port = read_endpoint_port_from_audit(audit, "aegis")
        self.assertTrue(0 < port < 65536)
        s = _topology(aegis_port=port)
        self.assertEqual(s.endpoint("aegis").port, port)
        verify_canonical_topology(s)


# ============================================================ No-fallback invariant
class NoFallbackNoMutationTopologyTests(unittest.TestCase):
    def test_topology_has_no_fixture_fallback_flag(self):
        s = _topology()
        self.assertTrue(s.allow_live)
        # every endpoint is loopback; none is fixture
        for e in s.endpoints:
            self.assertIn(e.host, ("127.0.0.1", "::1", "localhost"))

    def test_governance_decide_observation_only(self):
        action = GovernanceAction(action_id="a1", actor="fr", tool="observe", domain="status", summary="read")

        def route(body: bytes):
            import json
            a = json.loads(body or b"{}").get("action", {})
            return (200, H.governance_decision_body(a))

        with H.mock_service({("POST", "/v1/evaluate"): route}) as (port, _srv):
            ep = H.endpoint("governance", port)
            res = LiveGovernanceAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).decide(
                action, trusted_authority_class=AuthorityClass.A0, trusted_policy_version="v1", meta=META)
            self.assertEqual(res.status, "success")
            self.assertFalse(res.payload["mutation_authority_consumed"])


if __name__ == "__main__":
    unittest.main()
