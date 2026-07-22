"""P2-B Governance authority-reference contract — targeted adversarial suite.

The promoted UP-2 Governance decision carries its authority reference
(``policy_id`` + ``policy_version``).  This suite pins the repaired Recipe
Engine contract: both references are REQUIRED with the correct primitive
types, missing/empty/malformed values fail closed, any other unknown field
still fails closed, and the references are observed evidence only — they are
never converted into execution authority and never cross the AXON boundary.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.contract_validation import validate_governance_decision
from recipe_engine.founder_runtime.adapters.envelope import validate_payload_hash
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, GovernanceAction
from recipe_engine.founder_runtime.contracts.authority import AuthorityClass
from recipe_engine.founder_runtime.errors import MalformedResponse, ProvenanceInvalid, UnsafeCapability
from recipe_engine.live_adapters.canary import ORGAN_ORDER, run_live_readonly_canary
from recipe_engine.live_adapters.live_governance import LiveGovernanceAdapter
from recipe_engine.live_adapters.settings import LiveAdapterSettings, RepoRoot

from tests.founder_runtime import p2b_harness as H

META = AdapterRequestMeta(correlation_id="authref", observed_at="2026-07-21T00:00:00.000Z")


def _decision(**overrides):
    body = H.governance_decision_body({"action_id": "a1"})
    body.update(overrides)
    return body


def _without(body: dict, key: str) -> dict:
    return {k: v for k, v in body.items() if k != key}


# ================================================== Pure validator: fail closed
class AuthorityReferenceValidatorTests(unittest.TestCase):
    def test_canonical_decision_with_authority_reference_accepted(self):
        validated = validate_governance_decision(_decision())
        self.assertEqual(validated["policy_id"], H.GOVERNANCE_POLICY_ID)
        self.assertEqual(validated["policy_version"], H.GOVERNANCE_POLICY_VERSION)

    def test_missing_policy_id_fails_closed(self):
        with self.assertRaises(MalformedResponse):
            validate_governance_decision(_without(_decision(), "policy_id"))

    def test_missing_policy_version_fails_closed(self):
        with self.assertRaises(MalformedResponse):
            validate_governance_decision(_without(_decision(), "policy_version"))

    def test_wrong_type_policy_id_fails_closed(self):
        for bad in (7, True, None, {"id": "default"}, ["default"]):
            with self.assertRaises(MalformedResponse):
                validate_governance_decision(_decision(policy_id=bad))

    def test_wrong_type_policy_version_fails_closed(self):
        for bad in (7, True, None, {}, [H.GOVERNANCE_POLICY_VERSION]):
            with self.assertRaises(ProvenanceInvalid):
                validate_governance_decision(_decision(policy_version=bad))

    def test_empty_policy_references_fail_closed(self):
        with self.assertRaises(MalformedResponse):
            validate_governance_decision(_decision(policy_id=""))
        with self.assertRaises(MalformedResponse):
            validate_governance_decision(_decision(policy_id="   "))
        with self.assertRaises(ProvenanceInvalid):
            validate_governance_decision(_decision(policy_version=""))

    def test_malformed_policy_version_fails_closed(self):
        for bad in (
            H.GOVERNANCE_POLICY_VERSION[:-1],          # 63 hex chars
            H.GOVERNANCE_POLICY_VERSION.upper(),       # uppercase hex
            "z" * 64,                                  # non-hex
            H.GOVERNANCE_POLICY_VERSION + "aa",        # too long
        ):
            with self.assertRaises(ProvenanceInvalid):
                validate_governance_decision(_decision(policy_version=bad))

    def test_unknown_extra_field_still_fails_closed(self):
        with self.assertRaises(MalformedResponse):
            validate_governance_decision(_decision(surprise="field"))

    def test_execution_grant_smuggling_rejected(self):
        for grant in ({"execution": "GRANTED"}, {"mutation_authority": True}, {"approved_writes": ["*"]}):
            with self.assertRaises(MalformedResponse):
                validate_governance_decision(_decision(**grant))

    def test_caller_policy_override_still_rejected(self):
        body = _decision()
        body["action"] = {"action_id": "a1", "policy": {"caller": "override"}}
        with self.assertRaises(UnsafeCapability):
            validate_governance_decision(body)

    def test_mutation_like_decision_vocabulary_rejected(self):
        for bad in ("execute", "mutate", "approve_write", "ALLOW"):
            with self.assertRaises(MalformedResponse):
                validate_governance_decision(_decision(decision=bad))


# ============================================= Live adapter: wire-level contract
class AuthorityReferenceLiveAdapterTests(unittest.TestCase):
    def _action(self):
        return GovernanceAction(action_id="act-1", actor="founder_runtime", tool="observe",
                                domain="organism_status", summary="read-only observation")

    def _decide(self, route):
        with H.mock_service({("POST", "/v1/evaluate"): route}) as (port, _srv):
            ep = H.endpoint("governance", port)
            adapter = LiveGovernanceAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep))
            return adapter.decide(self._action(), trusted_authority_class=AuthorityClass.A0,
                                  trusted_policy_version="p2b.live.v1", meta=META)

    def _echo_route(self, **overrides):
        def route(body: bytes):
            action = json.loads(body or b"{}").get("action", {})
            resp = H.governance_decision_body(action)
            resp.update(overrides)
            return (200, resp)
        return route

    def test_decide_succeeds_with_authority_reference(self):
        res = self._decide(self._echo_route())
        self.assertEqual(res.status, "success")
        self.assertEqual(res.payload["policy_id"], H.GOVERNANCE_POLICY_ID)
        self.assertEqual(res.payload["policy_version"], H.GOVERNANCE_POLICY_VERSION)
        # Non-executing, read-only observation; deterministic receipt evidence.
        self.assertEqual(res.payload["execution_marker"], "NOT_CLAIMED_BY_GOVERNANCE")
        self.assertFalse(res.payload["mutation_authority_consumed"])
        self.assertFalse(res.mutation_attempted)
        self.assertTrue(validate_payload_hash(res))
        # The authority reference never displaces the caller's own trust pin.
        self.assertEqual(res.payload["trusted_policy_version"], "p2b.live.v1")
        self.assertEqual(res.payload["trusted_authority_class"], AuthorityClass.A0.value)

    def test_decide_missing_policy_id_fails_closed_on_wire(self):
        def route(body: bytes):
            action = json.loads(body or b"{}").get("action", {})
            return (200, _without(H.governance_decision_body(action), "policy_id"))
        self.assertEqual(self._decide(route).status, "malformed")

    def test_decide_missing_policy_version_fails_closed_on_wire(self):
        def route(body: bytes):
            action = json.loads(body or b"{}").get("action", {})
            return (200, _without(H.governance_decision_body(action), "policy_version"))
        self.assertEqual(self._decide(route).status, "malformed")

    def test_decide_unknown_extra_field_fails_closed_on_wire(self):
        res = self._decide(self._echo_route(execution="GRANTED"))
        self.assertIn(res.status, ("malformed", "failed"))

    def test_decide_malformed_policy_version_fails_closed_on_wire(self):
        res = self._decide(self._echo_route(policy_version="not-a-content-hash"))
        self.assertIn(res.status, ("malformed", "failed"))

    def test_decide_non_200_fails_closed(self):
        self.assertNotEqual(self._decide((503, {"ok": False})).status, "success")

    def test_decide_deterministic_replay(self):
        r1 = self._decide(self._echo_route())
        r2 = self._decide(self._echo_route())
        self.assertEqual(r1.payload_hash, r2.payload_hash)
        self.assertEqual(r1.payload, r2.payload)


# ===================================== Canary: no authority crosses into AXON
class AuthorityReferenceAxonBoundaryTests(unittest.TestCase):
    def test_green_canary_carries_no_policy_reference_into_axon(self):
        captured = {}

        def axon_route(body: bytes):
            captured["axon_request_body"] = bytes(body or b"")
            return (200, H.axon_identity_body())

        def governance_route(body: bytes):
            action = json.loads(body or b"{}").get("action", {})
            return (200, H.governance_decision_body(action))

        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            base = Path(td)
            gh = base / "recipe-engine"
            vs = base / "vault-src"
            vr = base / "vault-run"
            H.make_git_repo(gh, origin="https://github.com/NeuruhAI/recipe-engine.git")
            H.make_git_repo(vs)
            H.make_git_repo(vr, extra_commit=True)
            H.write_sealed_audit(base / "host")
            qport, _ = stack.enter_context(H.mock_service(H.qdrant_routes()))
            mport, _ = stack.enter_context(H.mock_service(
                {("GET", "/health"): (200, {"overall": "healthy", "dependencies": [], "blocked": []})}))
            iport, _ = stack.enter_context(H.mock_service(
                {("GET", "/provenance"): (200, H.iar_provenance_body())}))
            aport, _ = stack.enter_context(H.mock_service(
                {("GET", "/world/defensenet/opinion"): (200, H.aegis_world_body("defensenet"))}))
            gport, _ = stack.enter_context(H.mock_service({("POST", "/v1/evaluate"): governance_route}))
            xport, _ = stack.enter_context(H.mock_service({("GET", "/health"): axon_route}))
            settings = LiveAdapterSettings(
                repo_roots=(
                    RepoRoot("NeuruhAI/recipe-engine", str(gh)),
                    RepoRoot("NeuruhAI/neuruh-vault", str(vs), role="vault_source"),
                    RepoRoot("neuruh-vault-runtime", str(vr), role="vault_runtime"),
                ),
                endpoints=(
                    H.endpoint("qdrant", qport), H.endpoint("mother", mport), H.endpoint("iar", iport),
                    H.endpoint("aegis", aport), H.endpoint("governance", gport), H.endpoint("axon", xport),
                ),
                host_recovery_audit_base=str(base / "host"),
                recipe_engine_sha="ead8a89",
            )
            result = run_live_readonly_canary(settings)

        self.assertEqual(result.verdict, "GREEN", result.hold_reason)
        self.assertEqual([o.organ for o in result.observations], list(ORGAN_ORDER))
        by_organ = {o.organ: o for o in result.observations}
        governance = by_organ["governance"]
        axon = by_organ["axon_readonly"]
        # Governance observed the authority reference; the receipt binds it by hash.
        self.assertEqual(governance.status, "success")
        # The AXON call receives no governance policy reference in any form.
        for token in (b"policy_id", b"policy_version", H.GOVERNANCE_POLICY_VERSION.encode()):
            self.assertNotIn(token, captured["axon_request_body"])
        axon_evidence = json.dumps([list(axon.evidence_refs)]).lower()
        self.assertNotIn("policy_id", axon_evidence)
        self.assertNotIn(H.GOVERNANCE_POLICY_VERSION, axon_evidence)
        self.assertFalse(result.mutation_observed)
        self.assertFalse(result.fixture_fallback_used)


if __name__ == "__main__":
    unittest.main()
