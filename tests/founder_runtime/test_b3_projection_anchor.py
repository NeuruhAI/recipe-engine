"""B3 — full projection anchored to events."""
from __future__ import annotations
import json, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.contracts.mission import DeliverableStatus
from recipe_engine.founder_runtime.errors import PersistenceConflict
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.serialization import to_canonical
from recipe_engine.founder_runtime.vertical_slices.system_status import run_system_status_slice


class B3ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.path = self.tmp.name
        self.tmp.close()
        self.rt = MissionRuntime(self.path, clock=SteppingClock(), adapter_mode="fixture")

    def tearDown(self):
        self.rt.close()

    def test_objective_tamper_detected(self):
        m = self.rt.create_mission_from_text("organism status", creation_idempotency_key="b3-obj")
        c = self.rt.store._c()
        row = c.execute("SELECT body_json FROM missions WHERE mission_id=?", (m.mission_id,)).fetchone()
        body = json.loads(row["body_json"])
        body["objective"] = "HACKED"
        c.execute(
            "UPDATE missions SET body_json=?, projection_hash=? WHERE mission_id=?",
            (to_canonical(body), hash_payload(body), m.mission_id),
        )
        self.assertFalse(self.rt.store.verify_integrity()["ok"])
        with self.assertRaises(PersistenceConflict):
            self.rt.replay(m.mission_id)

    def test_constraint_tamper_detected(self):
        m = self.rt.create_mission_from_text("organism status", creation_idempotency_key="b3-con")
        c = self.rt.store._c()
        row = c.execute("SELECT body_json FROM missions WHERE mission_id=?", (m.mission_id,)).fetchone()
        body = json.loads(row["body_json"])
        body["constraints"] = ["evil"]
        c.execute(
            "UPDATE missions SET body_json=?, projection_hash=? WHERE mission_id=?",
            (to_canonical(body), hash_payload(body), m.mission_id),
        )
        self.assertFalse(self.rt.store.verify_integrity()["ok"])

    def test_deliverable_tamper_detected(self):
        m = self.rt.create_mission_from_text("organism status", creation_idempotency_key="b3-del")
        self.rt.set_deliverable_status(m.mission_id, "del_status_report", DeliverableStatus.DELIVERED)
        c = self.rt.store._c()
        row = c.execute("SELECT body_json FROM missions WHERE mission_id=?", (m.mission_id,)).fetchone()
        body = json.loads(row["body_json"])
        for d in body["deliverables"]:
            d["status"] = "waived"
        c.execute(
            "UPDATE missions SET body_json=?, projection_hash=? WHERE mission_id=?",
            (to_canonical(body), hash_payload(body), m.mission_id),
        )
        self.assertFalse(self.rt.store.verify_integrity()["ok"])

    def test_hash_cannot_legitimize_rewrite(self):
        m = self.rt.create_mission_from_text("organism status", creation_idempotency_key="b3-hash")
        c = self.rt.store._c()
        row = c.execute("SELECT body_json FROM missions WHERE mission_id=?", (m.mission_id,)).fetchone()
        body = json.loads(row["body_json"])
        body["title"] = "rewritten"
        # attacker updates both body and hash
        c.execute(
            "UPDATE missions SET body_json=?, projection_hash=? WHERE mission_id=?",
            (to_canonical(body), hash_payload(body), m.mission_id),
        )
        # still fails rebuild compare
        issues = self.rt.store.verify_integrity()["issues"]
        self.assertTrue(any("rebuild_divergence" in i for i in issues))

    def test_full_slice_replay_matches(self):
        result = run_system_status_slice(self.rt, creation_idempotency_key="b3-slice")
        self.assertEqual(result["status"], "completed")
        replay = self.rt.replay(result["mission_id"])
        self.assertTrue(replay["rebuilt_matches_stored"])
        self.assertTrue(self.rt.store.verify_integrity()["ok"])

    def test_mission_created_anchors_body(self):
        m = self.rt.create_mission_from_text("organism status", creation_idempotency_key="b3-anchor")
        events = self.rt.events(m.mission_id)
        self.assertEqual(events[0]["event_type"], "mission_created")
        self.assertIn("mission_body", events[0]["payload"])
        self.assertEqual(events[0]["payload"]["mission_body"]["mission_id"], m.mission_id)


if __name__ == "__main__":
    unittest.main()
