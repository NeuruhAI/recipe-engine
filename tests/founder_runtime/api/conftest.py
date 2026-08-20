"""Shared fixtures for Founder Runtime API tests. Loopback only."""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time

import pytest

from recipe_engine.founder_runtime.api.auth import generate_token
from recipe_engine.founder_runtime.api.client import FounderRuntimeClient
from recipe_engine.founder_runtime.api.service import MissionService

# create_app is imported inside the running_server fixture, not here. It reaches
# the FastAPI transport, and the authority surface under test — MissionService,
# its approval latch, idempotency and version binding — is pure Python. Importing
# it at module scope made a missing web framework an error for the entire API
# suite, including the tests that never open a socket.


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def token() -> str:
    return generate_token()


@pytest.fixture()
def service(tmp_path):
    db = str(tmp_path / "missions.sqlite")
    svc = MissionService(db)
    yield svc
    svc.close()


@pytest.fixture()
def running_server(tmp_path, token):
    """Start a real loopback uvicorn server; yield (client, port)."""
    pytest.importorskip("fastapi", reason="HTTP transport requires fastapi")
    uvicorn = pytest.importorskip("uvicorn", reason="HTTP transport requires uvicorn")
    from recipe_engine.founder_runtime.api.app import create_app
    port = _free_port()
    db = str(tmp_path / "missions.sqlite")
    os.environ["FR_API_OPERATOR_TOKEN"] = token
    app = create_app(db, probe_live=False)
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    client = FounderRuntimeClient(token, port=port)
    for _ in range(100):
        try:
            client.health()
            break
        except Exception:
            time.sleep(0.05)
    yield client, port
    server.should_exit = True
    time.sleep(0.1)
