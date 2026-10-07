"""G-1: only the founder credential can record an ALLOW, or make FR sign one.

Before this, both approve routes accepted the shared operator token (the same
credential the scheduler and agents hold) and ``_record_decision`` wrote
``decided_by="jeramie"`` as a constant, so any operator-token caller recorded a
founder decision, and on a gate ALLOW FR signed it into the artifact Governance
verifies. These tests hold the fix to its properties:

* the operator token is refused on both approve routes with a named 403;
* the founder credential is accepted and ``decided_by`` is its identity;
* a wrong or missing credential is refused;
* FR signs only a founder-authenticated gate ALLOW;
* late binding (an earlier unsigned ALLOW signed once a request is published)
  cannot be triggered by an operator;
* FR holds only a sha256 of the founder token and refuses a raw one.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from recipe_engine.founder_runtime.api.auth import (
    ENV_FOUNDER_IDENTITY, ENV_FOUNDER_TOKEN_SHA256, FORBIDDEN_RAW_FOUNDER_TOKEN_ENV,
    FounderAuth, FounderAuthConfigError, OperatorAuth, founder_token_sha256,
)
from recipe_engine.founder_runtime.api.founder_signing import ENV_FOUNDER_SECRET, FounderApprovalSigner
from recipe_engine.founder_runtime.api.identity import OPERATOR_IDENTITY, Decider
from recipe_engine.founder_runtime.api.service import (
    GOV_ARTIFACT_LATE_BOUND_PREFIX, ApiError, MissionService,
)
from recipe_engine.founder_runtime.missions.long_horizon import GOV_APPROVAL_REF_PREFIX, run_long_horizon_mission
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from tests.founder_runtime.deciders import FOUNDER, OPERATOR

SOURCE_SHA = "20a184f36088be934ded5f8a93039d7aeabad86f"
SECRET = "g1-founder-signing-test-secret-0123456789"  # gitleaks:allow — inert test vector input
API = Path(__file__).resolve().parents[3] / "recipe_engine" / "founder_runtime" / "api"


class _Result:
    def __init__(self, status, payload=None):
        self.status = status
        self.payload = payload or {}
        self.error_code = None
        self.evidence_refs = []


class _Governance:
    def decide(self, action, *, trusted_authority_class, trusted_policy_version, meta):
        return _Result("success", {"audit_id": "audit_ok", "decision": "allow"})

    def authorize(self, facts, *, founder_approval, meta):
        return _Result("success", {"authorization_id": "auth_ok", "decision": "allow",
                                   "reason_code": "FOUNDER_APPROVED_SCOPE_ONCE", "risk_tier": "R0"})


def _park(db_path: str, key: str) -> str:
    """A live-mode mission parked at the gate, with its authorization request published."""
    rt = MissionRuntime(db_path=db_path, adapter_mode="fixture")
    organs = {"mother": object(), "github": object(), "vault": object(),
              "qdrant": object(), "governance": _Governance()}
    res = run_long_horizon_mission(rt, mode="live", organs=organs, executor=object(),
                                   creation_idempotency_key=key, max_stages=0, source_sha=SOURCE_SHA)
    assert res["status"] == "awaiting_approval", res.get("status")
    return res["mission_id"]


class SpySigner(FounderApprovalSigner):
    def __init__(self):
        super().__init__(SECRET)
        self.calls = 0

    def sign_scope_once(self, **kw):
        self.calls += 1
        return super().sign_scope_once(**kw)


def _signed(refs):
    return [r for r in refs if isinstance(r, str) and r.startswith(GOV_APPROVAL_REF_PREFIX)]


def _version(svc: MissionService, mid: str) -> int:
    return int(svc.get_mission(mid)["envelope"]["version"])


def _post(port: int, path: str, bearer: str | None, *, key: str = "k", version: int | None = 1,
          body: dict | None = None) -> tuple[int, dict]:
    headers = {"content-type": "application/json", "Idempotency-Key": key}
    if version is not None:
        headers["X-Expected-Version"] = str(version)
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST",
                                 data=json.dumps(body or {"reason": "g1"}).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _code(payload: dict) -> str | None:
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return (detail or {}).get("code") if isinstance(detail, dict) else None


def _approvals(db: str, mid: str):
    rt = MissionRuntime(db_path=db, adapter_mode="fixture")
    try:
        return rt.store.list_approvals(mid)
    finally:
        rt.close() if hasattr(rt, "close") else None


# ------------------------------------------------------------------- routes --
@pytest.mark.parametrize("route", ["/approve", "/gate/approve"])
def test_operator_token_is_refused_on_both_approve_routes(server_factory, tmp_path, route):
    client, port = server_factory("op-refused")
    db = str(tmp_path / "op-refused.sqlite")
    mid = _park(db, "op-refused" + route.replace("/", "-"))
    status, payload = _post(port, f"/v1/missions/{mid}{route}", client._token,
                            version=int(client.get_mission(mid)["envelope"]["version"]))
    assert status == 403
    assert _code(payload) == "OPERATOR_TOKEN_NOT_FOUNDER"
    assert _approvals(db, mid) == [], "a refused call must record nothing"


def test_founder_token_records_the_gate_allow_with_its_own_identity(server_factory, tmp_path, founder_token):
    client, port = server_factory("founder-gate")
    mid = _park(str(tmp_path / "founder-gate.sqlite"), "founder-gate")
    out = client.approve_gate(mid, founder_token=founder_token, reason="ok",
                              expected_version=int(client.get_mission(mid)["envelope"]["version"]))
    assert out["authority_state"] == "allow"
    assert out["approval"]["decided_by"] == "jeramie"  # the configured FR_FOUNDER_IDENTITY


def test_founder_token_records_the_plan_allow_with_its_own_identity(server_factory, tmp_path, founder_token):
    client, _ = server_factory("founder-plan")
    mid = _park(str(tmp_path / "founder-plan.sqlite"), "founder-plan")
    out = client.approve(mid, founder_token=founder_token, reason="ok",
                         expected_version=int(client.get_mission(mid)["envelope"]["version"]))
    assert out["approval"]["decided_by"] == "jeramie"
    assert out["approval"]["exact_requested_action"] == "plan_decision:allow"


def test_decided_by_follows_the_configured_identity_not_a_constant(server_factory, tmp_path, founder_token):
    """decided_by is whatever identity the founder credential is configured as."""
    client, _ = server_factory("identity2", founder_identity="founder-two")
    mid = _park(str(tmp_path / "identity2.sqlite"), "identity2")
    out = client.approve_gate(mid, founder_token=founder_token, reason="ok",
                              expected_version=int(client.get_mission(mid)["envelope"]["version"]))
    assert out["approval"]["decided_by"] == "founder-two"


@pytest.mark.parametrize("route", ["/approve", "/gate/approve"])
@pytest.mark.parametrize("bearer,code", [(None, "FOUNDER_TOKEN_MISSING"),
                                         ("frf_not-the-founder-token", "FOUNDER_TOKEN_INVALID")])
def test_wrong_or_missing_token_is_refused(server_factory, tmp_path, route, bearer, code):
    client, port = server_factory("wrong")
    db = str(tmp_path / "wrong.sqlite")
    mid = _park(db, "wrong" + route.replace("/", "-") + code)
    status, payload = _post(port, f"/v1/missions/{mid}{route}", bearer,
                            version=int(client.get_mission(mid)["envelope"]["version"]))
    assert status == 403
    assert _code(payload) == code
    assert _approvals(db, mid) == []


def test_unconfigured_founder_credential_refuses_everyone(server_factory, tmp_path, founder_token):
    client, port = server_factory("unconfigured", founder_configured=False)
    mid = _park(str(tmp_path / "unconfigured.sqlite"), "unconfigured")
    ver = int(client.get_mission(mid)["envelope"]["version"])
    status, payload = _post(port, f"/v1/missions/{mid}/gate/approve", founder_token, version=ver)
    assert (status, _code(payload)) == (403, "FOUNDER_AUTH_NOT_CONFIGURED")
    status, payload = _post(port, f"/v1/missions/{mid}/gate/approve", client._token, key="k2", version=ver)
    assert (status, _code(payload)) == (403, "OPERATOR_TOKEN_NOT_FOUNDER")


def test_operator_may_reject_but_is_recorded_as_the_operator(server_factory, tmp_path):
    client, _ = server_factory("op-reject")
    mid = _park(str(tmp_path / "op-reject.sqlite"), "op-reject")
    out = client.reject_gate(mid, reason="not now",
                             expected_version=int(client.get_mission(mid)["envelope"]["version"]))
    assert out["authority_state"] == "deny"
    assert out["approval"]["decided_by"] == OPERATOR_IDENTITY
    assert out["approval"]["decided_by"] != "jeramie"


def test_founder_can_read_a_mission_with_the_founder_credential(server_factory, tmp_path, founder_token):
    """neuruh-approve holds one credential; it reads the version with it."""
    _, port = server_factory("founder-read")
    mid = _park(str(tmp_path / "founder-read.sqlite"), "founder-read")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/missions/{mid}",
                                 headers={"Authorization": f"Bearer {founder_token}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert json.loads(r.read())["envelope"]["mission_id"] == mid


def test_operator_gate_approve_over_http_never_signs(server_factory, tmp_path, monkeypatch, founder_token):
    monkeypatch.setenv(ENV_FOUNDER_SECRET, SECRET)
    client, port = server_factory("op-nosign")
    db = str(tmp_path / "op-nosign.sqlite")
    mid = _park(db, "op-nosign")
    ver = int(client.get_mission(mid)["envelope"]["version"])
    status, _ = _post(port, f"/v1/missions/{mid}/gate/approve", client._token, version=ver)
    assert status == 403
    assert _approvals(db, mid) == []
    out = client.approve_gate(mid, founder_token=founder_token, reason="ok", expected_version=ver)
    assert len(_signed(out["approval"]["evidence_refs"])) == 1  # only the founder's ALLOW is signed


# ------------------------------------------------------------------ service --
def test_service_refuses_an_allow_from_a_non_founder_and_never_signs(tmp_path):
    db = str(tmp_path / "svc.sqlite")
    mid = _park(db, "svc-refuse")
    spy = SpySigner()
    svc = MissionService(db, signer=spy)
    try:
        for call in (svc.approve_mission_gate, svc.approve_plan):
            with pytest.raises(ApiError) as e:
                call(mid, reason="ok", idempotency_key="op-" + call.__name__,
                     expected_version=_version(svc, mid), decider=OPERATOR)
            assert e.value.status == 403 and e.value.code == "FOUNDER_REQUIRED"
        assert spy.calls == 0
        assert svc.runtime.store.list_approvals(mid) == []
    finally:
        svc.close()


def test_service_signs_only_a_founder_gate_allow(tmp_path):
    db = str(tmp_path / "svc-sign.sqlite")
    spy = SpySigner()
    svc = MissionService(db, signer=spy)
    try:
        deny_mid = _park(db, "svc-deny")
        out = svc.reject_mission_gate(deny_mid, reason="no", idempotency_key="deny",
                                      expected_version=_version(svc, deny_mid), decider=FOUNDER)
        assert _signed(out["approval"]["evidence_refs"]) == [] and spy.calls == 0
        plan_mid = _park(db, "svc-plan")
        svc.approve_plan(plan_mid, reason="ok", idempotency_key="plan",
                         expected_version=_version(svc, plan_mid), decider=FOUNDER)
        assert spy.calls == 0  # a plan approval is never signed
        gate_mid = _park(db, "svc-gate")
        out = svc.approve_mission_gate(gate_mid, reason="ok", idempotency_key="gate",
                                       expected_version=_version(svc, gate_mid), decider=FOUNDER)
        assert spy.calls == 1 and len(_signed(out["approval"]["evidence_refs"])) == 1
        assert out["approval"]["decided_by"] == FOUNDER.identity
    finally:
        svc.close()


def test_late_binding_cannot_be_triggered_by_an_operator(tmp_path, monkeypatch):
    """An earlier artifact-less founder ALLOW is signed later only by the founder."""
    db = str(tmp_path / "late.sqlite")
    mid = _park(db, "late-bind")
    spy = SpySigner()
    svc = MissionService(db, signer=spy)
    try:
        real = svc.runtime.published_authorization_request
        # 1. The founder decides before the mission has published its request: no artifact.
        monkeypatch.setattr(svc.runtime, "published_authorization_request", lambda _mid: None)
        first = svc.approve_mission_gate(mid, reason="ok", idempotency_key="late-1",
                                         expected_version=_version(svc, mid), decider=FOUNDER)
        assert _signed(first["approval"]["evidence_refs"]) == [] and spy.calls == 0
        # 2. The request is now published: late binding is possible.
        monkeypatch.setattr(svc.runtime, "published_authorization_request", real)
        with pytest.raises(ApiError) as e:
            svc.approve_mission_gate(mid, reason="ok", idempotency_key="late-op",
                                     expected_version=_version(svc, mid), decider=OPERATOR)
        assert e.value.code == "FOUNDER_REQUIRED"
        assert spy.calls == 0, "an operator must not cause the late-bound signature"
        assert all(not _signed(a.evidence_refs) for a in svc.runtime.store.list_approvals(mid))
        # 3. The founder re-affirms: now, and only now, the artifact is late-bound.
        late = svc.approve_mission_gate(mid, reason="ok", idempotency_key="late-2",
                                        expected_version=_version(svc, mid), decider=FOUNDER)
        assert spy.calls == 1
        assert len(_signed(late["approval"]["evidence_refs"])) == 1
        assert any(c.startswith(GOV_ARTIFACT_LATE_BOUND_PREFIX) for c in late["approval"]["conditions"])
    finally:
        svc.close()


def test_the_decider_is_part_of_the_idempotency_request(tmp_path):
    """The founder's key replayed under another identity is a different request."""
    db = str(tmp_path / "idem.sqlite")
    mid = _park(db, "idem-decider")
    svc = MissionService(db)
    try:
        ver = _version(svc, mid)
        svc.reject_mission_gate(mid, reason="no", idempotency_key="same", expected_version=ver, decider=FOUNDER)
        with pytest.raises(ApiError) as e:
            svc.reject_mission_gate(mid, reason="no", idempotency_key="same", expected_version=ver,
                                    decider=OPERATOR)
        assert e.value.status == 409
    finally:
        svc.close()


def test_service_decision_methods_require_an_explicit_decider(tmp_path):
    svc = MissionService(str(tmp_path / "req.sqlite"))
    try:
        for call in (svc.approve_plan, svc.reject_plan, svc.approve_mission_gate, svc.reject_mission_gate):
            with pytest.raises(TypeError):
                call("msn_x", reason="r", idempotency_key="k")  # no decider
    finally:
        svc.close()


def test_no_constant_founder_identity_remains_in_the_decision_path():
    src = (API / "service.py").read_text(encoding="utf-8")
    assert 'decided_by="jeramie"' not in src
    assert "decided_by=decider.identity" in src


# ------------------------------------------------------------ configuration --
def test_founder_auth_holds_only_a_digest():
    tok = "frf_" + "a" * 43
    fa = FounderAuth(token_sha256=founder_token_sha256(tok), identity="jeramie", environ={})
    assert fa.configured and fa.verify(tok) and not fa.verify(tok + "x") and not fa.verify(None)
    assert tok not in repr(vars(fa))


@pytest.mark.parametrize("name", FORBIDDEN_RAW_FOUNDER_TOKEN_ENV)
def test_a_raw_founder_token_in_the_environment_is_refused(name):
    with pytest.raises(FounderAuthConfigError):
        FounderAuth(environ={name: "frf_raw", ENV_FOUNDER_TOKEN_SHA256: "0" * 64,
                             ENV_FOUNDER_IDENTITY: "jeramie"})


@pytest.mark.parametrize("digest,identity", [("abc", "jeramie"), ("g" * 64, "jeramie"),
                                             ("0" * 64, "operator"), ("0" * 64, "governance:x"),
                                             ("0" * 64, ""), ("0" * 64, "Jeramie")])
def test_malformed_founder_config_is_refused(digest, identity):
    with pytest.raises(FounderAuthConfigError):
        FounderAuth(token_sha256=digest, identity=identity, environ={})


def test_founder_token_equal_to_operator_token_is_refused():
    tok = "frk_" + "b" * 43
    with pytest.raises(FounderAuthConfigError):
        FounderAuth(token_sha256=founder_token_sha256(tok), identity="jeramie",
                    operator=OperatorAuth(tok), environ={})


def test_unconfigured_founder_auth_verifies_nothing():
    fa = FounderAuth(environ={})
    assert not fa.configured and not fa.verify("anything")


def test_fr_refuses_to_start_with_a_raw_founder_token(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="HTTP transport requires fastapi")
    from recipe_engine.founder_runtime.api.app import create_app
    monkeypatch.setenv("FR_API_OPERATOR_TOKEN", "frk_" + "c" * 43)
    monkeypatch.setenv("FR_FOUNDER_TOKEN", "frf_raw_value")
    with pytest.raises(FounderAuthConfigError):
        create_app(str(tmp_path / "refuse.sqlite"), probe_live=False)


def test_openapi_documents_the_founder_only_routes():
    from recipe_engine.founder_runtime.api.openapi import generate
    paths = generate()["paths"]
    for p in ("/v1/missions/{mission_id}/approve", "/v1/missions/{mission_id}/gate/approve"):
        op = paths[p]["post"]
        assert "Founder-only" in op.get("description", "")
        assert "403" in op["responses"]


def test_published_gate_descriptor_names_the_founder_channel():
    src = (API / "service.py").read_text(encoding="utf-8")
    assert '"approve_auth": "founder credential only' in src
    assert '"approve_command": "neuruh-approve {mission_id} --gate' in src
