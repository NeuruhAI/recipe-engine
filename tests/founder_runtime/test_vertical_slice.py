"""Vertical slice acceptance tests."""
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
from recipe_engine.founder_runtime.errors import AdapterModeInvalid


class VerticalSliceTests(unittest.TestCase):
    def test_complete_system_status(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        path = tmp.name
        tmp.close()
        rt = MissionRuntime(path, clock=SteppingClock(), adapter_mode="fixture")
        result = run_system_status_slice(rt, creation_idempotency_key="vs1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["final_state"], "COMPLETED")
        self.assertFalse(result["mutation"])
        self.assertEqual(result["adapter_mode"], "fixture")
        self.assertIsNotNone(result.get("completion_hash"))
        self.assertEqual(result["replay"]["final_state"], "COMPLETED")
        # plan remains proposal
        self.assertTrue(result.get("plan_proposal") is not None)
        insp = rt.inspect(result["mission_id"])
        self.assertTrue(insp["completion"])
        self.assertGreaterEqual(len(insp["receipts"]), 5)
        self.assertGreaterEqual(len(insp["events"]), 5)
        rt.close()

    def test_live_mode_rejected(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        path = tmp.name
        tmp.close()
        with self.assertRaises(AdapterModeInvalid):
            MissionRuntime(path, adapter_mode="live")

    def test_blocked_input_precise_question(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        path = tmp.name
        tmp.close()
        rt = MissionRuntime(path, clock=SteppingClock(), adapter_mode="fixture")
        result = run_system_status_slice(rt, request_text="???", creation_idempotency_key="blk1")
        self.assertEqual(result["status"], "blocked_input")
        self.assertTrue(result.get("question"))
        rt.close()


if __name__ == "__main__":
    unittest.main()
