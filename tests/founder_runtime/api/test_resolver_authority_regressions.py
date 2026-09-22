"""RED regressions for the authority defects the adversarial review confirmed.

Each test states a property the resolver must hold. They were written to FAIL
against the first cut of the resolver and are the pins that keep those defects
closed:

  A  a founder (or Governance) DENY is never filtered out of authority handling
  B  a replay may queue only after the port, the attested source SHA, the exact
     consequence and the exact recorded authorization have all been revalidated
  D  a policy-blocked mission never persists an ALLOW ahead of a transition that
     then fails
  E  pause/resume preserves the authority/block state; it cannot launder
     BLOCKED_POLICY back to the gate
  F  a resolver-queued mission has one legal recovery path after pause and is
     never stranded in APPROVED_NO_EXECUTION
"""

from __future__ import annotations

from tests.founder_runtime.deciders import FOUNDER

import pytest

from recipe_engine.founder_runtime.api.service import ApiError, MissionService
from recipe_engine.founder_runtime.contracts.approval import ApprovalDecision
from recipe_engine.founder_runtime.runtime import approval_resolver as resolver
from recipe_engine.founder_runtime.runtime.approvals import gated_action_for
from recipe_engine.founder_runtime.state.states import MissionState

from tests.founder_runtime.api.test_approval_resolver import (
    SHA,
    _Governance,
    _grant,
)

GATE_ACTION, GATE_SCOPE = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)


def _svc(tmp_path, name, **kw):
    kw.setdefault("governance", _Governance())
    kw.setdefault("attested_source_sha", SHA)
    kw.setdefault("auto_resolve", False)
    return MissionService(str(tmp_path / f"{name}.sqlite"), **kw)


def _park(svc, objective="status check of the organism", key="k"):
    res = svc.create_mission(objective=objective, idempotency_key=key)
    mid = res["envelope"]["mission_id"]
    assert res["envelope"]["state"] == "AWAITING_APPROVAL"
    return mid


def _version(svc, mid):
    return int(svc.get_mission(mid)["envelope"]["version"])


def _state(svc, mid):
    return svc.get_mission(mid)["envelope"]["state"]


def _approvals(svc, mid):
    return svc.runtime.store.list_approvals(mid)


# ------------------------------------------------------------------- A: DENY --
def test_founder_gate_deny_is_never_overridden_by_the_resolver(tmp_path):
    """A. The founder said no. No Governance answer may undo that."""
    gov = _Governance()
    svc = _svc(tmp_path, "deny-a", governance=gov)
    mid = _park(svc, key="a1")
    svc.reject_mission_gate(mid, reason="I do not want this mission run",
                            idempotency_key="a1-reject", expected_version=_version(svc, mid), decider=FOUNDER)
    out = svc.resolve_authority(mid, idempotency_key="a1-resolve", expected_version=_version(svc, mid))

    assert out["authority_state"] == resolver.FOUNDER_DENIED
    assert _state(svc, mid) == "AWAITING_APPROVAL", "a denied mission is never queued"
    assert gov.calls == [], "Governance is not even asked once the founder has refused"
    decisions = [a.decision for a in _approvals(svc, mid)]
    assert ApprovalDecision.ALLOW not in decisions
    assert ApprovalDecision.CONSUMED not in decisions
    assert svc.get_mission(mid)["envelope"]["authority"]["state"] == "REJECTED"


def test_founder_deny_survives_repeated_resolve_attempts(tmp_path):
    """A. A retry loop must not eventually find a window that queues it."""
    gov = _Governance()
    svc = _svc(tmp_path, "deny-a2", governance=gov)
    mid = _park(svc, key="a2")
    svc.reject_mission_gate(mid, reason="no", idempotency_key="a2-reject",
                            expected_version=_version(svc, mid), decider=FOUNDER)
    for i in range(3):
        out = svc.resolve_authority(mid, idempotency_key=f"a2-r{i}", expected_version=_version(svc, mid))
        assert out["authority_state"] == resolver.FOUNDER_DENIED
    assert _state(svc, mid) == "AWAITING_APPROVAL"
    assert gov.calls == []


# ------------------------------------------------------------- B: replay ------
def _seed_unspent_governance_allow(svc, mid, gov, *, source_sha=SHA):
    """Record a Governance-decided ALLOW without spending it (the crash window)."""
    mission = svc.runtime.get_mission(mid)
    req = mission.authority_requirements[0]
    now = svc.clock.now_iso()
    facts = resolver.resolver_facts(mid, req, action=GATE_ACTION, scope=GATE_SCOPE,
                                    source_sha=source_sha, nonce="rn_" + "b" * 29, issued_at=now)
    envelope = gov.authorize(facts, founder_approval=None, meta=None).payload
    approval = resolver.governance_decided_approval(
        envelope, mission_id=mid, requirement=req, action=GATE_ACTION, scope=GATE_SCOPE,
        now_iso=now, source_sha=source_sha,
    )
    svc.runtime.record_approval(approval)
    return approval


def test_replay_refuses_when_the_governance_port_is_absent(tmp_path):
    """B. A leftover ALLOW must not queue a mission in a process with no authority port."""
    gov = _Governance()
    seed = _svc(tmp_path, "replay-seed", governance=gov)
    mid = _park(seed, key="b1")
    approval = _seed_unspent_governance_allow(seed, mid, gov)
    seed.close()

    offline = MissionService(str(tmp_path / "replay-seed.sqlite"), governance=None,
                             attested_source_sha=SHA, auto_resolve=False)
    out = offline.resolve_authority(mid, idempotency_key="b1-resolve",
                                    expected_version=_version(offline, mid))
    assert out["authority_state"] == resolver.UNRESOLVED
    assert out["resolution"]["reason_code"] == "RESOLVER_GOVERNANCE_PORT_ABSENT"
    assert _state(offline, mid) == "AWAITING_APPROVAL"
    assert offline.runtime.store.get_approval(approval.approval_id).decision == ApprovalDecision.ALLOW
    offline.close()


def test_replay_refuses_when_the_runtime_source_sha_has_moved(tmp_path):
    """B. A decision about one runtime version is void for another."""
    gov = _Governance()
    seed = _svc(tmp_path, "replay-sha", governance=gov)
    mid = _park(seed, key="b2")
    _seed_unspent_governance_allow(seed, mid, gov, source_sha=SHA)
    seed.close()

    advanced = MissionService(str(tmp_path / "replay-sha.sqlite"), governance=_Governance(),
                              attested_source_sha="0" * 40, auto_resolve=False)
    out = advanced.resolve_authority(mid, idempotency_key="b2-resolve",
                                     expected_version=_version(advanced, mid))
    assert out["authority_state"] != resolver.RESOLVED_QUEUED
    assert _state(advanced, mid) == "AWAITING_APPROVAL"
    advanced.close()


def test_replay_refuses_an_authorization_recorded_for_a_different_consequence(tmp_path):
    """B. The recorded ALLOW must match the consequence being resolved now."""
    gov = _Governance()
    svc = _svc(tmp_path, "replay-cls", governance=gov)
    mid = _park(svc, key="b3")
    approval = _seed_unspent_governance_allow(svc, mid, gov)
    # Rewrite the stored action_class so it no longer names this mission's class.
    approval.action_class = "A6"
    approval.compute_hash()
    c = svc.runtime.store._c()
    from recipe_engine.founder_runtime.persistence.sqlite import to_canonical
    c.execute("UPDATE approvals SET body_json=?, approval_hash=? WHERE approval_id=?",
              (to_canonical(approval.to_dict()), approval.approval_hash, approval.approval_id))
    out = svc.resolve_authority(mid, idempotency_key="b3-resolve", expected_version=_version(svc, mid))
    assert out["resolution"]["reason_code"] != "RESOLVER_REPLAY"


def test_valid_replay_still_works_and_spends_exactly_once(tmp_path):
    """B. The legitimate crash-window replay must still be honoured, once."""
    gov = _Governance()
    svc = _svc(tmp_path, "replay-ok", governance=gov)
    mid = _park(svc, key="b4")
    approval = _seed_unspent_governance_allow(svc, mid, gov)
    before = len(gov.calls)
    out = svc.resolve_authority(mid, idempotency_key="b4-resolve", expected_version=_version(svc, mid))
    assert out["authority_state"] == resolver.RESOLVED_QUEUED
    assert out["resolution"]["reason_code"] == "RESOLVER_REPLAY"
    assert _state(svc, mid) == "QUEUED"
    assert len(gov.calls) == before, "a valid replay asks Governance nothing"
    assert svc.runtime.store.get_approval(approval.approval_id).decision == ApprovalDecision.CONSUMED


# ------------------------------------------------- D: no ALLOW before a raise --
def test_approve_on_a_policy_blocked_mission_writes_nothing(tmp_path):
    """D. No approval may be persisted ahead of a transition that cannot succeed."""
    svc = _svc(tmp_path, "blocked-d", governance=_Governance(deny_classes={"read"}))
    mid = _park(svc, key="d1")
    svc.resolve_authority(mid, idempotency_key="d1-resolve", expected_version=_version(svc, mid))
    assert _state(svc, mid) == "BLOCKED_POLICY"

    with pytest.raises(ApiError) as exc:
        svc.approve_plan(mid, reason="override", idempotency_key="d1-approve",
                         expected_version=_version(svc, mid), decider=FOUNDER)
    assert exc.value.status in (409, 422)
    assert _approvals(svc, mid) == [], "nothing was persisted on the failing path"
    assert svc.get_mission(mid)["envelope"]["authority"]["state"] == "BLOCKED"
    assert "approve" not in svc.get_mission(mid)["envelope"]["allowed_next_actions"]


def test_policy_blocked_mission_never_reports_approved_authority(tmp_path):
    """D. A prior ALLOW must not make a policy-blocked mission read as approved."""
    gov = _Governance()
    svc = _svc(tmp_path, "blocked-d2", governance=gov)
    mid = _park(svc, objective="edit the README draft locally", key="d2")
    svc.approve_mission_gate(mid, reason="ok", idempotency_key="d2-approve",
                             expected_version=_version(svc, mid), decider=FOUNDER)
    svc.governance = _Governance(deny_classes={"artifact_write"})
    svc.resolve_authority(mid, idempotency_key="d2-resolve", expected_version=_version(svc, mid))
    assert _state(svc, mid) == "BLOCKED_POLICY"
    assert svc.get_mission(mid)["envelope"]["authority"]["state"] != "APPROVED"


# ------------------------------------------- E: pause/resume preserves state --
def test_pause_resume_cannot_launder_a_policy_block(tmp_path):
    """E. A policy block is not a pausable condition, so it cannot be paused away.

    BLOCKED_POLICY -> PAUSED -> AWAITING_APPROVAL is two legal edges of which
    only the direct one is approval-gated, so pause/resume was a free exit from
    a policy block. Refusing the pause closes it at the first edge.
    """
    svc = _svc(tmp_path, "launder-e", governance=_Governance(deny_classes={"read"}))
    mid = _park(svc, key="e1")
    svc.resolve_authority(mid, idempotency_key="e1-resolve", expected_version=_version(svc, mid))
    assert _state(svc, mid) == "BLOCKED_POLICY"
    with pytest.raises(ApiError) as exc:
        svc.pause(mid, idempotency_key="e1-pause", expected_version=_version(svc, mid))
    assert exc.value.code == "BLOCKED_POLICY_NOT_PAUSABLE"
    assert _state(svc, mid) == "BLOCKED_POLICY"
    assert "pause" not in svc.get_mission(mid)["envelope"]["allowed_next_actions"]


def test_pause_resume_preserves_a_founder_required_gate(tmp_path):
    """E. A founder-required mission resumes still awaiting the founder."""
    gov = _Governance()
    svc = _svc(tmp_path, "launder-e2", governance=gov)
    mid = _park(svc, objective="send the email to the buyer", key="e2")
    out = svc.resolve_authority(mid, idempotency_key="e2-resolve", expected_version=_version(svc, mid))
    assert out["authority_state"] == resolver.FOUNDER_REQUIRED
    svc.pause(mid, idempotency_key="e2-pause", expected_version=_version(svc, mid))
    svc.resume(mid, idempotency_key="e2-resume", expected_version=_version(svc, mid))
    assert _state(svc, mid) == "AWAITING_APPROVAL"
    assert _approvals(svc, mid) == []


# --------------------------------------------------- F: no stranded mission --
def test_resolver_queued_mission_returns_to_queued_after_pause(tmp_path):
    """F. The authorized mission is recoverable, not stranded."""
    gov = _Governance()
    svc = _svc(tmp_path, "strand-f", governance=gov)
    mid = _park(svc, key="f1")
    out = svc.resolve_authority(mid, idempotency_key="f1-resolve", expected_version=_version(svc, mid))
    assert out["authority_state"] == resolver.RESOLVED_QUEUED
    assert _state(svc, mid) == "QUEUED"
    svc.pause(mid, idempotency_key="f1-pause", expected_version=_version(svc, mid))
    assert _state(svc, mid) == "PAUSED"
    svc.resume(mid, idempotency_key="f1-resume", expected_version=_version(svc, mid))
    assert _state(svc, mid) == "QUEUED", "an authorized mission is never stranded"


def test_plan_approved_mission_still_resumes_to_approved_no_execution(tmp_path):
    """F. The pre-existing plan-approval resume behaviour is unchanged."""
    gov = _Governance()
    svc = _svc(tmp_path, "strand-f2", governance=gov)
    mid = _park(svc, key="f2")
    svc.approve_plan(mid, reason="ok", idempotency_key="f2-approve", expected_version=_version(svc, mid), decider=FOUNDER)
    assert _state(svc, mid) == "APPROVED_NO_EXECUTION"
    svc.pause(mid, idempotency_key="f2-pause", expected_version=_version(svc, mid))
    svc.resume(mid, idempotency_key="f2-resume", expected_version=_version(svc, mid))
    assert _state(svc, mid) == "APPROVED_NO_EXECUTION"


# ------------------------------------------- availability under a slow ask --
def test_health_still_answers_while_a_resolve_waits_on_governance(tmp_path, token=None):
    """A runtime busy asking permission must not read as an absent runtime.

    The workers dispatch bridge probes /v1/health with a 0.25s timeout and fails
    closed when it does not answer. The resolver's Governance call is a blocking
    request bounded in seconds; run on the event loop it stopped the whole ASGI
    worker, so a healthy Founder Runtime looked absent for the duration of every
    authority question.
    """
    import os
    import socket
    import threading
    import time as _time

    import pytest as _pytest
    _pytest.importorskip("fastapi", reason="HTTP transport requires fastapi")
    uvicorn = _pytest.importorskip("uvicorn", reason="HTTP transport requires uvicorn")
    import urllib.request

    from recipe_engine.founder_runtime.api.app import create_app
    from recipe_engine.founder_runtime.api.auth import generate_token
    from recipe_engine.founder_runtime.api.client import FounderRuntimeClient

    started = threading.Event()

    class _SlowGovernance(_Governance):
        def authorize(self, facts, *, founder_approval, meta, grant=None):
            started.set()
            _time.sleep(1.5)
            return super().authorize(facts, founder_approval=founder_approval, meta=meta, grant=grant)

    tok = generate_token()
    os.environ["FR_API_OPERATOR_TOKEN"] = tok
    app = create_app(str(tmp_path / "loop.sqlite"), probe_live=False,
                     governance=_SlowGovernance(), attested_source_sha=SHA, auto_resolve=False)
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    client = FounderRuntimeClient(tok, port=port)
    for _ in range(100):
        try:
            client.health(); break
        except Exception:
            _time.sleep(0.05)
    try:
        mid = _park(MissionService(str(tmp_path / "loop.sqlite"), governance=_Governance(),
                                   attested_source_sha=SHA, auto_resolve=False), key="loop1")
        version = int(client.get_mission(mid)["envelope"]["version"])
        done = []
        threading.Thread(
            target=lambda: done.append(client.resolve_gate(mid, expected_version=version)),
            daemon=True).start()
        assert started.wait(5), "the resolve reached Governance"
        # Governance is mid-answer. Health must still respond well inside the
        # 0.25s budget the workers bridge allows.
        began = _time.monotonic()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=0.25) as resp:
            assert resp.status == 200
        assert _time.monotonic() - began < 0.25
        for _ in range(80):
            if done:
                break
            _time.sleep(0.05)
        assert done and done[0]["authority_state"] == resolver.RESOLVED_QUEUED
    finally:
        server.should_exit = True
        _time.sleep(0.1)
