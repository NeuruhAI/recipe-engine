"""The mission AUTHORITY gate, reachable from outside the mission process.

T31B proved the authority chain with the approval channel calling
``MissionService.approve_mission_gate`` directly in a separate OS process. The
HTTP surface — the channel a human actually reaches — routed only the PLAN
decision, and the kernel refuses a plan decision at the gate:
``assert_approval_usable`` compares ``exact_requested_action`` against the
transition being consumed, and ``plan_decision:allow`` is not that transition.
A founder could therefore call the API's approve endpoint, get a 200, and watch
the mission stay parked forever.

These tests hold the two routes that close that gap to the property that
matters: an ALLOW arriving over HTTP produces the signed artifact a parked live
mission can actually spend, bound to what that mission published, and a DENY
produces none.
"""

from __future__ import annotations

import json

import pytest

from recipe_engine.founder_runtime.api.founder_signing import ENV_FOUNDER_SECRET
from recipe_engine.founder_runtime.missions.long_horizon import (
    GOV_APPROVAL_REF_PREFIX,
    authorization_scope_key,
    run_long_horizon_mission,
)
from recipe_engine.founder_runtime.runtime.approvals import gated_action_for
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.state.states import MissionState

GATE_ACTION, GATE_SCOPE = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
SOURCE_SHA = "20a184f36088be934ded5f8a93039d7aeabad86f"
SECRET = "t39-gate-route-secret-value-0123456789"  # gitleaks:allow — inert test vector input


class _Result:
    def __init__(self, status, payload=None):
        self.status = status
        self.payload = payload or {}
        self.error_code = None
        self.evidence_refs = []


class _Governance:
    """Stands in for the live organ; these tests are about the HTTP gate, not Governance."""

    def decide(self, action, *, trusted_authority_class, trusted_policy_version, meta):
        return _Result("success", {"audit_id": "audit_ok", "decision": "allow"})

    def authorize(self, facts, *, founder_approval, meta):
        return _Result("success", {"authorization_id": "auth_ok", "decision": "allow",
                                   "reason_code": "FOUNDER_APPROVED_SCOPE_ONCE", "risk_tier": "R0"})


def _organs():
    return {"mother": object(), "github": object(), "vault": object(),
            "qdrant": object(), "governance": _Governance()}


def _park_live_mission(db_path: str, key: str) -> str:
    """Run the MISSION role until it parks and publishes what it will ask for.

    The artifact is bound to the published request, so a mission that never
    published one cannot be gate-approved into authority — which is the reason
    this helper exists instead of a plain create_mission.
    """
    rt = MissionRuntime(db_path=db_path, adapter_mode="fixture")
    res = run_long_horizon_mission(rt, mode="live", organs=_organs(), executor=object(),
                                   creation_idempotency_key=key, max_stages=0,
                                   source_sha=SOURCE_SHA)
    assert res["status"] == "awaiting_approval", res.get("status")
    return res["mission_id"]


def _signed(refs):
    return [r for r in refs if isinstance(r, str) and r.startswith(GOV_APPROVAL_REF_PREFIX)]


def _version(client, mission_id):
    return int(client.get_mission(mission_id)["envelope"]["version"])


# ------------------------------------------------------------------ routing --
def test_gate_routes_are_reachable_over_http(server_factory, tmp_path):
    db = str(tmp_path / "routing.sqlite")
    mid = _park_live_mission(db, "gate-routing")
    client, _ = server_factory("routing")
    out = client.approve_gate(mid, reason="read-only observation approved",
                              expected_version=_version(client, mid))
    assert out["authority_state"] == "allow"
    # Authority and execution are separate acts: this surface never executes.
    assert out["executed"] is False


def test_gate_allow_binds_the_transition_the_kernel_consumes(server_factory, tmp_path):
    """The whole point: a plan decision could never satisfy the gate."""
    db = str(tmp_path / "binding.sqlite")
    mid = _park_live_mission(db, "gate-binding")
    client, _ = server_factory("binding")
    out = client.approve_gate(mid, reason="ok", expected_version=_version(client, mid))
    assert out["approval"]["exact_requested_action"] == GATE_ACTION
    assert out["approval"]["scope"] == GATE_SCOPE

    mid2 = _park_live_mission(db, "gate-binding-plan")
    plan = client.approve(mid2, reason="ok", expected_version=_version(client, mid2))
    assert plan["approval"]["exact_requested_action"] == "plan_decision:allow"
    assert plan["approval"]["exact_requested_action"] != GATE_ACTION


# ------------------------------------------------------------------ signing --
def test_gate_allow_over_http_mints_the_artifact_bound_to_what_was_published(
        server_factory, tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_FOUNDER_SECRET, SECRET)
    db = str(tmp_path / "signing.sqlite")
    mid = _park_live_mission(db, "gate-signing")
    client, _ = server_factory("signing")

    out = client.approve_gate(mid, reason="ok", expected_version=_version(client, mid))
    refs = _signed(out["approval"]["evidence_refs"])
    assert len(refs) == 1, out["approval"]["evidence_refs"]
    artifact = json.loads(refs[0][len(GOV_APPROVAL_REF_PREFIX):])

    assert artifact["mode"] == "scope_once"
    assert artifact["approver_id"] == "founder"
    assert artifact["signature"]
    # Bound to the MISSION's published coordinates, not to anything the channel chose.
    assert artifact["bound"]["source_sha"] == SOURCE_SHA
    assert artifact["bound"]["scope_key"] == authorization_scope_key(
        mid, action=GATE_ACTION, scope=GATE_SCOPE, source_sha=SOURCE_SHA)


def test_gate_deny_over_http_signs_nothing(server_factory, tmp_path, monkeypatch):
    """There is no such thing as a signed refusal to spend."""
    monkeypatch.setenv(ENV_FOUNDER_SECRET, SECRET)
    db = str(tmp_path / "deny.sqlite")
    mid = _park_live_mission(db, "gate-deny")
    client, _ = server_factory("deny")
    out = client.reject_gate(mid, reason="not now", expected_version=_version(client, mid))
    assert out["authority_state"] == "deny"
    assert _signed(out["approval"]["evidence_refs"]) == []


def test_plan_route_never_mints_an_artifact(server_factory, tmp_path, monkeypatch):
    """A plan approval must not become a spendable authority artifact."""
    monkeypatch.setenv(ENV_FOUNDER_SECRET, SECRET)
    db = str(tmp_path / "planonly.sqlite")
    mid = _park_live_mission(db, "gate-planonly")
    client, _ = server_factory("planonly")
    out = client.approve(mid, reason="ok", expected_version=_version(client, mid))
    assert _signed(out["approval"]["evidence_refs"]) == []


def test_gate_without_a_signing_key_records_but_cannot_mint(server_factory, tmp_path, monkeypatch):
    """Fail closed: no key means no artifact, so the live mission simply parks."""
    monkeypatch.delenv(ENV_FOUNDER_SECRET, raising=False)
    monkeypatch.delenv("GOVERNANCE_FOUNDER_SECRET_FILE", raising=False)
    db = str(tmp_path / "unsigned.sqlite")
    mid = _park_live_mission(db, "gate-unsigned")
    client, _ = server_factory("unsigned")
    out = client.approve_gate(mid, reason="ok", expected_version=_version(client, mid))
    assert out["authority_state"] == "allow"
    assert _signed(out["approval"]["evidence_refs"]) == []


# ----------------------------------------------------------------- contract --
def test_gate_reject_requires_a_reason(server_factory, tmp_path):
    from recipe_engine.founder_runtime.api.client import ApiClientError
    db = str(tmp_path / "reason.sqlite")
    mid = _park_live_mission(db, "gate-reason")
    client, _ = server_factory("reason")
    with pytest.raises(ApiClientError) as e:
        client.reject_gate(mid, reason="   ", expected_version=_version(client, mid))
    assert e.value.status == 422


def test_gate_routes_require_auth_idempotency_and_version(server_factory, tmp_path):
    import urllib.error
    import urllib.request
    db = str(tmp_path / "headers.sqlite")
    mid = _park_live_mission(db, "gate-headers")
    client, port = server_factory("headers")
    url = f"http://127.0.0.1:{port}/v1/missions/{mid}/gate/approve"

    def post(headers):
        req = urllib.request.Request(url, data=b'{"reason":"x"}', method="POST",
                                     headers={"content-type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as err:
            return err.code

    auth = {"Authorization": f"Bearer {client._token}"}
    assert post({}) == 401
    assert post({"Authorization": "Bearer wrong"}) == 401
    assert post(auth) == 428                                       # no Idempotency-Key
    assert post({**auth, "Idempotency-Key": "k1"}) == 428           # no X-Expected-Version
    assert post({**auth, "Idempotency-Key": "k2", "X-Expected-Version": "99999"}) == 409


def test_gate_decision_is_a_single_terminal_latch(server_factory, tmp_path):
    from recipe_engine.founder_runtime.api.client import ApiClientError
    db = str(tmp_path / "latch.sqlite")
    mid = _park_live_mission(db, "gate-latch")
    client, _ = server_factory("latch")
    ver = _version(client, mid)
    client.approve_gate(mid, reason="ok", idempotency_key="latch-1", expected_version=ver)
    replay = client.approve_gate(mid, reason="ok", idempotency_key="latch-1", expected_version=ver)
    assert replay["idempotent_replay"] is True
    with pytest.raises(ApiClientError) as e:
        client.reject_gate(mid, reason="changed my mind", idempotency_key="latch-2",
                           expected_version=_version(client, mid))
    assert e.value.status == 409
    assert e.value.payload["error"]["code"] == "DECISION_CONFLICT"


def test_openapi_publishes_the_gate_routes():
    from recipe_engine.founder_runtime.api.openapi import generate
    paths = generate()["paths"]
    assert "/v1/missions/{mission_id}/gate/approve" in paths
    assert "/v1/missions/{mission_id}/gate/reject" in paths
