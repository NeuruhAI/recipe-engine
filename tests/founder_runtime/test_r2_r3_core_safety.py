"""Core-level proofs for the two Founder Runtime residuals portable without the API layer.

R2 — per-mission event-chain integrity verification (SqliteStore.verify_mission_chain)
R3 — explicit non-executing authority truth (MissionState.APPROVED_NO_EXECUTION)

These are ported from recipe-engine PR #1 (head 68a34e8), whose own tests exercise the
same behavior through the HTTP Founder Runtime API. That API layer is not present on
main, so the behavior is re-proven here directly against the store and state machine.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.state.states import (
    ALL_STATES,
    MissionState,
    TERMINAL_STATES,
)
from recipe_engine.founder_runtime.state.transitions import is_transition_allowed


# --------------------------------------------------------------------------
# R3 — explicit non-executing authority truth
# --------------------------------------------------------------------------

class ApprovedNoExecutionStateTests(unittest.TestCase):
    """APPROVED_NO_EXECUTION is a real, reachable, non-executing parking state."""

    def test_state_exists_and_is_canonical(self):
        self.assertIn(MissionState.APPROVED_NO_EXECUTION, set(ALL_STATES))
        self.assertEqual(MissionState.APPROVED_NO_EXECUTION.value, "APPROVED_NO_EXECUTION")

    def test_state_is_not_terminal(self):
        # Operator lifecycle controls must remain available after approval.
        self.assertNotIn(MissionState.APPROVED_NO_EXECUTION, TERMINAL_STATES)

    def test_approval_can_park_without_execution(self):
        self.assertTrue(is_transition_allowed(
            MissionState.AWAITING_APPROVAL, MissionState.APPROVED_NO_EXECUTION))

    def test_no_path_to_queued_or_executing(self):
        # The entire point of the state: approval never confers execution authority.
        self.assertFalse(is_transition_allowed(
            MissionState.APPROVED_NO_EXECUTION, MissionState.QUEUED))
        self.assertFalse(is_transition_allowed(
            MissionState.APPROVED_NO_EXECUTION, MissionState.EXECUTING))

    def test_no_path_to_any_execution_adjacent_state(self):
        forbidden = {
            MissionState.QUEUED,
            MissionState.EXECUTING,
            MissionState.VERIFYING,
            MissionState.RECONCILING,
            MissionState.RECOVERING,
            MissionState.COMPLETED,
            MissionState.COMPLETED_WITH_EXCEPTIONS,
        }
        for target in forbidden:
            with self.subTest(target=target.value):
                self.assertFalse(is_transition_allowed(
                    MissionState.APPROVED_NO_EXECUTION, target))

    def test_operator_controls_remain(self):
        for target in (MissionState.CANCELLED, MissionState.PAUSED,
                       MissionState.BLOCKED_POLICY, MissionState.PLANNING):
            with self.subTest(target=target.value):
                self.assertTrue(is_transition_allowed(
                    MissionState.APPROVED_NO_EXECUTION, target))

    def test_paused_can_return_to_parked_approval(self):
        self.assertTrue(is_transition_allowed(
            MissionState.PAUSED, MissionState.APPROVED_NO_EXECUTION))

    def test_parking_state_cannot_be_reached_from_execution(self):
        # Not an execution off-ramp: it is an approval outcome only.
        self.assertFalse(is_transition_allowed(
            MissionState.EXECUTING, MissionState.APPROVED_NO_EXECUTION))
        self.assertFalse(is_transition_allowed(
            MissionState.QUEUED, MissionState.APPROVED_NO_EXECUTION))


# --------------------------------------------------------------------------
# R2 — event-chain integrity verification
# --------------------------------------------------------------------------

class MissionChainIntegrityTests(unittest.TestCase):
    """Every tamper mode must fail closed. The store is never mutated by verification."""

    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.path = tmp.name
        tmp.close()
        self.clock = SteppingClock()
        self.rt = MissionRuntime(self.path, clock=self.clock, adapter_mode="fixture")
        m = self.rt.create_mission_from_text("integrity subject", creation_idempotency_key="r2")
        self.mission_id = m.mission_id
        for nxt in (MissionState.INTAKE,
                    MissionState.CONTEXT_RESOLUTION,
                    MissionState.PLANNING):
            self.rt.transition(self.mission_id, nxt, reason="advance")
        self.store = self.rt.store

    def tearDown(self):
        self.rt.close()

    # -- direct sqlite tampering, bypassing the store's append path ----------

    def _raw(self):
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _tail_event_id(self) -> str:
        conn = self._raw()
        row = conn.execute(
            "SELECT event_id FROM mission_events WHERE mission_id=?"
            " ORDER BY sequence DESC LIMIT 1", (self.mission_id,)).fetchone()
        conn.close()
        return row["event_id"]

    def _issue_kinds(self, result) -> set[str]:
        return {i.split(":", 1)[0] for i in result["issues"]}

    # -- clean baseline ------------------------------------------------------

    def test_clean_chain_verifies(self):
        result = self.store.verify_mission_chain(self.mission_id)
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["issues"], [])
        self.assertGreaterEqual(result["event_count"], 4)

    def test_clean_store_verifies(self):
        self.assertTrue(self.store.verify_integrity()["ok"])

    def test_unknown_mission_fails_closed(self):
        result = self.store.verify_mission_chain("mission_does_not_exist")
        self.assertFalse(result["ok"])
        self.assertIn("mission_missing", self._issue_kinds(result))

    # -- tamper modes --------------------------------------------------------

    def test_event_hash_column_stomp_detected(self):
        conn = self._raw()
        conn.execute("UPDATE mission_events SET event_hash=? WHERE event_id=?",
                     ("f" * 64, self._tail_event_id()))
        conn.close()
        result = self.store.verify_mission_chain(self.mission_id)
        self.assertFalse(result["ok"])
        self.assertIn("event_hash_column_stomp", self._issue_kinds(result))

    def test_body_payload_tamper_detected(self):
        eid = self._tail_event_id()
        conn = self._raw()
        row = conn.execute("SELECT body_json FROM mission_events WHERE event_id=?",
                           (eid,)).fetchone()
        body = json.loads(row["body_json"])
        body["reason"] = "silently rewritten"
        conn.execute("UPDATE mission_events SET body_json=? WHERE event_id=?",
                     (json.dumps(body), eid))
        conn.close()
        result = self.store.verify_mission_chain(self.mission_id)
        self.assertFalse(result["ok"])
        self.assertIn("event_hash_mismatch", self._issue_kinds(result))

    def test_previous_hash_tamper_detected(self):
        eid = self._tail_event_id()
        conn = self._raw()
        row = conn.execute("SELECT body_json FROM mission_events WHERE event_id=?",
                           (eid,)).fetchone()
        body = json.loads(row["body_json"])
        body["previous_event_hash"] = "a" * 64
        conn.execute(
            "UPDATE mission_events SET body_json=?, previous_event_hash=? WHERE event_id=?",
            (json.dumps(body), "a" * 64, eid))
        conn.close()
        result = self.store.verify_mission_chain(self.mission_id)
        self.assertFalse(result["ok"])
        self.assertIn("previous_hash_tamper", self._issue_kinds(result))

    def test_tail_truncation_detected(self):
        conn = self._raw()
        conn.execute("DELETE FROM mission_events WHERE event_id=?", (self._tail_event_id(),))
        conn.close()
        result = self.store.verify_mission_chain(self.mission_id)
        self.assertFalse(result["ok"])
        self.assertIn("tail_version_mismatch", self._issue_kinds(result))

    def test_interior_deletion_detected(self):
        conn = self._raw()
        row = conn.execute(
            "SELECT event_id FROM mission_events WHERE mission_id=?"
            " ORDER BY sequence ASC LIMIT 1 OFFSET 1", (self.mission_id,)).fetchone()
        conn.execute("DELETE FROM mission_events WHERE event_id=?", (row["event_id"],))
        conn.close()
        result = self.store.verify_mission_chain(self.mission_id)
        self.assertFalse(result["ok"])
        kinds = self._issue_kinds(result)
        # a hole breaks both the hash chain and positional contiguity
        self.assertTrue({"chain_position_gap", "previous_hash_tamper"} & kinds, kinds)

    def test_chain_position_column_stomp_detected(self):
        conn = self._raw()
        conn.execute("UPDATE mission_events SET chain_position=99 WHERE event_id=?",
                     (self._tail_event_id(),))
        conn.close()
        result = self.store.verify_mission_chain(self.mission_id)
        self.assertFalse(result["ok"])
        self.assertIn("chain_position_column_stomp", self._issue_kinds(result))

    def test_timestamp_column_stomp_detected(self):
        conn = self._raw()
        conn.execute("UPDATE mission_events SET timestamp=? WHERE event_id=?",
                     ("1999-01-01T00:00:00Z", self._tail_event_id()))
        conn.close()
        result = self.store.verify_mission_chain(self.mission_id)
        self.assertFalse(result["ok"])
        self.assertIn("timestamp_column_stomp", self._issue_kinds(result))

    def test_cross_mission_splice_detected(self):
        other = self.rt.create_mission_from_text("other", creation_idempotency_key="r2b")
        eid = self._tail_event_id()
        conn = self._raw()
        row = conn.execute("SELECT body_json FROM mission_events WHERE event_id=?",
                           (eid,)).fetchone()
        body = json.loads(row["body_json"])
        body["mission_id"] = other.mission_id
        conn.execute("UPDATE mission_events SET body_json=? WHERE event_id=?",
                     (json.dumps(body), eid))
        conn.close()
        result = self.store.verify_mission_chain(self.mission_id)
        self.assertFalse(result["ok"])
        self.assertIn("cross_mission_splice", self._issue_kinds(result))

    def test_verification_does_not_mutate_store(self):
        conn = self._raw()
        before = conn.execute(
            "SELECT count(*) c FROM mission_events WHERE mission_id=?",
            (self.mission_id,)).fetchone()["c"]
        conn.close()
        self.store.verify_mission_chain(self.mission_id)
        self.store.verify_integrity()
        conn = self._raw()
        after = conn.execute(
            "SELECT count(*) c FROM mission_events WHERE mission_id=?",
            (self.mission_id,)).fetchone()["c"]
        conn.close()
        self.assertEqual(before, after)

    def test_store_verification_surfaces_chain_failure(self):
        # A per-mission chain break must also fail the whole-store verdict.
        conn = self._raw()
        conn.execute("UPDATE mission_events SET event_hash=? WHERE event_id=?",
                     ("f" * 64, self._tail_event_id()))
        conn.close()
        self.assertFalse(self.store.verify_integrity()["ok"])

    # -- concurrent missions are not a chain break --------------------------

    def test_interleaved_missions_do_not_break_either_chain(self):
        """Two live missions share one autoincrement, so neither owns a contiguous range.

        Reproduced from a live Founder Runtime store where /v1/missions returned
        INTEGRITY_FAILURE "sequence_gap" for a mission whose own chain was intact: its
        events occupied global sequences 161-169 and 180, because a second mission had
        written 170-179 in between. Verification must judge the mission's own chain,
        not whether anything else wrote to the table while it was open.
        """
        other = self.rt.create_mission_from_text("interleaved", creation_idempotency_key="r2b")
        for nxt in (MissionState.INTAKE, MissionState.CONTEXT_RESOLUTION):
            self.rt.transition(other.mission_id, nxt, reason="advance")
        # subject advances again, so its sequences now straddle the other mission's
        self.rt.transition(self.mission_id, MissionState.AWAITING_APPROVAL, reason="advance")

        conn = self._raw()
        subject = [r["sequence"] for r in conn.execute(
            "SELECT sequence FROM mission_events WHERE mission_id=? ORDER BY sequence",
            (self.mission_id,))]
        conn.close()
        gaps = [b - a for a, b in zip(subject, subject[1:])]
        self.assertTrue(any(step > 1 for step in gaps),
                        f"fixture did not interleave; steps={gaps}")

        for mid in (self.mission_id, other.mission_id):
            result = self.store.verify_mission_chain(mid)
            self.assertTrue(result["ok"], f"{mid}: {result['issues']}")
        self.assertTrue(self.store.verify_integrity()["ok"])

    def test_reorder_against_store_append_order_is_still_detected(self):
        """Dropping the contiguity rule must not drop ordering.

        Events are read ORDER BY sequence, so swapping two rows' sequences reorders the
        chain as the verifier sees it. chain_position then lands where the next position
        was expected, which is the property the old contiguity rule was reaching for.
        """
        conn = self._raw()
        rows = conn.execute(
            "SELECT sequence, event_id FROM mission_events WHERE mission_id=?"
            " ORDER BY sequence", (self.mission_id,)).fetchall()
        first, second = rows[0], rows[1]
        spare = conn.execute("SELECT MAX(sequence)+1000 m FROM mission_events").fetchone()["m"]
        conn.execute("UPDATE mission_events SET sequence=? WHERE event_id=?",
                     (spare, first["event_id"]))
        conn.execute("UPDATE mission_events SET sequence=? WHERE event_id=?",
                     (first["sequence"], second["event_id"]))
        conn.execute("UPDATE mission_events SET sequence=? WHERE event_id=?",
                     (second["sequence"], first["event_id"]))
        conn.close()
        result = self.store.verify_mission_chain(self.mission_id)
        self.assertFalse(result["ok"])
        self.assertIn("chain_position_gap", self._issue_kinds(result))

    def test_tail_sequence_is_the_last_appended_row(self):
        conn = self._raw()
        expected = conn.execute(
            "SELECT MAX(sequence) m FROM mission_events WHERE mission_id=?",
            (self.mission_id,)).fetchone()["m"]
        conn.close()
        self.assertEqual(
            self.store.verify_mission_chain(self.mission_id)["tail_sequence"], expected)


if __name__ == "__main__":
    unittest.main()
