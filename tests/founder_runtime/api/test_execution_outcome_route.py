"""R02D — POST /v1/missions/{id}/execution-outcomes over the real loopback transport.

The workers dispatch bridge is the intended caller (H03 step 2, a separate
writer). These tests pin the HTTP contract it will program against.
"""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest

from recipe_engine.founder_runtime.api.auth import OperatorAuth, generate_token
from recipe_engine.founder_runtime.api.client import ApiClientError, FounderRuntimeClient
from recipe_engine.founder_runtime.api.openapi import generate
from recipe_engine.founder_runtime.api.service import MissionService
from tests.founder_runtime.api.test_approval_resolver import SHA, _Governance
from tests.founder_runtime.execution_outcome_fixtures import (
    court_envelope,
    outcome_envelope,
    work_spec,
)

ROUTE = "/v1/missions/{mission_id}/execution-outcomes"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def queued_server(tmp_path):
    """A loopback server whose fixture Governance resolves missions to QUEUED."""
    pytest.importorskip("fastapi")
    uvicorn = pytest.importorskip("uvicorn")
    from recipe_engine.founder_runtime.api.server import build_app
    token = generate_token()
    os.environ["FR_API_OPERATOR_TOKEN"] = token
    service = MissionService(str(tmp_path / "queued.sqlite"), governance=_Governance(),
                             attested_source_sha=SHA)
    app = build_app(service, OperatorAuth(), probe_live=False)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    client = FounderRuntimeClient(token, port=port)
    for _ in range(100):
        try:
            client.health()
            break
        except Exception:
            time.sleep(0.05)
    yield client, service, token, port
    server.should_exit = True
    time.sleep(0.1)
    service.close()


def _queued(client: FounderRuntimeClient, key: str = "http-create") -> dict:
    env = client.create_mission(objective="Read-only research plan.", work_spec=work_spec(),
                                idempotency_key=key)["envelope"]
    assert env["state"] == "QUEUED"
    return env


def test_openapi_exposes_the_write_back_route():
    spec = generate()
    assert ROUTE in spec["paths"]
    assert "post" in spec["paths"][ROUTE]


def test_route_requires_operator_token_and_idempotency_key(queued_server):
    client, _service, token, port = queued_server
    env = _queued(client)
    body = outcome_envelope(env, "DISPATCHED")
    anon = FounderRuntimeClient("not-the-token", port=port)
    with pytest.raises(ApiClientError) as excinfo:
        anon._req("POST", ROUTE.format(mission_id=env["mission_id"]), body=body, idempotency_key="k")
    assert excinfo.value.status == 401
    with pytest.raises(ApiClientError) as excinfo:
        client._req("POST", ROUTE.format(mission_id=env["mission_id"]), body=body)
    assert excinfo.value.status == 428
    assert client.get_mission(env["mission_id"])["envelope"]["state"] == "QUEUED"


def test_client_records_succeeded_then_court_and_receipts_route_shows_the_receipt(queued_server):
    client, _service, _token, _port = queued_server
    env = _queued(client)
    mid = env["mission_id"]
    ok = outcome_envelope(env, "SUCCEEDED")
    res = client.record_execution_outcome(mid, ok, idempotency_key="wb-1")
    assert res["applied"] is True and res["state_after"] == "VERIFYING"
    assert res["execution"]["court"] == "PENDING" and res["execution"]["verified"] is False
    assert client.get_mission(mid)["envelope"]["state"] == "VERIFYING"

    receipts = client.receipts(mid)
    assert receipts["execution"]["receipt_sha"] == ok["receipt"]["receipt_sha"]
    sealed = [o for o in receipts["execution_outcomes"] if o["outcome"] == "SUCCEEDED"]
    assert len(sealed) == 1
    assert sealed[0]["execution_outcome"]["receipt"]["fields"] == ok["receipt"]["fields"]
    # ExecutionReceipt (fr.receipt.v1) evidence is a different thing and stays empty
    assert receipts["receipts"] == []

    court = client.record_execution_outcome(mid, court_envelope(env, "COURT_PASSED", ok), idempotency_key="wb-2")
    assert court["state_after"] == "RECONCILING"
    assert client.get_mission(mid)["envelope"]["state"] == "RECONCILING"
    # no longer in the dispatchable list view
    listed = next(m for m in client.list_missions()["missions"] if m["mission_id"] == mid)
    assert listed["state"] == "RECONCILING"
    assert listed["authority"]["work_spec_execution_authorized"] is False


def test_http_idempotency_replay_and_conflict(queued_server):
    client, _service, _token, _port = queued_server
    env = _queued(client)
    mid = env["mission_id"]
    ok = outcome_envelope(env, "SUCCEEDED")
    first = client.record_execution_outcome(mid, ok, idempotency_key="wb-same")
    version_after = client.get_mission(mid)["envelope"]["version"]
    replay = client.record_execution_outcome(mid, ok, idempotency_key="wb-same")
    assert replay["idempotent_replay"] is True and replay["applied"] is False
    fresh = client.record_execution_outcome(mid, ok, idempotency_key="wb-fresh")
    assert fresh["idempotent_replay"] is True and fresh["applied"] is False
    assert client.get_mission(mid)["envelope"]["version"] == version_after
    assert first["events_appended"] and not fresh["events_appended"]
    with pytest.raises(ApiClientError) as excinfo:
        client.record_execution_outcome(mid, outcome_envelope(env, "FAILED"), idempotency_key="wb-same")
    assert excinfo.value.status == 409
    assert excinfo.value.payload["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_http_rejections_carry_precise_codes_and_change_nothing(queued_server):
    client, _service, _token, _port = queued_server
    env = _queued(client)
    mid = env["mission_id"]
    before = client.get_mission(mid)
    cases = [
        (outcome_envelope(env, "COURT_PASSED"), 409, "OUTCOME_OUT_OF_ORDER"),
        (outcome_envelope(env, "DISPATCHED", work_spec_sha256="a" * 64), 409, "WORK_SPEC_MISMATCH"),
        (outcome_envelope(env, "DISPATCHED", mission_version=env["version"] + 5), 409, "MISSION_VERSION_MISMATCH"),
        (outcome_envelope(env, "DISPATCHED") | {"unexpected": True}, 400, "EXECUTION_OUTCOME_MALFORMED"),
    ]
    for body, status, code in cases:
        with pytest.raises(ApiClientError) as excinfo:
            client.record_execution_outcome(mid, body, idempotency_key=f"rej-{code}")
        assert excinfo.value.status == status, (code, excinfo.value.payload)
        assert excinfo.value.payload["error"]["code"] == code
    after = client.get_mission(mid)
    assert after["envelope"]["state"] == "QUEUED"
    assert after["envelope"]["version"] == before["envelope"]["version"]
    assert after["mission"]["extensions"].get("execution") is None


def test_wrong_mission_in_path_cannot_mutate_the_other_mission(queued_server):
    client, _service, _token, _port = queued_server
    a = _queued(client, key="a")
    b = _queued(client, key="b")
    with pytest.raises(ApiClientError) as excinfo:
        client.record_execution_outcome(b["mission_id"], outcome_envelope(a, "SUCCEEDED"), idempotency_key="cross")
    assert excinfo.value.status == 409
    assert excinfo.value.payload["error"]["code"] == "MISSION_MISMATCH"
    assert client.get_mission(a["mission_id"])["envelope"]["state"] == "QUEUED"
    assert client.get_mission(b["mission_id"])["envelope"]["state"] == "QUEUED"
