"""Completion verifier tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.runtime.completion import verify_completion
from recipe_engine.founder_runtime.errors import CompletionCriteriaFailed
from recipe_engine.founder_runtime.contracts.mission import (
    Mission, Principal, MissionClass, Priority, RiskClass, AutonomyLevel,
    SCHEMA_MISSION, Deliverable, SCHEMA_DELIVERABLE, DeliverableStatus,
)
from recipe_engine.founder_runtime.contracts.completion import AcceptanceResult
from recipe_engine.founder_runtime.contracts.receipts import ExecutionReceipt, ReceiptStatus, SCHEMA_RECEIPT, TestResult
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.adapters.fixture_governance import FixtureGovernance


def _mission(state=MissionState.VERIFYING, del_status=DeliverableStatus.DELIVERED):
    return Mission(
        schema_version=SCHEMA_MISSION,
        mission_id="m1",
        version=3,
        principal=Principal.jeramie_fixture(),
        title="t",
        objective="o",
        mission_class=MissionClass.SYSTEM_STATUS,
        priority=Priority.P1,
        risk_class=RiskClass.LOW,
        autonomy_level=AutonomyLevel.L1_ASSISTED,
        current_state=state,
        desired_outcome="d",
        deliverables=[Deliverable(SCHEMA_DELIVERABLE, "d1", "desc", True, ("a",), ("e",), del_status)],
        constraints=[],
        authority_requirements=[],
        context_requirements=[],
        verification_criteria=[],
        completion_rule="all_required_deliverables",
        created_at="t",
        updated_at="t",
    )


def _receipt(status=ReceiptStatus.SUCCESS):
    decision = FixtureGovernance().decide("A0", action="c", scope="local_fixture")
    r = ExecutionReceipt(
        schema_version=SCHEMA_RECEIPT,
        receipt_id="r1",
        mission_id="m1",
        plan_id="p1",
        step_id="s1",
        executor="fixture_axon",
        capability="c",
        started_at="a",
        ended_at="b",
        status=status,
        input_hash="i",
        output_hash="",
        repository_metadata={"mutation": False},
        files_changed=[],
        tests=[TestResult("t", True)],
        artifacts=[],
        error=None,
        authority_reference=f"fixture_gov:{decision['decision_id']}:A0:allow",
        authority_contract=decision,
        idempotency_key="k",
        outcome_data={"result": "verified", "mutation": False},
    )
    r.output_hash = hash_payload(r.outcome_data)
    r.action_binding = f"{r.capability}:{r.input_hash}:{r.output_hash}"
    r.compute_hash()
    return r


def _outcome():
    body = {"outcome_id":"o1", "mission_id":"m1", "plan_id":"p1", "status":"verified", "result_hash":"result"}
    return {**body, "outcome_hash": hash_payload(body)}


class CompletionTests(unittest.TestCase):
    def test_full_completion(self):
        report = verify_completion(
            _mission(),
            receipts=[_receipt()],
            artifacts=[{"artifact_id": "a1"}],
            approvals=[],
            acceptance=[AcceptanceResult("a", True, "r1")],
            outcome=_outcome(),
            plan_id="p1",
            clock_now="t",
        )
        self.assertEqual(report.final_state, MissionState.COMPLETED)

    def test_missing_deliverable(self):
        with self.assertRaises(CompletionCriteriaFailed):
            verify_completion(
                _mission(del_status=DeliverableStatus.PENDING),
                receipts=[_receipt()],
                artifacts=[],
                approvals=[],
                acceptance=[AcceptanceResult("a", True, "e")],
                clock_now="t",
            )

    def test_failed_receipt_without_exceptions(self):
        with self.assertRaises(CompletionCriteriaFailed):
            verify_completion(
                _mission(),
                receipts=[_receipt(ReceiptStatus.FAILURE)],
                artifacts=[],
                approvals=[],
                acceptance=[AcceptanceResult("a", True, "e")],
                clock_now="t",
            )

    def test_exceptions_cannot_waive_truth_gates(self):
        with self.assertRaises(CompletionCriteriaFailed):
            verify_completion(
                _mission(), receipts=[_receipt(ReceiptStatus.FAILURE)], artifacts=[], approvals=[],
                acceptance=[AcceptanceResult("a", False, None)], exceptions=["build_failed"],
                outcome=_outcome(),
                clock_now="t",
            )


if __name__ == "__main__":
    unittest.main()
