"""P0 autonomy integration tests."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.autonomy import collapse_scenarios, default_config
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.vertical_slices.system_status import run_system_status_slice


class P0IntegrationTests(unittest.TestCase):
    def test_p0_api_used_and_max_four(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        path = tmp.name
        tmp.close()
        rt = MissionRuntime(path, clock=SteppingClock(), adapter_mode="fixture")
        result = run_system_status_slice(rt, creation_idempotency_key="p0i1")
        selected = result["packet"].get("selected") or []
        self.assertLessEqual(len(selected), 4)
        self.assertGreaterEqual(len(selected), 1)
        self.assertTrue(result["packet"].get("receipt_hash") or result["packet"].get("packet_id"))
        rt.close()

    def test_autonomy_exports(self):
        from recipe_engine import autonomy
        self.assertTrue(hasattr(autonomy, "collapse_scenarios"))
        self.assertTrue(hasattr(autonomy, "compile_execution_plan"))
        self.assertTrue(hasattr(autonomy, "reconcile_outcome"))


if __name__ == "__main__":
    unittest.main()
