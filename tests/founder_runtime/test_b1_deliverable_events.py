"""B1 — event-sourced deliverable status adversarial tests."""
from __future__ import annotations
import json, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.contracts.mission import DeliverableStatus
from recipe_engine.founder_runtime.errors import (
    MissionSchemaInvalid, StateTransitionInvalid, PersistenceConflict,
)
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.serialization import to_canonical


class B1DeliverableEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.path = self.tmp.name
        self.tmp.close()
        self.rt = MissionRuntime(self.path, clock=SteppingClock(), adapter_mode="fixture")

    def tearDown(self):
        self.rt.close()

    def _mission(self):
        return self.rt.create_mission_from_text(
            "Tell me where the Neuruh organism is",
            creation_idempotency_key="b1-" + self.id(),
        )

    def test_valid_change(self):
        m = self._mission()
        v0 = m.version
        m2 = self.rt.set_deliverable_status(m.mission_id, "del_status_report", DeliverableStatus.DELIVERED)
        self.assertEqual(m2.version, v0 + 1)
        d = next(x for x in m2.deliverables if x.deliverable_id == "del_status_report")
        self.assertEqual(d.status, DeliverableStatus.DELIVERED)
        events = self.rt.events(m.mission_id)
        self.assertTrue(any(e["event_type"] == "deliverable_status_changed" for e in events))

    def test_stale_version(self):
        m = self._mission()
        # craft event with wrong expected version via store
        from recipe_engine.founder_runtime.contracts.events import MissionEvent, EventType, SCHEMA_EVENT
        from recipe_engine.founder_runtime.ids import new_uuid
        ev = MissionEvent(
            schema_version=SCHEMA_EVENT, event_id=new_uuid("evt"), mission_id=m.mission_id,
            prior_state=m.current_state, next_state=m.current_state,
            event_type=EventType.DELIVERABLE_STATUS_CHANGED, actor="t", reason="r",
            evidence_refs=[], timestamp=self.rt.clock.now_iso(),
            idempotency_key="stale-deliv", causation_id=None, correlation_id=m.mission_id,
            payload={"deliverable_id": "del_status_report", "prior_status": "pending", "next_status": "delivered"},
        )
        ev.compute_hash()
        with self.assertRaises(PersistenceConflict):
            self.rt.store.append_event(ev, expected_version=0)  # actual is >=1 after create

    def test_invalid_deliverable(self):
        m = self._mission()
        with self.assertRaises(MissionSchemaInvalid):
            self.rt.set_deliverable_status(m.mission_id, "no_such_deliv", DeliverableStatus.DELIVERED)

    def test_invalid_status(self):
        m = self._mission()
        with self.assertRaises(MissionSchemaInvalid):
            self.rt.set_deliverable_status(m.mission_id, "del_status_report", "not_a_status")  # type: ignore

    def test_duplicate_event(self):
        m = self._mission()
        m1 = self.rt.set_deliverable_status(
            m.mission_id, "del_status_report", DeliverableStatus.IN_PROGRESS,
            idempotency_key="dup-deliv-1",
        )
        v = m1.version
        m2 = self.rt.set_deliverable_status(
            m.mission_id, "del_status_report", DeliverableStatus.IN_PROGRESS,
            idempotency_key="dup-deliv-1",
        )
        # second is no-op at status equality OR idempotent event — version should not double
        # if status already IN_PROGRESS, set_deliverable returns early without event
        self.assertEqual(self.rt.get_mission(m.mission_id).version, v)

    def test_terminal_mission(self):
        m = self._mission()
        # force to CANCELLED
        self.rt.transition(m.mission_id, MissionState.CANCELLED, reason="cancel")
        with self.assertRaises(StateTransitionInvalid):
            self.rt.set_deliverable_status(m.mission_id, "del_status_report", DeliverableStatus.DELIVERED)

    def test_replay_reconstruction(self):
        m = self._mission()
        self.rt.set_deliverable_status(m.mission_id, "del_status_report", DeliverableStatus.DELIVERED)
        self.rt.set_deliverable_status(m.mission_id, "del_next_plan", DeliverableStatus.DELIVERED)
        replay = self.rt.replay(m.mission_id)
        self.assertTrue(replay["rebuilt_matches_stored"])
        dels = {d["deliverable_id"]: d["status"] for d in replay["mission"]["deliverables"]}
        self.assertEqual(dels["del_status_report"], "delivered")
        self.assertEqual(dels["del_next_plan"], "delivered")

    def test_direct_body_tamper_detected(self):
        m = self._mission()
        self.rt.set_deliverable_status(m.mission_id, "del_status_report", DeliverableStatus.DELIVERED)
        # tamper body + re-sign projection_hash
        c = self.rt.store._c()
        row = c.execute("SELECT body_json FROM missions WHERE mission_id=?", (m.mission_id,)).fetchone()
        body = json.loads(row["body_json"])
        body["objective"] = "TAMPERED_OBJECTIVE"
        for d in body["deliverables"]:
            if d["deliverable_id"] == "del_status_report":
                d["status"] = "pending"  # undo event
        new_hash = hash_payload(body)
        c.execute(
            "UPDATE missions SET body_json=?, projection_hash=? WHERE mission_id=?",
            (to_canonical(body), new_hash, m.mission_id),
        )
        integrity = self.rt.store.verify_integrity()
        self.assertFalse(integrity["ok"])
        self.assertTrue(any("rebuild_divergence" in i or "projection" in i for i in integrity["issues"]))
        with self.assertRaises(PersistenceConflict):
            self.rt.replay(m.mission_id)

    def test_no_raw_mutation_path(self):
        src = Path(ROOT / "recipe_engine/founder_runtime/runtime/mission_runtime.py").read_text()
        self.assertNotIn("UPDATE missions SET body_json=?, projection_hash=? WHERE mission_id=? AND version=?", src)
        self.assertIn("DELIVERABLE_STATUS_CHANGED", src)


if __name__ == "__main__":
    unittest.main()
