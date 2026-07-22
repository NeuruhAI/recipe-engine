"""P2-B AXON final-gate response contract — deterministic + adversarial.

Proves the LiveAxonReadonlyAdapter identity path against the REAL promoted
gateway health shape (f3a94d1a, v2.4.0-p1.1): the reviewed endpoint-specific
schema accepts exactly the real aggregation, fails closed on every unknown or
malformed field (top-level and nested), pins SHA/version/loopback, and the
normalization boundary never carries an execution-authority-shaped claim into
the canonical identity receipt.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.adapters.contract_validation import (
    AXON_HEALTH_REQUIRED_KEYS,
    validate_axon_identity,
)
from recipe_engine.founder_runtime.errors import MalformedResponse, ProvenanceInvalid

from recipe_engine.live_adapters.canonical_topology import CANONICAL_AXON_SHA
from recipe_engine.live_adapters.live_axon import LiveAxonReadonlyAdapter
from recipe_engine.live_adapters.settings import LiveConfigError, LiveEndpoint
from recipe_engine.live_adapters.transport import TransportDenied

from tests.founder_runtime import p2b_harness as H

META = AdapterRequestMeta(correlation_id="p2b-axon-gate", observed_at="2026-07-22T00:00:00.000Z",
                          mission_id="m1", plan_id="pl1", step_id="s1")


def _adapter(port: int, **settings_kw):
    ep = H.endpoint("axon", port)
    return LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,), **settings_kw), http=H.http_client(ep))


class AxonIdentityValidatorTests(unittest.TestCase):
    """Pure-validator schema cases (no I/O)."""

    def test_real_gateway_health_shape_validates(self):
        validated = validate_axon_identity(H.axon_identity_body())
        self.assertEqual(validated["version"], "2.4.0-p1.1")
        self.assertEqual(validated["status"], "healthy")
        self.assertIs(validated["inference"]["healthy"], True)
        self.assertEqual(validated["inference"]["authority"], "iar")
        self.assertIs(validated["governance_reachable"], True)
        self.assertIsNone(validated["degraded_reason"])

    def test_normalized_receipt_carries_no_execution_authority(self):
        validated = validate_axon_identity(H.axon_identity_body())
        self.assertEqual(
            set(validated),
            {"status", "version", "inference", "governance_reachable", "degraded_reason"},
        )
        for authority_shaped in ("execution_available", "services", "bonsai",
                                 "bonsai_required", "uptime", "mutation", "execution"):
            self.assertNotIn(authority_shaped, validated)
        self.assertEqual(set(validated["inference"]), {"authority", "provider", "model", "healthy"})

    def test_normalized_receipt_is_deterministic(self):
        self.assertEqual(validate_axon_identity(H.axon_identity_body()),
                         validate_axon_identity(H.axon_identity_body()))

    def test_missing_inference_health_fails_closed(self):
        body = H.axon_identity_body()
        del body["inference"]["healthy"]
        with self.assertRaises(MalformedResponse):
            validate_axon_identity(body)

    def test_inference_health_wrong_type_fails_closed(self):
        for bad in ("yes", 1, None, {}, []):
            body = H.axon_identity_body()
            body["inference"]["healthy"] = bad
            with self.assertRaises(MalformedResponse):
                validate_axon_identity(body)

    def test_legacy_inference_health_key_fails_closed(self):
        # The old fixture key 'health' is NOT silently translated to 'healthy'.
        body = H.axon_identity_body()
        body["inference"]["health"] = body["inference"].pop("healthy")
        with self.assertRaises(MalformedResponse):
            validate_axon_identity(body)

    def test_unknown_top_level_field_fails_closed(self):
        body = H.axon_identity_body()
        body["debug_shell"] = True
        with self.assertRaises(MalformedResponse):
            validate_axon_identity(body)

    def test_unknown_nested_inference_field_fails_closed(self):
        body = H.axon_identity_body()
        body["inference"]["sampler"] = "greedy"
        with self.assertRaises(MalformedResponse):
            validate_axon_identity(body)

    def test_unknown_nested_service_field_fails_closed(self):
        body = H.axon_identity_body()
        body["services"]["shadow_executor"] = True
        with self.assertRaises(MalformedResponse):
            validate_axon_identity(body)

    def test_each_required_field_missing_fails_closed(self):
        for key in sorted(AXON_HEALTH_REQUIRED_KEYS):
            body = H.axon_identity_body()
            del body[key]
            with self.assertRaises(MalformedResponse, msg=f"missing {key} must fail closed"):
                validate_axon_identity(body)

    def test_execution_available_never_fabricated(self):
        # Absent on the wire -> fail closed (nothing synthesizes a default) ...
        body = H.axon_identity_body()
        del body["execution_available"]
        with self.assertRaises(MalformedResponse):
            validate_axon_identity(body)
        # ... wrong type -> fail closed ...
        body = H.axon_identity_body()
        body["execution_available"] = "true"
        with self.assertRaises(MalformedResponse):
            validate_axon_identity(body)
        # ... and present/valid -> observed only, never carried forward.
        self.assertNotIn("execution_available", validate_axon_identity(H.axon_identity_body()))

    def test_wrong_version_fails_closed(self):
        body = H.axon_identity_body()
        body["version"] = "9.9.9-bad"
        with self.assertRaises(ProvenanceInvalid):
            validate_axon_identity(body)

    def test_wrong_inference_authority_fails_closed(self):
        body = H.axon_identity_body()
        body["inference"]["authority"] = "ollama"
        with self.assertRaises(ProvenanceInvalid):
            validate_axon_identity(body)

    def test_foreign_service_health_shape_fails_closed(self):
        # A Governance-style health body served on the AXON port is rejected.
        with self.assertRaises(MalformedResponse):
            validate_axon_identity({"ok": True, "service": "autonomous-governance-core"})

    def test_status_vocabulary_fails_closed(self):
        body = H.axon_identity_body()
        body["status"] = "rebooting"
        with self.assertRaises(ProvenanceInvalid):
            validate_axon_identity(body)

    def test_degraded_reason_consistency(self):
        # healthy must carry a null degraded_reason ...
        body = H.axon_identity_body()
        body["degraded_reason"] = "but-healthy"
        with self.assertRaises(MalformedResponse):
            validate_axon_identity(body)
        # ... and a non-healthy status must explain itself.
        body = H.axon_identity_body()
        body["status"] = "degraded"
        with self.assertRaises(MalformedResponse):
            validate_axon_identity(body)
        body["degraded_reason"] = "iar=unreachable"
        body["status_reason"] = "iar=unreachable"
        validated = validate_axon_identity(body)
        self.assertEqual(validated["status"], "degraded")
        self.assertEqual(validated["degraded_reason"], "iar=unreachable")

    def test_inference_backend_alias_must_match_provider(self):
        body = H.axon_identity_body()
        body["inference_backend"] = "claude-cloud"
        with self.assertRaises(MalformedResponse):
            validate_axon_identity(body)

    def test_service_flag_wrong_type_fails_closed(self):
        body = H.axon_identity_body()
        body["services"]["governance"] = "up"
        with self.assertRaises(MalformedResponse):
            validate_axon_identity(body)


class AxonFinalGateAdapterTests(unittest.TestCase):
    """Adapter-level cases over real loopback I/O."""

    def test_identity_succeeds_against_real_health_shape(self):
        with H.mock_service({("GET", "/health"): (200, H.axon_identity_body())}) as (port, _srv):
            res = _adapter(port).identity(META)
            self.assertEqual(res.status, "success")
            self.assertEqual(res.payload["version"], "2.4.0-p1.1")
            self.assertNotIn("execution_available", res.payload)
            self.assertIs(res.payload["inference"]["healthy"], True)

    def test_wrong_axon_sha_fails_closed_before_any_request(self):
        with H.mock_service({("GET", "/health"): (200, H.axon_identity_body())}) as (port, srv):
            res = _adapter(port, axon_source_sha="deadbeef" * 5).identity(META)
            self.assertNotEqual(res.status, "success")
            self.assertEqual(res.error_code, "PROVENANCE_INVALID")
            self.assertEqual(srv.received, [])  # rejected before the wire

    def test_pinned_axon_sha_succeeds(self):
        with H.mock_service({("GET", "/health"): (200, H.axon_identity_body())}) as (port, _srv):
            res = _adapter(port, axon_source_sha=CANONICAL_AXON_SHA).identity(META)
            self.assertEqual(res.status, "success")

    def test_unknown_top_level_field_fails_closed_via_adapter(self):
        body = H.axon_identity_body()
        body["execution_grant"] = {"mutation": True}
        with H.mock_service({("GET", "/health"): (200, body)}) as (port, _srv):
            res = _adapter(port).identity(META)
            self.assertEqual(res.error_code, "MALFORMED_RESPONSE")

    def test_missing_inference_health_fails_closed_via_adapter(self):
        body = H.axon_identity_body()
        del body["inference"]["healthy"]
        with H.mock_service({("GET", "/health"): (200, body)}) as (port, _srv):
            res = _adapter(port).identity(META)
            self.assertEqual(res.error_code, "MALFORMED_RESPONSE")

    def test_malformed_json_fails_closed(self):
        with H.mock_service({("GET", "/health"): (200, b"not-json{")}) as (port, _srv):
            res = _adapter(port).identity(META)
            self.assertNotEqual(res.status, "success")

    def test_non_200_fails_closed(self):
        with H.mock_service({("GET", "/health"): (500, {})}) as (port, _srv):
            res = _adapter(port).identity(META)
            self.assertNotEqual(res.status, "success")

    def test_non_loopback_endpoint_rejected_at_construction(self):
        with self.assertRaises(LiveConfigError):
            LiveEndpoint(service_id="axon", host="10.0.0.5", port=8300, endpoint_class="loopback")

    def test_non_loopback_host_denied_even_if_construction_bypassed(self):
        # Force a non-loopback host past LiveEndpoint validation; every later
        # layer (settings and/or transport) must still fail closed.
        ep = H.endpoint("axon", 8300)
        object.__setattr__(ep, "host", "203.0.113.7")
        try:
            adapter = LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep))
            res = adapter.identity(META)
            self.assertNotEqual(res.status, "success")
        except (LiveConfigError, TransportDenied):
            pass  # fail-closed at construction is equally acceptable

    def test_no_mutation_authority_in_identity_result(self):
        with H.mock_service({("GET", "/health"): (200, H.axon_identity_body())}) as (port, _srv):
            res = _adapter(port).identity(META)
            self.assertEqual(res.status, "success")
            self.assertNotIn("mutation_authorized", res.payload)
            self.assertNotIn("execution_available", res.payload)
            self.assertIsNone(res.write_class)


if __name__ == "__main__":
    unittest.main()
