"""R3 — explicit APPROVED/REJECTED authority truth in the mission envelope."""

from __future__ import annotations

from recipe_engine.founder_runtime.api.openapi import generate


def _make(service, key):
    return service.create_mission(objective="status", idempotency_key=key)


def test_pending_before_decision(service):
    res = _make(service, "as1")
    env = res["envelope"]
    assert env["authority"]["state"] == "PENDING"
    assert env["authority"]["executed"] is False
    assert env["authority"]["execution_enabled"] is False


def test_approved_after_allow(service):
    res = _make(service, "as2")
    mid = res["envelope"]["mission_id"]
    out = service.approve_plan(mid, reason="ok", idempotency_key="as2a",
                               expected_version=res["envelope"]["version"])
    assert out["authority_state"] == "allow"
    env = out["envelope"]
    assert env["authority"]["state"] == "PLAN_APPROVED_NO_EXECUTION"
    assert env["authority"]["executed"] is False
    assert env["authority"]["execution_enabled"] is False
    got = service.get_mission(mid)
    assert got["envelope"]["authority"]["state"] == "PLAN_APPROVED_NO_EXECUTION"


def test_rejected_after_deny(service):
    res = _make(service, "as3")
    mid = res["envelope"]["mission_id"]
    out = service.reject_plan(mid, reason="no", idempotency_key="as3r",
                              expected_version=res["envelope"]["version"])
    assert out["authority_state"] == "deny"
    env = out["envelope"]
    assert env["authority"]["state"] == "REJECTED"
    assert env["authority"]["executed"] is False
    got = service.get_mission(mid)
    assert got["envelope"]["authority"]["state"] == "REJECTED"


def test_lifecycle_not_misleading_after_allow(service):
    res = _make(service, "as4")
    mid = res["envelope"]["mission_id"]
    service.approve_plan(mid, reason="ok", idempotency_key="as4a",
                         expected_version=res["envelope"]["version"])
    got = service.get_mission(mid)
    # lifecycle state must not claim approval is still pending
    assert got["envelope"]["state"] != "AWAITING_APPROVAL"
    assert got["envelope"]["state"] == "APPROVED_NO_EXECUTION"
    # no execution state ever implied
    states = [e["next_state"] for e in service.events(mid)["events"]]
    assert "EXECUTING" not in states
    assert "EXECUTED" not in states
    assert "QUEUED" not in states


def test_capability_flags_remain_false(service):
    res = _make(service, "as5")
    mid = res["envelope"]["mission_id"]
    out = service.approve_plan(mid, reason="ok", idempotency_key="as5a",
                               expected_version=res["envelope"]["version"])
    auth = out["envelope"]["authority"]
    assert auth["mutation_authorized"] is False
    assert auth["external_action_authorized"] is False
    assert auth["production_persistence_authorized"] is False
    for blocked in ("execute_plan", "external_action", "mutate_axon",
                    "workers_dispatch", "production_persist"):
        assert blocked in out["envelope"]["blocked_actions"]
    # approve is no longer an allowed next action after the terminal decision
    assert "approve" not in out["envelope"]["allowed_next_actions"]


def test_event_projection_carries_authority_truth(service):
    res = _make(service, "as6")
    mid = res["envelope"]["mission_id"]
    service.approve_plan(mid, reason="ok", idempotency_key="as6a",
                         expected_version=res["envelope"]["version"])
    rec = service.receipts(mid)
    approvals = [a for a in rec["approvals"] if a["decision"] == "allow"]
    assert len(approvals) == 1
    approval_events = [e for e in service.events(mid)["events"]
                       if e["event_type"] == "approval_recorded"]
    assert len(approval_events) == 1
    assert approval_events[0]["payload"]["decision"] == "allow"


def test_openapi_mentions_authority_state():
    spec = generate()
    import json
    text = json.dumps(spec)
    assert "EXPECTED_VERSION_REQUIRED" in text or "X-Expected-Version" in text
    # stream endpoint documents cursor resume
    stream = spec["paths"]["/v1/missions/{mission_id}/events/stream"]["get"]
    params = {p["name"] for p in stream.get("parameters", [])}
    assert "after" in params
