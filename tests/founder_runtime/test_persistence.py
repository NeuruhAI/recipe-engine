"""Persistence and event sourcing tests."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.persistence.sqlite import SqliteStore
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.errors import PersistenceConflict, StateTransitionInvalid
from recipe_engine.founder_runtime.contracts.events import MissionEvent, EventType, SCHEMA_EVENT
from recipe_engine.founder_runtime.ids import new_uuid


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.path = self.tmp.name
        self.tmp.close()
        self.clock = SteppingClock()
        self.rt = MissionRuntime(self.path, clock=self.clock, adapter_mode="fixture")

    def tearDown(self):
        self.rt.close()

    def test_create_and_persist(self):
        m = self.rt.create_mission_from_text("status check", creation_idempotency_key="k1")
        m2 = self.rt.get_mission(m.mission_id)
        self.assertEqual(m.mission_id, m2.mission_id)

    def test_duplicate_mission_idempotent(self):
        m1 = self.rt.create_mission_from_text("status", creation_idempotency_key="same")
        m2 = self.rt.create_mission_from_text("status", creation_idempotency_key="same")
        self.assertEqual(m1.mission_id, m2.mission_id)

    def test_append_only_events(self):
        m = self.rt.create_mission_from_text("x", creation_idempotency_key="e1")
        self.rt.transition(m.mission_id, MissionState.INTAKE, reason="in")
        events = self.rt.events(m.mission_id)
        self.assertGreaterEqual(len(events), 2)
        # cannot delete — only list grows
        n = len(events)
        self.rt.transition(m.mission_id, MissionState.CONTEXT_RESOLUTION, reason="ctx")
        self.assertEqual(len(self.rt.events(m.mission_id)), n + 1)

    def test_duplicate_event_idempotent(self):
        m = self.rt.create_mission_from_text("x", creation_idempotency_key="e2")
        self.rt.transition(m.mission_id, MissionState.INTAKE, reason="in", idempotency_key="idem-in")
        v1 = self.rt.get_mission(m.mission_id).version
        # re-append same event via store (duplicate delivery)
        from recipe_engine.founder_runtime.contracts.events import MissionEvent, EventType, SCHEMA_EVENT
        m = self.rt.get_mission(m.mission_id)
        ev = MissionEvent(
            schema_version=SCHEMA_EVENT,
            event_id="evt_dup",
            mission_id=m.mission_id,
            prior_state=MissionState.DRAFT,
            next_state=MissionState.INTAKE,
            event_type=EventType.STATE_TRANSITION,
            actor="runtime",
            reason="in",
            evidence_refs=[],
            timestamp=self.clock.now_iso(),
            idempotency_key="idem-in",
            causation_id=None,
            correlation_id=m.mission_id,
        )
        ev.compute_hash()
        again = self.rt.store.append_event(ev, expected_version=v1, mission_body=m)
        self.assertEqual(again.idempotency_key, "idem-in")
        v2 = self.rt.get_mission(m.mission_id).version
        self.assertEqual(v1, v2)

    def test_optimistic_version_conflict(self):
        m = self.rt.create_mission_from_text("x", creation_idempotency_key="e3")
        m = self.rt.transition(m.mission_id, MissionState.INTAKE, reason="in")
        # craft event with wrong version
        store = self.rt.store
        from recipe_engine.founder_runtime.contracts.events import MissionEvent, EventType, SCHEMA_EVENT
        ev = MissionEvent(
            schema_version=SCHEMA_EVENT,
            event_id=new_uuid("evt"),
            mission_id=m.mission_id,
            prior_state=MissionState.INTAKE,
            next_state=MissionState.CONTEXT_RESOLUTION,
            event_type=EventType.STATE_TRANSITION,
            actor="t",
            reason="r",
            evidence_refs=[],
            timestamp=self.clock.now_iso(),
            idempotency_key="badver",
            causation_id=None,
            correlation_id=m.mission_id,
        )
        ev.compute_hash()
        with self.assertRaises(PersistenceConflict):
            store.append_event(ev, expected_version=0, mission_body=m)

    def test_event_hash_tamper_detection(self):
        m = self.rt.create_mission_from_text("x", creation_idempotency_key="e4")
        self.rt.transition(m.mission_id, MissionState.INTAKE, reason="in")
        # tamper in DB
        c = self.rt.store._c()
        row = c.execute("SELECT event_id, body_json FROM mission_events WHERE mission_id=?", (m.mission_id,)).fetchone()
        import json
        body = json.loads(row["body_json"])
        body["event_hash"] = "0" * 64
        c.execute("UPDATE mission_events SET body_json=?, event_hash=? WHERE event_id=?",
                  (json.dumps(body), "0"*64, row["event_id"]))
        with self.assertRaises(Exception):
            self.rt.replay(m.mission_id)

    def test_restart_persistence(self):
        m = self.rt.create_mission_from_text("x", creation_idempotency_key="e5")
        mid = m.mission_id
        self.rt.transition(mid, MissionState.INTAKE, reason="in")
        self.rt.close()
        rt2 = MissionRuntime(self.path, clock=SteppingClock("2026-07-19T01:00:00.000Z"), adapter_mode="fixture")
        m2 = rt2.get_mission(mid)
        self.assertEqual(m2.current_state, MissionState.INTAKE)
        rt2.close()

    def test_projection_rebuild(self):
        m = self.rt.create_mission_from_text("x", creation_idempotency_key="e6")
        self.rt.transition(m.mission_id, MissionState.INTAKE, reason="in")
        self.rt.transition(m.mission_id, MissionState.CONTEXT_RESOLUTION, reason="ctx")
        replay = self.rt.replay(m.mission_id)
        self.assertEqual(replay["final_state"], "CONTEXT_RESOLUTION")

    def test_invalid_transition_rejected(self):
        m = self.rt.create_mission_from_text("x", creation_idempotency_key="e7")
        with self.assertRaises(StateTransitionInvalid):
            self.rt.transition(m.mission_id, MissionState.COMPLETED, reason="nope")


if __name__ == "__main__":
    unittest.main()
