"""Security isolation + estate-sale pipeline non-regression."""

from __future__ import annotations

import ast
import hashlib
import importlib
import socket
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PROTECTED = [
    "run.py",
    "scripts/daily_run.sh",
    "db/010_recipe_schema.sql",
    "recipe_engine/engine.py",
    "recipe_engine/parse.py",
    "recipe_engine/persist.py",
    "recipe_engine/fetch.py",
    "recipe_engine/cities.py",
]

FORBIDDEN_IMPORTS = {
    "requests",
    "psycopg",
    "httpx",
    "urllib",
    "socket",
    "subprocess",
    "asyncio",
    "http",
    "ftplib",
    "paramiko",
    "qdrant_client",
    "openai",
    "anthropic",
}


class PipelineRegressionTests(unittest.TestCase):
    def test_protected_files_unchanged(self):
        # expected hashes from pre-implementation baseline (computed at worktree create)
        expected = {
            "run.py": "5e780f9fe4e76e5d47ec3d1df923a8a0fafe5c2c54aaf699a06fb0c4aa01cb52",
            "scripts/daily_run.sh": "d55e18e2144251e4ba9d14d460ca6ad5d6a4a812d34bb9ba2590cc4794b14357",
            "db/010_recipe_schema.sql": "92fe05549eceb58b2f6514653e5b700b318c45446ea3dcb64eff95409df4cfd3",
            "recipe_engine/engine.py": "e0e7f29ee2f6946044e5f4a0e962114cdab12a27942022ffaeb94b77dd2c15cd",
            "recipe_engine/parse.py": "07e57993d1695f6c91df1225dbd15c372625f0fe951761bcea02ce32c95e1f00",
            "recipe_engine/persist.py": "3af9deb40a6326185f6ffc8e0fc16ec2464c2ceb83bec5b39fa7ab75962154dc",
            "recipe_engine/fetch.py": "8d248a3726fb9906f7228b61f452b938ea34247ee5c613697c7b44f3fb81042d",
            "recipe_engine/cities.py": "4a803fa3e44328bb136c58345f625a701553571e63af33103617c7665818fbad",
        }
        for rel, exp in expected.items():
            data = (ROOT / rel).read_bytes()
            got = hashlib.sha256(data).hexdigest()
            self.assertEqual(got, exp, f"protected file changed: {rel}")

    def test_canon_keys_unchanged(self):
        from recipe_engine.parse import canon_keys
        from recipe_engine.adapters.estate_sales import canon_keys as adapter_keys

        self.assertEqual(canon_keys("4937331"), adapter_keys("4937331"))
        self.assertEqual(canon_keys("4937331")["parcel_apn"], "estate-4937331")
        self.assertEqual(len(canon_keys("4937331")["owner_hash"]), 64)

    def test_run_help_or_argparse(self):
        # static: run.py still defines argparse without executing pipeline
        src = (ROOT / "run.py").read_text()
        self.assertIn("argparse", src)
        self.assertIn("--no-engine", src)
        # compile only
        compile(src, "run.py", "exec")

    def test_existing_package_imports(self):
        import recipe_engine.cities
        import recipe_engine.parse
        import recipe_engine.engine

        self.assertTrue(hasattr(recipe_engine.parse, "canon_keys"))
        self.assertTrue(hasattr(recipe_engine.engine, "LandosEngine"))


class AutonomyPurityTests(unittest.TestCase):
    def test_autonomy_forbidden_imports(self):
        auto_dir = ROOT / "recipe_engine" / "autonomy"
        for path in auto_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        self.assertNotIn(root, FORBIDDEN_IMPORTS, path.name)
                if isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".")[0]
                    self.assertNotIn(root, FORBIDDEN_IMPORTS, path.name)

    def test_no_eval_exec_in_autonomy(self):
        auto_dir = ROOT / "recipe_engine" / "autonomy"
        for path in auto_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"eval", "exec", "compile", "__import__"})

    def test_collapse_does_not_open_sockets(self):
        from recipe_engine.autonomy import collapse_scenarios, default_config
        from tests.autonomy.fixtures.builders import domain_pack

        orig = socket.socket

        def blocked(*a, **k):
            raise AssertionError("socket opened during autonomy collapse")

        socket.socket = blocked  # type: ignore
        try:
            opp, strats = domain_pack("non_re_ops")
            collapse_scenarios(opp, strats, default_config())
        finally:
            socket.socket = orig

    def test_no_subprocess_during_collapse(self):
        from recipe_engine.autonomy import collapse_scenarios, default_config
        from tests.autonomy.fixtures.builders import domain_pack

        orig = subprocess.Popen

        def blocked(*a, **k):
            raise AssertionError("subprocess spawned")

        subprocess.Popen = blocked  # type: ignore
        try:
            opp, strats = domain_pack("business_acquisition")
            collapse_scenarios(opp, strats, default_config())
        finally:
            subprocess.Popen = orig


if __name__ == "__main__":
    unittest.main()
