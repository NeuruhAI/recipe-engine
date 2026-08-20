"""Auth tests: fail closed, no token in source/logs, loopback only."""

from __future__ import annotations

import pytest

from recipe_engine.founder_runtime.api.auth import OperatorAuth, generate_token
from recipe_engine.founder_runtime.api.client import ApiClientError, FounderRuntimeClient


def test_no_token_configured_denies_everything():
    auth = OperatorAuth("")  # empty -> fail closed
    assert not auth.configured
    assert not auth.verify("anything")


def test_wrong_token_denied():
    auth = OperatorAuth(generate_token())
    assert not auth.verify(generate_token())
    assert not auth.verify(None)
    assert not auth.verify("")


def test_correct_token_allowed():
    tok = generate_token()
    auth = OperatorAuth(tok)
    assert auth.verify(tok)


def test_http_requires_bearer(running_server, token):
    client, port = running_server
    # valid token works
    assert client.list_missions()["count"] >= 0
    # wrong token -> 401
    bad = FounderRuntimeClient("frk_wrong", port=port)
    with pytest.raises(ApiClientError) as ei:
        bad.list_missions()
    assert ei.value.status == 401


def test_health_and_readiness_do_not_require_auth(running_server):
    client, _ = running_server
    assert client.health()["status"] == "ok"
    assert client.readiness()["persistence_integrity"] is True


def test_client_rejects_non_loopback():
    with pytest.raises(ValueError):
        FounderRuntimeClient("tok", host="example.com")


def test_state_change_requires_idempotency_key(running_server):
    import json
    import urllib.request
    client, port = running_server
    res = client.create_mission(objective="x", idempotency_key="k")
    mid = res["envelope"]["mission_id"]
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/missions/{mid}/pause",
        data=b"{}", method="POST")
    req.add_header("Authorization", f"Bearer {client._token}")
    req.add_header("Content-Type", "application/json")
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False, "expected 428"
    except urllib.error.HTTPError as e:
        assert e.code == 428
