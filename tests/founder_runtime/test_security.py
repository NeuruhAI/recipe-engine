"""Security and non-mutation tests."""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

FR = ROOT / "recipe_engine" / "founder_runtime"

FORBIDDEN_IMPORTS = {
    "socket", "subprocess", "requests", "http.client", "urllib.request",
    "paramiko", "psycopg", "supabase", "boto3",
}
FORBIDDEN_CALLS = {"system", "popen", "Popen", "urlopen", "eval", "exec"}


class SecurityTests(unittest.TestCase):
    def test_no_forbidden_imports_in_founder_runtime(self):
        violations = []
        for path in FR.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        if alias.name in FORBIDDEN_IMPORTS or root in FORBIDDEN_IMPORTS:
                            violations.append(f"{path}:{alias.name}")
                if isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".")[0]
                    if node.module in FORBIDDEN_IMPORTS or root in FORBIDDEN_IMPORTS:
                        violations.append(f"{path}:{node.module}")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id in FORBIDDEN_CALLS:
                        violations.append(f"{path}:call:{node.func.id}")
        self.assertEqual(violations, [], msg=str(violations))

    def test_no_env_secret_reads(self):
        for path in FR.rglob("*.py"):
            text = path.read_text()
            self.assertNotIn("os.environ", text)
            self.assertNotIn(".env", text)
            self.assertNotIn("load_dotenv", text)

    def test_adapter_mode_explicit(self):
        from recipe_engine.founder_runtime.adapters.fixture_axon import FixtureAxon
        from recipe_engine.founder_runtime.errors import AdapterModeInvalid
        with self.assertRaises(AdapterModeInvalid):
            FixtureAxon(mode="production")


if __name__ == "__main__":
    unittest.main()
