"""Recovery and power-loss simulation tests."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.vertical_slices.system_status import run_system_status_slice
from recipe_engine.founder_runtime.runtime.recovery import RecoveryStatus


class RecoveryTests(unittest.TestCase):
    def test_power_loss_after_two_steps(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        path = tmp.name
        tmp.close()
        clock = SteppingClock()
        rt = MissionRuntime(path, clock=clock, adapter_mode="fixture")
        partial = run_system_status_slice(
            rt,
            creation_idempotency_key="pl1",
            simulate_power_loss_after_steps=2,
        )
        self.assertEqual(partial["status"], "simulated_power_loss")
        self.assertEqual(len(partial["completed_steps"]), 2)
        mid = partial["mission_id"]
        # side effects present
        ses = rt.store.list_side_effects(mid)
        self.assertTrue(ses)
        rt.close()

        # reopen — simulate process loss
        rt2 = MissionRuntime(path, clock=SteppingClock("2026-07-19T02:00:00.000Z"), adapter_mode="fixture")
        rec = rt2.recover(mid)
        self.assertEqual(rec["status"], RecoveryStatus.SAFE_TO_RESUME.value)
        # completed steps must not be rerun
        self.assertIn("read_mother_health", rec["do_not_rerun"])
        self.assertIn("read_github_state", rec["do_not_rerun"])
        # receipts exist for completed
        receipts = rt2.store.list_receipts(mid)
        self.assertEqual(len(receipts), 2)
        # duplicate acknowledgement does not duplicate receipt
        r0 = receipts[0]
        again = rt2.record_receipt(r0)
        self.assertEqual(again.receipt_id, r0.receipt_id)
        self.assertEqual(len(rt2.store.list_receipts(mid)), 2)
        rt2.close()

    def test_full_slice_restart_replay_identical(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        path = tmp.name
        tmp.close()
        rt = MissionRuntime(path, clock=SteppingClock(), adapter_mode="fixture")
        result = run_system_status_slice(rt, creation_idempotency_key="full1")
        self.assertEqual(result["status"], "completed")
        mid = result["mission_id"]
        replay1 = rt.replay(mid)
        rt.close()
        rt2 = MissionRuntime(path, clock=SteppingClock("2026-07-19T03:00:00.000Z"), adapter_mode="fixture")
        replay2 = rt2.replay(mid)
        self.assertEqual(replay1["final_state"], replay2["final_state"])
        self.assertEqual(replay1["projection_hash"], replay2["projection_hash"])
        self.assertEqual(replay1["event_count"], replay2["event_count"])
        self.assertEqual(rt2.get_mission(mid).current_state.value, "COMPLETED")
        rt2.close()


if __name__ == "__main__":
    unittest.main()
