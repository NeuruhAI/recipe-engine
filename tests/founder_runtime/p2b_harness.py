"""Deterministic loopback test harness for the P2-B live adapters.

Real live I/O — a threaded HTTP server bound to 127.0.0.1 and real temp git
repos — so the adapters exercise their genuine transports with zero external
dependency.  (Test-support module: imported by tests, never by the package under
the security scan.)
"""

from __future__ import annotations

import hashlib
import json
import os
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


ROOT_LEGACY = "host-colima-integrity-recovery"
ROOT_FINAL = "host-colima-integrity-recovery-final"

_FINAL_FILES = {"qdrant": "QDRANT_VERIFICATION.md", "redis": "REDIS_VERIFICATION.md", "volume": "VOLUME_PRESERVATION.csv"}
_LEGACY_FILES = {"qdrant": "QDRANT_INTEGRITY.md", "redis": "REDIS_INTEGRITY.md", "volume": "DOCKER_VOLUME_LEDGER.csv"}


def _qdrant_md(count_before: int, count_after: int) -> str:
    return (
        "# QDRANT VERIFICATION\n\n"
        "| check | before | after |\n|---|---|---|\n"
        f"| collection `neuruh_vault_canon` | exists | exists |\n"
        f"| exact point count | {count_before} | {count_after} |\n"
        f"| count after scroll | {count_after} | {count_after} |\n"
        "| vector size / distance | 3072 / Cosine | 3072 / Cosine |\n"
        f"points_count: {count_before}. No points written.\n"
    )


def _redis_md(pong: bool) -> str:
    if pong:
        return "# REDIS VERIFICATION\n- PING -> **PONG**; DBSIZE 0; healthy. No write command issued.\n"
    return "# REDIS VERIFICATION\n- PING -> (no reply); server DOWN; healthcheck failing.\n"


def _volume_csv(kind: str, n: int, recreated_row: bool = False) -> str:
    names = [
        "neuruh-qdrant-data", "landgraph_redis_data", "landgraph_neo4j_data",
        "landgraph_neo4j_logs", "9a7e6dca8bf44c8a6c24686d20aa4cd9f63ce93c",
    ]
    rows = names[:n]
    if kind == "legacy":
        out = ["volume,driver,consumed_by,mount_dest,state,preserved,silently_recreated"]
        for i, v in enumerate(rows):
            recr = "yes" if (recreated_row and i == 0) else "no (no restart performed)"
            out.append(f"{v},local,svc,/data,running,yes,{recr}")
    else:
        out = ["named_volume,present_before,present_after,mountpoint,identity_unchanged,recreated"]
        for i, v in enumerate(rows):
            recr = "yes" if (recreated_row and i == 0) else "no"
            out.append(f"{v},yes,yes,/var/lib/docker/volumes/{v}/_data,yes,{recr}")
    return "\n".join(out) + "\n"


def _io_md(clear: bool) -> str:
    if clear:
        return (
            "# I/O ERROR ASSESSMENT — before vs after\n\n"
            "| item | before recovery | after recovery |\n|---|---|---|\n"
            "| new dmesg I/O errors (this boot) | JBD2 aborted journal, EXT4-fs errors | **0** |\n"
            "| ext4 vdb1 superblock | needs_recovery | clean |\n\n"
            "superblock clean, needs_recovery cleared.\n"
            "**No continuing virtio / JBD2 / ext4 / overlayfs / containerd I/O errors.**\n"
        )
    return (
        "# I/O ERROR ASSESSMENT\n\n"
        "continuing I/O errors: **YES** — ongoing ext4 EIO on vdb1; JBD2 journal still aborting.\n"
    )


def _verdict_md(verdict: str) -> str:
    return f"# FINAL VERDICT — Host recovery\n\n## VERDICT: {verdict}\n\nSealed test audit.\n"


def write_sealed_audit(
    base: Path,
    *,
    root_name: str = ROOT_FINAL,
    ts: str = "20260722T000000Z",
    verdict: str = "GREEN",
    aliases: str = "final",           # "final" | "legacy" | "mixed"
    count_before: int = 3049,
    count_after: int = 3049,
    redis_pong: bool = True,
    volumes: int = 5,
    io_clear: bool = True,
    recreated_volume: bool = False,
    corrupt_checksum: bool = False,
    omit_qdrant: bool = False,
    symlink_evidence: bool = False,
    io_filename: str | None = None,   # override the I/O evidence filename
    write_manifest: bool = True,
    manifest_extra_line: str | None = None,
) -> Path:
    """Build a fully-sealed host-recovery audit under an allowlisted root name.

    Returns the audit directory path.  ``base`` is the gate's audit *base*; the
    audit lands at ``base/<root_name>/<ts>/``.
    """
    audit = base / root_name / ts
    audit.mkdir(parents=True, exist_ok=True)
    if aliases == "legacy":
        fmap, vkind = _LEGACY_FILES, "legacy"
    elif aliases == "mixed":
        fmap = {"qdrant": "QDRANT_INTEGRITY.md", "redis": "REDIS_VERIFICATION.md", "volume": "VOLUME_LEDGER.csv"}
        vkind = "final"
    else:
        fmap, vkind = _FINAL_FILES, "final"

    files: dict[str, str] = {"FINAL_VERDICT.md": _verdict_md(verdict)}
    if not omit_qdrant:
        files[fmap["qdrant"]] = _qdrant_md(count_before, count_after)
    files[fmap["redis"]] = _redis_md(redis_pong)
    files[fmap["volume"]] = _volume_csv(vkind, volumes, recreated_row=recreated_volume)
    io_name = io_filename or ("IO_ERROR_CLEARANCE.md" if aliases == "final" else "IO_ERROR_ASSESSMENT.md")
    files[io_name] = _io_md(io_clear)
    files["SHUTDOWN_STARTUP_LEDGER.md"] = "Graceful quiesce; docker start; all volumes preserved.\n"

    for name, content in files.items():
        (audit / name).write_text(content)

    if symlink_evidence:
        # replace the qdrant evidence with a symlink to an out-of-audit file
        outside = base / "outside_evidence.md"
        outside.write_text(_qdrant_md(count_before, count_after))
        target = audit / fmap["qdrant"]
        if target.exists():
            target.unlink()
        os.symlink(outside, target)

    if write_manifest:
        manifest_lines = []
        for name in sorted(files):
            if symlink_evidence and name == fmap["qdrant"]:
                continue  # symlinked file intentionally not in the sealed manifest
            digest = hashlib.sha256((audit / name).read_bytes()).hexdigest()
            manifest_lines.append(f"{digest}  {name}")
        if manifest_extra_line:
            manifest_lines.append(manifest_extra_line)
        (audit / "DELIVERABLES.sha256").write_text("\n".join(manifest_lines) + "\n")
        if corrupt_checksum:
            # tamper a file AFTER sealing so its recorded checksum no longer matches
            (audit / "SHUTDOWN_STARTUP_LEDGER.md").write_text("TAMPERED after sealing.\n")
    return audit


def make_green_host_audit(base: Path, *, root_name: str = ROOT_FINAL, ts: str = "20260722T000000Z") -> Path:
    """Backward-compatible helper: a sealed GREEN audit under ``base``."""
    return write_sealed_audit(base, root_name=root_name, ts=ts, verdict="GREEN")


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
