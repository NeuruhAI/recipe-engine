"""Recovery: process restart, deterministic replay, no event loss, corrupt tail."""

from __future__ import annotations

import pytest

from recipe_engine.founder_runtime.api.service import MissionService


def _make(db, key="rk1"):
    svc = MissionService(db)
    res = svc.create_mission(objective="status", done_criteria="d", idempotency_key=key)
    return svc, res


def test_restart_recovers_state(tmp_path):
    db = str(tmp_path / "m.sqlite")
    svc, res = _make(db)
    mid = res["envelope"]["mission_id"]
    events_before = svc.events(mid)["count"]
    proj_before = svc.replay(mid)["replay"]["projection_hash"]
    svc.close()
    # fresh process (new service on same DB)
    svc2 = MissionService(db)
    got = svc2.get_mission(mid)
    assert got["envelope"]["state"] == res["envelope"]["state"]
    assert svc2.events(mid)["count"] == events_before
    assert svc2.replay(mid)["replay"]["projection_hash"] == proj_before
    svc2.close()


def test_recovery_deterministic_and_safe(tmp_path):
    db = str(tmp_path / "m.sqlite")
    svc, res = _make(db)
    mid = res["envelope"]["mission_id"]
    svc.pause(mid, idempotency_key="pk", expected_version=res["envelope"]["version"])
    svc.close()
    svc2 = MissionService(db)
    rec = svc2.recover(mid)
    assert rec["recovery"]["status"] in ("safe_to_resume", "approval_required")
    assert rec["recovery"]["execution_resume_authorized"] is False
    # recovery twice -> identical
    rec2 = svc2.recover(mid)
    assert rec["recovery"]["replay"]["projection_hash"] == rec2["recovery"]["replay"]["projection_hash"]
    svc2.close()


def test_no_event_loss_across_restart(tmp_path):
    db = str(tmp_path / "m.sqlite")
    svc, res = _make(db)
    mid = res["envelope"]["mission_id"]
    svc.approve_plan(mid, reason="ok", idempotency_key="ak",
                     expected_version=res["envelope"]["version"])
    before = [e["event_hash"] for e in svc.events(mid)["events"]]
    svc.close()
    svc2 = MissionService(db)
    after = [e["event_hash"] for e in svc2.events(mid)["events"]]
    assert before == after
    svc2.close()


def test_corrupt_event_tail_detected(tmp_path):
    db = str(tmp_path / "m.sqlite")
    svc, res = _make(db)
    mid = res["envelope"]["mission_id"]
    # tamper with the last event's payload directly in the store
    conn = svc.runtime.store._c()
    row = conn.execute(
        "SELECT sequence, body_json FROM mission_events WHERE mission_id=? ORDER BY sequence DESC LIMIT 1",
        (mid,)).fetchone()
    import json
    body = json.loads(row["body_json"])
    body["payload"] = {"tampered": True}
    conn.execute("UPDATE mission_events SET body_json=? WHERE mission_id=? AND sequence=?",
                 (json.dumps(body), mid, row["sequence"]))
    integ = svc.runtime.store.verify_integrity()
    assert integ["ok"] is False
    svc.close()


def test_stable_projection_hash(tmp_path):
    db = str(tmp_path / "m.sqlite")
    svc, res = _make(db)
    mid = res["envelope"]["mission_id"]
    h1 = svc.replay(mid)["replay"]["projection_hash"]
    svc.close()
    svc2 = MissionService(db)
    h2 = svc2.replay(mid)["replay"]["projection_hash"]
    svc2.close()
    assert h1 == h2
