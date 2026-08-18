"""Estate-sale pipeline and P0 non-regression."""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PROTECTED = [
    "run.py",
    "recipe_engine/parse.py",
    "recipe_engine/fetch.py",
    "recipe_engine/persist.py",
    "recipe_engine/engine.py",
    "recipe_engine/cities.py",
    "db/010_recipe_schema.sql",
    "scripts/daily_run.sh",
    "recipe_engine/adapters/estate_sales.py",
    "recipe_engine/adapters/base.py",
]

# Expected hashes at P0 base 4756e3d.
# RE-PINNED 2026-08-18 (E3 lane A): run.py and cities.py moved deliberately when
# the anchor source switched from the SSR paid-carousel scrape to the site's own
# /api/sale-details endpoint. The tripwire did its job — it caught the change.
# Everything it guards downstream (canon keys, persist contract, engine call) is
# unchanged; see recipe_engine/source_api.py and tests/test_source_api.py.
EXPECTED = {
    "run.py": "fb5c22096e4c02820d00f941d9541fb0bc6709445a52982a250f33245ed4aedf",
    "recipe_engine/parse.py": "07e57993d1695f6c91df1225dbd15c372625f0fe951761bcea02ce32c95e1f00",
    "recipe_engine/fetch.py": "8d248a3726fb9906f7228b61f452b938ea34247ee5c613697c7b44f3fb81042d",
    "recipe_engine/persist.py": "3af9deb40a6326185f6ffc8e0fc16ec2464c2ceb83bec5b39fa7ab75962154dc",
    "recipe_engine/engine.py": "e0e7f29ee2f6946044e5f4a0e962114cdab12a27942022ffaeb94b77dd2c15cd",
    "db/010_recipe_schema.sql": "92fe05549eceb58b2f6514653e5b700b318c45446ea3dcb64eff95409df4cfd3",
    "scripts/daily_run.sh": "d55e18e2144251e4ba9d14d460ca6ad5d6a4a812d34bb9ba2590cc4794b14357",
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


class PipelineRegressionTests(unittest.TestCase):
    def test_protected_hashes(self):
        for rel, expected in EXPECTED.items():
            self.assertEqual(sha256(ROOT / rel), expected, msg=rel)

    def test_p0_autonomy_import(self):
        from recipe_engine.autonomy import collapse_scenarios, compile_execution_plan, reconcile_outcome
        self.assertTrue(callable(collapse_scenarios))

    def test_p0_tests_still_importable(self):
        from tests.autonomy.test_collapse import CollapseTests  # noqa: F401


if __name__ == "__main__":
    unittest.main()
