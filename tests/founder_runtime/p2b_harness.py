"""Deterministic loopback test harness for the P2-B live adapters.

Real live I/O — a threaded HTTP server bound to 127.0.0.1 and real temp git
repos — so the adapters exercise their genuine transports with zero external
dependency.  (Test-support module: imported by tests, never by the package under
the security scan.)
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping

from recipe_engine.live_adapters.settings import LiveAdapterSettings, LiveEndpoint, RepoRoot
from recipe_engine.live_adapters.transport import LoopbackHttpClient


# ------------------------------------------------------------------ HTTP mock
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # silence
        return

    def _dispatch(self) -> None:
        server: "MockService" = self.server  # type: ignore[assignment]
        if server.delay:
            time.sleep(server.delay)
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        key = (self.command, self.path.split("?", 1)[0])
        route = server.routes.get(key) or server.routes.get(("*", self.path.split("?", 1)[0]))
        server.received.append({"method": self.command, "path": self.path, "auth": self.headers.get("x-axon-secret") or self.headers.get("Authorization")})
        if route is None:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"no route"}')
            return
        status, payload = route(body) if callable(route) else route
        raw = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = _dispatch
    do_POST = _dispatch


class MockService(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, routes: Mapping[tuple, Any], delay: float = 0.0):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.routes = dict(routes)
        self.delay = delay
        self.received: list[dict] = []


@contextmanager
def mock_service(routes: Mapping[tuple, Any], delay: float = 0.0):
    srv = MockService(routes, delay=delay)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, srv
    finally:
        srv.shutdown()
        srv.server_close()


def endpoint(service_id: str, port: int, *, auth_header: str | None = None, auth_secret: str | None = None) -> LiveEndpoint:
    return LiveEndpoint(
        service_id=service_id, host="127.0.0.1", port=port, endpoint_class="loopback",
        auth_header=auth_header, auth_secret=auth_secret,
    )


def http_client(*endpoints: LiveEndpoint) -> LoopbackHttpClient:
    return LoopbackHttpClient({e.service_id: e for e in endpoints})


def settings_with(*, endpoints: tuple[LiveEndpoint, ...] = (), repo_roots: tuple[RepoRoot, ...] = (),
                  host_recovery_audit_root: str | None = None, **kw: Any) -> LiveAdapterSettings:
    return LiveAdapterSettings(
        repo_roots=repo_roots, endpoints=endpoints,
        host_recovery_audit_root=host_recovery_audit_root,
        recipe_engine_sha=kw.pop("recipe_engine_sha", "test-sha"),
        axon_source_sha=kw.pop("axon_source_sha", ""),
        governance_source_sha=kw.pop("governance_source_sha", ""),
        **kw,
    )


# ------------------------------------------------------------------ git repos
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/tmp", "PATH": "/usr/bin:/bin",
    "GIT_TERMINAL_PROMPT": "0",
}


def make_git_repo(path: Path, *, origin: str | None = None, files: Mapping[str, str] | None = None,
                  dirty: bool = False, extra_commit: bool = False) -> str:
    path.mkdir(parents=True, exist_ok=True)

    def g(*args: str) -> str:
        return subprocess.run(["git", "-C", str(path), *args], env=_GIT_ENV,
                              capture_output=True, text=True, check=True).stdout.strip()

    g("init", "-q", "-b", "main")
    for name, content in (files or {"README.md": "hello\n"}).items():
        (path / name).write_text(content)
    g("add", "-A")
    g("commit", "-q", "-m", "initial")
    if extra_commit:
        (path / "more.txt").write_text("more\n")
        g("add", "-A")
        g("commit", "-q", "-m", "second")
    if origin:
        g("remote", "add", "origin", origin)
    if dirty:
        (path / "README.md").write_text("changed\n")
    return g("rev-parse", "HEAD")


def make_green_host_audit(root: Path) -> str:
    """Create a sealed GREEN host-recovery audit satisfying the 5 criteria."""
    audit = root / "20260722T000000Z"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "FINAL_VERDICT.md").write_text(
        "# FINAL VERDICT — Host recovery\n\n## VERDICT: GREEN (recovery complete)\n\n"
        "Data integrity green; runtime restored; volumes preserved.\n"
    )
    (audit / "QDRANT_INTEGRITY.md").write_text(
        "# QDRANT READ-ONLY INTEGRITY — PASS\n\npoints_count: 3049; exact 3049.\n"
        "Bounded read-only scroll OK. Recount 3049.\n"
    )
    (audit / "REDIS_INTEGRITY.md").write_text("# REDIS INTEGRITY — PASS\nPING PONG; persistence ok.\n")
    (audit / "IO_ERROR_ASSESSMENT.md").write_text("# I/O ASSESSMENT\ncontinuing I/O corruption: NONE. Store healthy.\n")
    (audit / "DOCKER_VOLUME_LEDGER.csv").write_text("volume,present\nneuruh-qdrant-data,yes\n")
    (audit / "SHUTDOWN_STARTUP_LEDGER.md").write_text("All 5 volumes present & mapped; none recreated.\n")
    return str(audit)


# ------------------------------------------------------------------ canned bodies
def qdrant_routes(count: int = 3049, manifest_id: str = "vault-canon-remediated-20260718T054050Z-cct22",
                  count_after: int | None = None) -> dict:
    ca = count if count_after is None else count_after
    return {
        ("GET", "/readyz"): (200, {}),
        ("GET", "/"): (200, {"title": "qdrant", "version": "1.13.2"}),
        ("GET", "/collections/neuruh_vault_canon"): (200, {"result": {"status": "green", "points_count": count}, "status": "ok"}),
        ("POST", "/collections/neuruh_vault_canon/points/count"): _count_toggle(count, ca),
        ("POST", "/collections/neuruh_vault_canon/points/scroll"):
            (200, {"result": {"points": [{"id": 907989612130417, "payload": {"manifest_id": manifest_id}}], "next_page_offset": None}, "status": "ok"}),
    }


def _count_toggle(before: int, after: int):
    state = {"n": 0}

    def route(_body: bytes):
        state["n"] += 1
        val = before if state["n"] == 1 else after
        return (200, {"result": {"count": val}, "status": "ok"})

    return route


def axon_identity_body() -> dict:
    return {"status": "healthy", "version": "2.4.0-p1.1",
            "inference": {"authority": "iar", "provider": "ollama-bonsai", "model": "bonsai", "health": True},
            "execution_available": True, "degraded_reason": None}


def axon_governed_read_body(path: str = "README.md") -> dict:
    from recipe_engine.founder_runtime.ids import digest
    return {"taskId": "task-1", "status": "completed", "governance": {"decision": "allow"},
            "subtasks": [{"tool": "file_read", "status": "succeeded", "path": path}],
            "path": path, "output_hash": digest("hello\n"), "mutation": False}


def iar_health_body() -> dict:
    return {"enabled": True, "active_backend": "ollama-bonsai", "active_model": "bonsai",
            "backends": {"ollama-bonsai": True}, "degraded_reason": None,
            "budget_policy": {"cap_usd": 0}, "config_provenance": "live"}


def iar_provenance_body() -> dict:
    return {"provenance_intact": True, "binding": {
        "pid": 4242, "command": "iar", "cwd": "/x", "repo_sha": "abc123",
        "ledger_path": "/abs/ledger.jsonl", "ledger_line_count": 143,
        "ledger_sha256_prefix": "fae0db89abcdef", "genesis_continuity": True}}


def governance_decision_body(action: dict) -> dict:
    return {"action_id": action["action_id"], "decision": "allow", "risk_score": 0.1,
            "thresholds": {"block": 0.9}, "reasons": [], "required_next_steps": [],
            "audit_id": "aud-1", "action": dict(action), "created_at": "2026-07-21T00:00:00Z"}


def aegis_world_body(world: str = "neuruh_status") -> dict:
    return {"world": world, "has_compliance_contract": False, "compliance_required": False,
            "valid_for_composition": True, "verdict": "VALID_NO_CONTRACT",
            "hard_conflicts": [], "soft_notes": [], "advisories": [], "offending_nodes": [],
            "summary": "advisory only"}


def mother_equivalence_body() -> dict:
    from recipe_engine.founder_runtime.ids import digest
    return {"services": [{"port": 8848, "status": 200, "body_sha256": digest("ok"), "pid": 10,
                          "command": "gov", "cwd": "/x", "listener": "127.0.0.1:8848"}],
            "mutation_path": "MUTATION_PATH_NOT_RATIFIED", "cockpit_sha": "a" * 40,
            "cycle_id": "c1", "timestamp": "2026-07-21T00:00:00Z", "seal": "sealed"}
