"""Package-wide static and full-slice runtime confinement tests (D23)."""
from __future__ import annotations

import ast
import builtins
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
PACKAGE = ROOT / "recipe_engine"

FORBIDDEN_ROOTS = {
    "socket", "subprocess", "requests", "http", "urllib", "paramiko",
    "psycopg", "supabase", "boto3", "importlib",
}
FORBIDDEN_NAMES = {"eval", "exec", "__import__", "open"}
ALLOWED_OS_CALLS = {"recipe_engine.founder_runtime.runtime.locks": {"getpid"}}
PATH_IO_METHODS = {"read_text", "read_bytes", "write_text", "write_bytes", "open", "unlink", "rename", "mkdir"}
ALLOWED_PATH_IO = {
    "recipe_engine.founder_runtime.cli.commands": {"read_text"},  # D29 explicit operator CLI input
    "recipe_engine.founder_runtime.persistence.sqlite": {"mkdir"},  # fixture DB parent only
}

# Founder Runtime API v1 (feat/founder-runtime-api-v1): the API is the operator-facing
# NETWORK BOUNDARY of the runtime. Its serving/client modules legitimately perform
# loopback HTTP and read an operator-supplied token file / write the OpenAPI spec.
# The mission KERNEL (service.py, persistence, runtime, autonomy) remains free of
# network I/O and is unchanged; these flags never reach the kernel.
API_BOUNDARY_ALLOWED_IMPORTS = {
    "recipe_engine.founder_runtime.api.client": {"urllib.request", "urllib.error"},  # loopback operator client
    "recipe_engine.founder_runtime.api.serve": {"urllib.request"},  # injected loopback organ prober
}
API_BOUNDARY_ALLOWED_PATH_IO = {
    "recipe_engine.founder_runtime.api.auth": {"read_text"},  # operator token file (FR_API_OPERATOR_TOKEN_FILE)
    "recipe_engine.founder_runtime.api.openapi": {"write_text"},  # emit machine-readable OpenAPI contract
    # T31B: the founder signing key, on the EXTERNAL DECISION side of the
    # boundary. Same shape as the operator token above — an operator-owned file
    # read once at construction, never returned, never logged. The mission side
    # holds no key at all and refuses to run live if it can see one.
    "recipe_engine.founder_runtime.api.founder_signing": {"read_text"},
}


def _module(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__": parts.pop()
    return ".".join(parts)


def reachable_modules() -> tuple[dict[str, Path], set[str]]:
    modules = {_module(p): p for p in PACKAGE.rglob("*.py")}
    graph: dict[str, set[str]] = {name: set() for name in modules}
    for name, path in modules.items():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                graph[name].update(a.name for a in node.names if a.name.startswith("recipe_engine"))
            elif isinstance(node, ast.ImportFrom) and node.module:
                base = node.module
                if node.level:
                    prefix = name.split(".")[:-node.level]
                    base = ".".join(prefix + ([node.module] if node.module else []))
                if base.startswith("recipe_engine"):
                    graph[name].add(base)
                    graph[name].update(f"{base}.{a.name}" for a in node.names)
    reached = {name for name in modules if name.startswith("recipe_engine.founder_runtime")}
    pending = list(reached)
    while pending:
        current = pending.pop()
        for target in graph.get(current, set()):
            candidates = [target] + [m for m in modules if target.startswith(m + ".")]
            for candidate in candidates:
                if candidate in modules and candidate not in reached:
                    reached.add(candidate); pending.append(candidate)
    return modules, reached


def scan_source(module: str, source: str) -> list[str]:
    tree = ast.parse(source)
    aliases: dict[str, str] = {}
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name
                if alias.name.split(".")[0] in FORBIDDEN_ROOTS: violations.append(f"import:{alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names: aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
            if node.module.split(".")[0] in FORBIDDEN_ROOTS: violations.append(f"from:{node.module}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_NAMES:
                violations.append(f"call:{node.func.id}")
            if isinstance(node.func, ast.Name) and node.func.id == "getattr":
                if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant): violations.append("dynamic:getattr")
            if isinstance(node.func, ast.Attribute):
                base = node.func.value.id if isinstance(node.func.value, ast.Name) else ""
                resolved = aliases.get(base, base)
                root = resolved.split(".")[0]
                if root in FORBIDDEN_ROOTS: violations.append(f"call:{resolved}.{node.func.attr}")
                if root == "os" and node.func.attr not in ALLOWED_OS_CALLS.get(module, set()):
                    violations.append(f"os:{node.func.attr}")
                if node.func.attr in PATH_IO_METHODS and node.func.attr not in ALLOWED_PATH_IO.get(module, set()):
                    violations.append(f"path-io:{node.func.attr}")
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            if isinstance(node.value.value, ast.Name) and aliases.get(node.value.value.id, node.value.value.id) == "os" and node.value.attr == "environ":
                violations.append("env:subscript")
    return violations


class SecurityTests(unittest.TestCase):
    def test_transitive_reachable_graph_is_clean(self):
        modules, reached = reachable_modules()
        self.assertTrue(any(name.startswith("recipe_engine.autonomy") for name in reached))
        violations = []
        for name in sorted(reached):
            allowed_imports = API_BOUNDARY_ALLOWED_IMPORTS.get(name, set())
            allowed_io = ALLOWED_PATH_IO.get(name, set()) | API_BOUNDARY_ALLOWED_PATH_IO.get(name, set())
            for v in scan_source(name, modules[name].read_text()):
                if v.startswith("import:") and v.split(":", 1)[1] in allowed_imports:
                    continue
                if v.startswith("path-io:") and v.split(":", 1)[1] in allowed_io:
                    continue
                violations.append(f"{name}:{v}")
        self.assertEqual(violations, [])

    def test_moved_aliased_helper_is_detected(self):
        samples = {
            "recipe_engine.helper": "import subprocess as harmless\nharmless.run(['x'])",
            "recipe_engine.helper2": "from socket import socket as connector\nconnector()",
            "recipe_engine.helper3": "__import__('requests')",
            "recipe_engine.helper4": "import os as config\nvalue=config.environ['TOKEN']",
            "recipe_engine.helper5": "target=getattr(object(), input())",
            "recipe_engine.helper6": "from pathlib import Path as P\nP('/tmp/x').write_text('x')",
        }
        for module, source in samples.items():
            self.assertTrue(scan_source(module, source), module)

    def test_full_fixture_slice_denies_runtime_io_escape(self):
        from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
        from recipe_engine.founder_runtime.clock import SteppingClock
        from recipe_engine.founder_runtime.vertical_slices.system_status import run_system_status_slice
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "fixture.sqlite"
            def denied(*args, **kwargs): raise AssertionError("forbidden runtime I/O")
            rt = MissionRuntime(db, clock=SteppingClock(), adapter_mode="fixture")
            with mock.patch.object(socket, "socket", denied), \
                 mock.patch.object(subprocess, "Popen", denied), \
                 mock.patch.object(subprocess, "run", denied), \
                 mock.patch.object(os, "getenv", denied), \
                 mock.patch.object(builtins, "open", denied):
                result = run_system_status_slice(rt, creation_idempotency_key="security-full-slice")
            rt.close()
            self.assertEqual(result["status"], "completed")
            self.assertTrue(all(p.name.startswith("fixture.sqlite") for p in Path(td).iterdir()))

    def test_adapter_mode_explicit(self):
        from recipe_engine.founder_runtime.adapters.fixture_axon import FixtureAxon
        from recipe_engine.founder_runtime.errors import AdapterModeInvalid
        with self.assertRaises(AdapterModeInvalid): FixtureAxon(mode="production")


if __name__ == "__main__": unittest.main()
