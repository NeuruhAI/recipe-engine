"""D01-D31 exploit regressions for Founder Runtime P1.2."""
from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.fixture_axon import FixtureAxon
from recipe_engine.founder_runtime.adapters.fixture_governance import FixtureGovernance
from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.contracts.authority import AuthorityClass
from recipe_engine.founder_runtime.contracts.events import EventType, MissionEvent, SCHEMA_EVENT
from recipe_engine.founder_runtime.contracts.receipts import ReceiptStatus
from recipe_engine.founder_runtime.errors import (
    AdapterModeInvalid, CompletionCriteriaFailed, MissionSchemaInvalid,
    PersistenceConflict, StateTransitionInvalid,
)
from recipe_engine.founder_runtime.evidence.validation import validate_receipt, validated_receipt_result
from recipe_engine.founder_runtime.events.projector import rebuild_mission_from_events
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.intent.authority import classify_authority, POLICY_VERSION
from recipe_engine.founder_runtime.intent.compiler import compile_intent_to_mission
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.runtime.p2_gates import require_p2_gate
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.vertical_slices.system_status import run_system_status_slice


def _runtime():
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    return MissionRuntime(tmp.name, clock=SteppingClock(), adapter_mode="fixture")


def _receipt(*, status=ReceiptStatus.SUCCESS, outcome=None, capability="fixture.read"):
    outcome = dict(outcome or {"observed": "fixture", "mutation": False})
    gov = FixtureGovernance().decide("A0", action=capability, scope="local_fixture")
    return FixtureAxon().execute_readonly_step(
        mission_id="m1", plan_id="p1", step_id="s1", capability=capability,
        input_hash="input", authority_reference=f"fixture_gov:{gov['decision_id']}:A0:allow",
        authority_contract=gov, started_at="2026-01-01T00:00:00Z",
        ended_at="2026-01-01T00:00:01Z", outcome_data=outcome,
        force_failure=status != ReceiptStatus.SUCCESS,
    )


class AuthorityDoctrineTests(unittest.TestCase):
    def test_allowlisted_status_is_a0(self):
        req = classify_authority("Tell me where the system is and report status", canonical_action="system_status")[0]
        self.assertEqual(req.authority_class, AuthorityClass.A0)
        self.assertEqual(req.policy_version, POLICY_VERSION)

    def test_unknown_escalates_to_a5(self):
        self.assertEqual(classify_authority("do the thing", canonical_action="general")[0].authority_class, AuthorityClass.A5)

    def test_a5_is_reachable_for_paraphrased_external_send(self):
        for text in ("notify them with the result", "make it live", "ship to customers", "move funds to the vendor"):
            self.assertEqual(classify_authority(text)[0].authority_class, AuthorityClass.A5, text)

    def test_destructive_paraphrases_are_a6(self):
        for text in ("make the records unrecoverable", "discard all records", "remove permanently"):
            self.assertEqual(classify_authority(text)[0].authority_class, AuthorityClass.A6, text)

    def test_model_hint_cannot_lower_class(self):
        class LyingIAR:
            def interpret(self, request_text, principal_id="jeramie"):
                from recipe_engine.founder_runtime.adapters.fixture_iar import FixtureIAR
                result = FixtureIAR().interpret(request_text, principal_id=principal_id)
                result["authority_hint"] = "A0"
                material = dict(result); material.pop("output_hash"); material.pop("receipt_id")
                result["output_hash"] = hash_payload(material)
                return result
        mission, _ = compile_intent_to_mission("ship to customers", clock_now="t", iar=LyingIAR())
        self.assertEqual(mission.authority_requirements[0].authority_class, AuthorityClass.A5)

    def test_authority_is_evented_and_projection_tamper_detected(self):
        rt = _runtime(); mission = rt.create_mission_from_text("status report", creation_idempotency_key="auth-event")
        self.assertIn("authority_classified", [e["event_type"] for e in rt.events(mission.mission_id)])
        body = mission.to_dict(); body["authority_requirements"][0]["authority_class"] = "A0"
        rt.store._c().execute("UPDATE missions SET body_json=? WHERE mission_id=?", (json.dumps(body), mission.mission_id))
        with self.assertRaises(PersistenceConflict): rt.replay(mission.mission_id)
        rt.close()

    def test_blocked_input_is_real_state(self):
        mission, _ = compile_intent_to_mission("???", clock_now="t")
        self.assertEqual(mission.current_state, MissionState.BLOCKED_INPUT)

    def test_iar_closed_schema_rejects_extra_field(self):
        class ExtraIAR:
            def interpret(self, request_text, principal_id="jeramie"):
                from recipe_engine.founder_runtime.adapters.fixture_iar import FixtureIAR
                value=FixtureIAR().interpret(request_text, principal_id=principal_id); value["surprise"]="x"; return value
        with self.assertRaises(MissionSchemaInvalid): compile_intent_to_mission("status report", clock_now="t", iar=ExtraIAR())

    def test_iar_hash_and_request_binding_rejected(self):
        class BadIAR:
            def interpret(self, request_text, principal_id="jeramie"):
                from recipe_engine.founder_runtime.adapters.fixture_iar import FixtureIAR
                value=FixtureIAR().interpret(request_text, principal_id=principal_id); value["output_hash"]="0"*64; return value
        with self.assertRaises(MissionSchemaInvalid): compile_intent_to_mission("status report", clock_now="t", iar=BadIAR())


class ReceiptDoctrineTests(unittest.TestCase):
    def _errors(self, receipt):
        return validate_receipt(receipt, expected_mission_id="m1", expected_plan_id="p1", expected_step_id="s1", expected_input_hash="input")

    def test_valid_fixture_receipt_reports_trust(self):
        receipt = _receipt()
        result = validated_receipt_result(receipt, expected_mission_id="m1", expected_plan_id="p1", expected_step_id="s1", expected_input_hash="input")
        self.assertTrue(result["ok"]); self.assertEqual(result["trust_level"], "fixture_only")

    def test_missing_hash_rejected_without_backfill(self):
        receipt = _receipt(); receipt.receipt_hash = ""
        self.assertIn("missing_receipt_hash", self._errors(receipt)); self.assertEqual(receipt.receipt_hash, "")

    def test_compute_hash_cannot_invent_success(self):
        receipt = _receipt(status=ReceiptStatus.FAILURE); receipt.compute_hash()
        self.assertIn("status_not_success", self._errors(receipt))

    def test_self_consistent_caller_outcome_contradiction_rejected(self):
        receipt = _receipt(); receipt.outcome_data["observed"] = "forged"; receipt.compute_hash()
        self.assertIn("outcome_hash_mismatch", self._errors(receipt))

    def test_authority_must_resolve(self):
        receipt = _receipt(); receipt.authority_contract = {}; receipt.compute_hash()
        self.assertIn("authority_contract_unresolved", self._errors(receipt))

    def test_fixture_cannot_authorize_live_mutation(self):
        receipt = _receipt(capability="deploy.production"); receipt.mutation_authorized = True; receipt.compute_hash()
        errors = self._errors(receipt)
        self.assertIn("fixture_mutation_forbidden", errors); self.assertIn("unsafe_fixture_capability", errors)

    def test_exact_mission_plan_step_action_input_output_binding(self):
        receipt = _receipt(); receipt.action_binding = "wrong"; receipt.compute_hash()
        self.assertIn("action_binding_mismatch", self._errors(receipt))

    def test_duplicate_receipt_preserves_trust(self):
        rt = _runtime(); m = rt.create_mission_from_text("status report", creation_idempotency_key="dup-r")
        rt.transition(m.mission_id, MissionState.INTAKE, reason="intake")
        receipt = _receipt(); receipt.mission_id=m.mission_id; receipt.plan_id="p1"; receipt.step_id="s1"
        receipt.action_binding=f"{receipt.capability}:{receipt.input_hash}:{receipt.output_hash}"; receipt.compute_hash()
        rt.record_receipt(receipt); again=rt.record_receipt(copy.deepcopy(receipt))
        self.assertEqual(again.to_dict(), receipt.to_dict()); self.assertEqual(len(rt.store.list_receipts(m.mission_id)), 1)
        rt.close()


class EventChainDoctrineTests(unittest.TestCase):
    def setUp(self):
        self.rt = _runtime(); self.m = self.rt.create_mission_from_text("status report", creation_idempotency_key="chain")
        self.rt.transition(self.m.mission_id, MissionState.INTAKE, reason="intake")
        self.rt.transition(self.m.mission_id, MissionState.CONTEXT_RESOLUTION, reason="context")
    def tearDown(self): self.rt.close()

    def test_genesis_and_predecessors(self):
        events=self.rt.store.list_events(self.m.mission_id)
        self.assertEqual([e.chain_position for e in events], list(range(len(events))))
        self.assertEqual(events[1].previous_event_hash, events[0].event_hash)

    def test_deletion_detected(self):
        events=self.rt.store.list_events(self.m.mission_id); del events[1]
        with self.assertRaises(PersistenceConflict): rebuild_mission_from_events(events)

    def test_insertion_detected(self):
        events=self.rt.store.list_events(self.m.mission_id); fake=copy.deepcopy(events[1]); fake.event_id="fake"
        events.insert(1, fake)
        with self.assertRaises(PersistenceConflict): rebuild_mission_from_events(events)

    def test_reorder_detected(self):
        events=self.rt.store.list_events(self.m.mission_id); events[1],events[2]=events[2],events[1]
        with self.assertRaises(PersistenceConflict): rebuild_mission_from_events(events)

    def test_substitution_detected_even_if_row_rehashed(self):
        events=self.rt.store.list_events(self.m.mission_id); events[1].reason="substituted"; events[1].compute_hash()
        with self.assertRaises(PersistenceConflict): rebuild_mission_from_events(events)

    def test_direct_completion_transition_rejected(self):
        with self.assertRaises(StateTransitionInvalid): self.rt.transition(self.m.mission_id, MissionState.COMPLETED, reason="bypass")

    def test_idempotency_collision_rejected(self):
        existing=self.rt.store.list_events(self.m.mission_id)[-1]; forged=copy.deepcopy(existing); forged.event_id="other"; forged.reason="other"
        with self.assertRaises(PersistenceConflict): self.rt.store.append_event(forged, expected_version=self.rt.get_mission(self.m.mission_id).version)

    def test_terminal_resurrection_rejected(self):
        result=run_system_status_slice(self.rt, creation_idempotency_key="terminal")
        terminal=self.rt.get_mission(result["mission_id"])
        ev=MissionEvent(SCHEMA_EVENT,"resurrect",terminal.mission_id,terminal.current_state,MissionState.QUEUED,EventType.STATE_TRANSITION,"attacker","resume",[],"t","resurrect",None,terminal.mission_id)
        with self.assertRaises(PersistenceConflict): self.rt.store.append_event(ev, expected_version=terminal.version)


class OnLogAndDeferredDoctrineTests(unittest.TestCase):
    def test_full_slice_truth_objects_have_events(self):
        rt=_runtime(); result=run_system_status_slice(rt, creation_idempotency_key="onlog")
        inspection=rt.inspect(result["mission_id"]); types=[e["event_type"] for e in inspection["events"]]
        self.assertEqual(types.count("receipt_recorded"), len(inspection["receipts"]))
        self.assertEqual(types.count("artifact_registered"), len(inspection["artifacts"]))
        self.assertIn("outcome_recorded", types); self.assertEqual(types[-1], "completion_recorded")
        rt.close()

    def test_store_receipt_without_event_rejected(self):
        rt=_runtime(); m=rt.create_mission_from_text("status report", creation_idempotency_key="direct-r")
        receipt=_receipt(); receipt.mission_id=m.mission_id; receipt.action_binding=f"{receipt.capability}:{receipt.input_hash}:{receipt.output_hash}"; receipt.compute_hash()
        with self.assertRaises(PersistenceConflict): rt.store.save_receipt(receipt)
        rt.close()

    def test_all_deferred_capabilities_fail_closed(self):
        for gate in ("R-L","R-M","R-N","R-O"):
            with self.assertRaises(AdapterModeInvalid): require_p2_gate(gate)
        from recipe_engine.founder_runtime.runtime.scheduler import LocalScheduler
        from recipe_engine.founder_runtime.runtime.retry import should_retry, RetryPolicy
        with self.assertRaises(AdapterModeInvalid): LocalScheduler().enqueue("x", lambda: None)
        with self.assertRaises(AdapterModeInvalid): should_retry(0, RetryPolicy())

    def test_recovery_is_plan_only(self):
        rt=_runtime(); partial=run_system_status_slice(rt, stop_after_steps=1, creation_idempotency_key="recover-guard")
        rec=rt.recover(partial["mission_id"])
        self.assertFalse(rec["execution_resume_authorized"]); self.assertEqual(rec["p2_gate"], "R-L")
        rt.close()


class ConfigurationDoctrineTests(unittest.TestCase):
    def test_example_contains_only_nonfunctional_identifiers(self):
        text=(ROOT/".env.example").read_text()
        self.assertIn("NONFUNCTIONAL_PLACEHOLDER", text)
        self.assertNotRegex(text, r"/Users/|/home/|[a-z]{20}\.supabase\.co")
        uuids=re.findall(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", text)
        self.assertEqual(uuids, ["00000000-0000-0000-0000-000000000000"])


if __name__ == "__main__": unittest.main()
