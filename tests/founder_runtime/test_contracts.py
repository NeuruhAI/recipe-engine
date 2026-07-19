"""Contract validation tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.contracts.mission import (
    Principal, Mission, MissionClass, Priority, RiskClass, AutonomyLevel,
    SCHEMA_MISSION, validate_mission_dict, Deliverable, SCHEMA_DELIVERABLE, DeliverableStatus,
)
from recipe_engine.founder_runtime.contracts.authority import AuthorityClass, AuthorityRequirement
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.serialization import to_canonical


class ContractTests(unittest.TestCase):
    def _mission(self, **over):
        p = Principal.jeramie_fixture()
        m = Mission(
            schema_version=SCHEMA_MISSION,
            mission_id="msn_test",
            version=1,
            principal=p,
            title="t",
            objective="o",
            mission_class=MissionClass.SYSTEM_STATUS,
            priority=Priority.P1,
            risk_class=RiskClass.LOW,
            autonomy_level=AutonomyLevel.L1_ASSISTED,
            current_state=MissionState.DRAFT,
            desired_outcome="done",
            deliverables=[
                Deliverable(SCHEMA_DELIVERABLE, "d1", "desc", True, ("a",), ("e",), DeliverableStatus.PENDING)
            ],
            constraints=["no_live"],
            authority_requirements=[
                AuthorityRequirement("fr.authority_req.v1", AuthorityClass.A0, "read", "local", "r", False)
            ],
            context_requirements=["founder_core"],
            verification_criteria=["receipts_valid"],
            completion_rule="all_required_deliverables",
            created_at="2026-07-19T00:00:00.000Z",
            updated_at="2026-07-19T00:00:00.000Z",
        )
        m.recompute_input_hash()
        for k, v in over.items():
            setattr(m, k, v)
        return m

    def test_valid_mission(self):
        m = self._mission()
        self.assertEqual(validate_mission_dict(m.to_dict()), [])

    def test_missing_principal(self):
        d = self._mission().to_dict()
        del d["principal"]
        self.assertTrue(any("principal" in e for e in validate_mission_dict(d)))

    def test_invalid_autonomy(self):
        d = self._mission().to_dict()
        d["autonomy_level"] = "L99"
        self.assertTrue(any("autonomy" in e for e in validate_mission_dict(d)))

    def test_invalid_authority(self):
        d = self._mission().to_dict()
        d["authority_requirements"] = [{"authority_class": "A9", "schema_version": "x", "action": "a", "scope": "s", "reason": "r", "requires_approval": False}]
        self.assertTrue(any("authority" in e for e in validate_mission_dict(d)))

    def test_invalid_completion_rule(self):
        d = self._mission().to_dict()
        d["completion_rule"] = "whatever"
        self.assertTrue(any("completion_rule" in e for e in validate_mission_dict(d)))

    def test_invalid_schema_version(self):
        d = self._mission().to_dict()
        d["schema_version"] = "nope.v1"
        self.assertTrue(any("schema_version" in e for e in validate_mission_dict(d)))

    def test_stable_canonical_hash(self):
        m = self._mission()
        h1 = hash_payload(m.to_dict())
        h2 = hash_payload(m.to_dict())
        self.assertEqual(h1, h2)

    def test_key_order_does_not_alter_hash(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        self.assertEqual(hash_payload(a), hash_payload(b))
        self.assertEqual(to_canonical(a), to_canonical(b))

    def test_jeramie_principal(self):
        p = Principal.jeramie_fixture()
        self.assertEqual(p.principal_id, "jeramie")


if __name__ == "__main__":
    unittest.main()
