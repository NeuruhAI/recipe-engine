"""B4 — plan compile failure never yields COMPLETED."""
from __future__ import annotations
import sys, tempfile, unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.vertical_slices.system_status import run_system_status_slice
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.autonomy.errors import PlanningError


class B4PlanFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.path = self.tmp.name
        self.tmp.close()
        self.rt = MissionRuntime(self.path, clock=SteppingClock(), adapter_mode="fixture")

    def tearDown(self):
        self.rt.close()

    def test_compile_exception_fails_mission(self):
        with mock.patch(
            "recipe_engine.founder_runtime.vertical_slices.system_status.compile_execution_plan",
            side_effect=PlanningError("boom", {"x": 1}),
        ):
            result = run_system_status_slice(self.rt, creation_idempotency_key="b4-exc")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["final_state"], "FAILED_FINAL")
        self.assertIsNone(result.get("completion_hash"))
        m = self.rt.get_mission(result["mission_id"])
        self.assertEqual(m.current_state, MissionState.FAILED_FINAL)
        # no completion report
        self.assertIsNone(self.rt.store.get_completion(result["mission_id"]))
        events = self.rt.events(result["mission_id"])
        self.assertTrue(any(e["event_type"] == "plan_compile_failed" for e in events))

    def test_no_fallback_to_completed(self):
        with mock.patch(
            "recipe_engine.founder_runtime.vertical_slices.system_status.compile_execution_plan",
            side_effect=RuntimeError("unexpected"),
        ):
            result = run_system_status_slice(self.rt, creation_idempotency_key="b4-no-comp")
        self.assertNotEqual(result.get("final_state"), "COMPLETED")
        self.assertNotEqual(result.get("status"), "completed")

    def test_replay_after_failure(self):
        with mock.patch(
            "recipe_engine.founder_runtime.vertical_slices.system_status.compile_execution_plan",
            side_effect=PlanningError("boom", {}),
        ):
            result = run_system_status_slice(self.rt, creation_idempotency_key="b4-replay")
        replay = self.rt.replay(result["mission_id"])
        self.assertEqual(replay["final_state"], "FAILED_FINAL")
        self.assertTrue(replay["rebuilt_matches_stored"])

    def test_completion_rejected_on_failed_mission(self):
        with mock.patch(
            "recipe_engine.founder_runtime.vertical_slices.system_status.compile_execution_plan",
            side_effect=PlanningError("boom", {}),
        ):
            result = run_system_status_slice(self.rt, creation_idempotency_key="b4-rej")
        self.assertIsNone(self.rt.store.get_completion(result["mission_id"]))

    def test_detail_bounded_no_secret_leak_pattern(self):
        secretish = "password=SUPERSECRET " + ("x" * 2000)
        with mock.patch(
            "recipe_engine.founder_runtime.vertical_slices.system_status.compile_execution_plan",
            side_effect=RuntimeError(secretish),
        ):
            result = run_system_status_slice(self.rt, creation_idempotency_key="b4-bound")
        detail = result.get("detail") or ""
        self.assertLessEqual(len(detail), 520)
        # failure event payload bounded
        for e in self.rt.events(result["mission_id"]):
            if e["event_type"] == "plan_compile_failed":
                self.assertLessEqual(len(e["payload"].get("detail") or ""), 500)


if __name__ == "__main__":
    unittest.main()
