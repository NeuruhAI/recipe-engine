"""API contract tests: full loop, envelope shape, blocked actions."""

from __future__ import annotations

REQUIRED_ENVELOPE_KEYS = {
    "mission_id", "state", "version", "evidence_refs", "authority",
    "allowed_next_actions", "blocked_actions", "freshness", "receipt_id",
}


def _assert_envelope(env):
    assert REQUIRED_ENVELOPE_KEYS <= set(env), f"missing keys: {REQUIRED_ENVELOPE_KEYS - set(env)}"
    assert env["authority"]["mutation_authorized"] is False
    assert env["authority"]["external_action_authorized"] is False
    assert env["authority"]["production_persistence_authorized"] is False
    for blocked in ("execute_plan", "external_action", "mutate_axon", "workers_dispatch"):
        assert blocked in env["blocked_actions"]


def test_create_mission_returns_packet_and_plan(service):
    res = service.create_mission(
        objective="Where is the organism and what is the safest next plan?",
        done_criteria="status and plan reported",
        idempotency_key="c1",
    )
    _assert_envelope(res["envelope"])
    assert res["envelope"]["state"] == "AWAITING_APPROVAL"
    packet = res["decision_packet"]
    assert 0 <= len(packet["selected_strategy_ids"]) <= 4
    assert res["proposed_plan"] is not None
    assert "approve" in res["envelope"]["allowed_next_actions"]


def test_get_and_list_missions(service):
    res = service.create_mission(objective="status check", idempotency_key="c2")
    mid = res["envelope"]["mission_id"]
    got = service.get_mission(mid)
    _assert_envelope(got["envelope"])
    listing = service.list_missions()
    assert listing["count"] >= 1
    assert any(m["mission_id"] == mid for m in listing["missions"])


def test_decision_packet_and_plan_endpoints(service):
    res = service.create_mission(objective="status check", idempotency_key="c3")
    mid = res["envelope"]["mission_id"]
    pkt = service.decision_packet(mid)
    assert pkt["decision_packet"]["selected_strategy_ids"]
    plan = service.proposed_plan(mid)
    assert plan["proposed_plan"] is not None
    assert plan["plan_id"]


def test_events_append_only(service):
    res = service.create_mission(objective="status check", idempotency_key="c4")
    mid = res["envelope"]["mission_id"]
    ev = service.events(mid)
    assert ev["count"] >= 1
    hashes = [e["event_hash"] for e in ev["events"]]
    assert all(hashes)
    # hash-chained
    prev = "0" * 64
    for e in ev["events"]:
        assert e["previous_event_hash"] == prev
        prev = e["event_hash"]


def test_receipts_and_provenance(service):
    res = service.create_mission(objective="status check", idempotency_key="c5")
    mid = res["envelope"]["mission_id"]
    rec = service.receipts(mid)
    assert "receipts" in rec and "approvals" in rec and "checkpoints" in rec
    _assert_envelope(rec["envelope"])


def test_replay_matches_projection(service):
    res = service.create_mission(objective="status check", idempotency_key="c6")
    mid = res["envelope"]["mission_id"]
    rp = service.replay(mid)
    assert rp["replay"]["final_state"] == res["envelope"]["state"]
    assert rp["replay"]["projection_hash"]
