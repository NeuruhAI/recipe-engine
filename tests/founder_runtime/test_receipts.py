"""Receipt validation tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.contracts.receipts import (
    ExecutionReceipt, ReceiptStatus, TestResult, ArtifactRef, SCHEMA_RECEIPT,
)
from recipe_engine.founder_runtime.evidence.validation import validate_receipt, assert_valid_receipt
from recipe_engine.founder_runtime.errors import ReceiptInvalid
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.adapters.fixture_governance import FixtureGovernance


def _rcpt(**over):
    decision = FixtureGovernance().decide("A0", action="fixture.read", scope="local_fixture")
    r = ExecutionReceipt(
        schema_version=SCHEMA_RECEIPT,
        receipt_id="r1",
        mission_id="m1",
        plan_id="p1",
        step_id="s1",
        executor="fixture_axon",
        capability="fixture.read",
        started_at="2026-07-19T00:00:00.000Z",
        ended_at="2026-07-19T00:00:01.000Z",
        status=ReceiptStatus.SUCCESS,
        input_hash="in1",
        output_hash="",
        repository_metadata={"mode": "fixture", "mutation": False},
        files_changed=[],
        tests=[TestResult("t", True)],
        artifacts=[ArtifactRef("a1", "report", "h", "fixture://a1")],
        error=None,
        authority_reference=f"fixture_gov:{decision['decision_id']}:A0:allow",
        authority_contract=decision,
        idempotency_key="idem1",
        outcome_data={"result": "fixture", "mutation": False},
    )
    r.output_hash = hash_payload(r.outcome_data)
    r.action_binding = f"{r.capability}:{r.input_hash}:{r.output_hash}"
    r.compute_hash()
    for k, v in over.items():
        setattr(r, k, v)
    if "receipt_hash" not in over:
        r.compute_hash()
    return r


class ReceiptTests(unittest.TestCase):
    def test_valid(self):
        r = _rcpt()
        self.assertEqual(validate_receipt(r, expected_mission_id="m1", expected_plan_id="p1", expected_step_id="s1", expected_input_hash="in1"), [])

    def test_wrong_mission(self):
        r = _rcpt()
        self.assertIn("wrong_mission_id", validate_receipt(r, expected_mission_id="x", expected_plan_id="p1", expected_step_id="s1"))

    def test_wrong_plan(self):
        r = _rcpt()
        self.assertIn("wrong_plan_id", validate_receipt(r, expected_mission_id="m1", expected_plan_id="x", expected_step_id="s1"))

    def test_wrong_step(self):
        r = _rcpt()
        self.assertIn("wrong_step_id", validate_receipt(r, expected_mission_id="m1", expected_plan_id="p1", expected_step_id="x"))

    def test_bad_input_hash(self):
        r = _rcpt()
        self.assertIn("bad_input_hash", validate_receipt(r, expected_mission_id="m1", expected_plan_id="p1", expected_step_id="s1", expected_input_hash="nope"))

    def test_missing_authority(self):
        r = _rcpt(authority_reference="")
        r.compute_hash()
        self.assertIn("missing_authority", validate_receipt(r, expected_mission_id="m1", expected_plan_id="p1", expected_step_id="s1"))

    def test_timestamp_order(self):
        r = _rcpt(started_at="2026-07-19T00:00:02.000Z", ended_at="2026-07-19T00:00:01.000Z")
        r.compute_hash()
        self.assertIn("timestamp_order", validate_receipt(r, expected_mission_id="m1", expected_plan_id="p1", expected_step_id="s1"))

    def test_tampered_receipt(self):
        r = _rcpt()
        r.receipt_hash = "0" * 64
        self.assertIn("tampered_receipt", validate_receipt(r, expected_mission_id="m1", expected_plan_id="p1", expected_step_id="s1"))

    def test_assert_raises(self):
        r = _rcpt(mission_id="wrong")
        r.compute_hash()
        with self.assertRaises(ReceiptInvalid):
            assert_valid_receipt(r, expected_mission_id="m1", expected_plan_id="p1", expected_step_id="s1")


if __name__ == "__main__":
    unittest.main()
