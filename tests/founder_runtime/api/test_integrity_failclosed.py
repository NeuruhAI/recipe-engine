"""R2 — every corruption mode is detected and every surface fails closed."""

from __future__ import annotations

import json
import sqlite3

import pytest

from recipe_engine.founder_runtime.api.service import ApiError, MissionService


def _service_with_mission(tmp_path, key="ic1"):
    db = str(tmp_path / "m.sqlite")
    svc = MissionService(db)
    res = svc.create_mission(objective="status", idempotency_key=key)
    return svc, res["envelope"]["mission_id"], db


def _conn(svc) -> sqlite3.Connection:
    return svc.runtime.store._c()


def _assert_fail_closed(svc, mid):
    integ = svc.runtime.store.verify_integrity()
    assert integ["ok"] is False, f"integrity should fail: {integ}"
    with pytest.raises(ApiError) as ei:
        svc.get_mission(mid)
    assert ei.value.code == "INTEGRITY_FAILURE"
    assert ei.value.status == 409
    with pytest.raises(ApiError) as ei:
        svc.events(mid)
    assert ei.value.code == "INTEGRITY_FAILURE"
    with pytest.raises(ApiError) as ei:
        svc.recover(mid)
    assert ei.value.code == "INTEGRITY_FAILURE"
    with pytest.raises(ApiError) as ei:
        svc.resolve_sse_cursor(mid, None)
    assert ei.value.code in ("STREAM_CORRUPTED", "INTEGRITY_FAILURE")
    with pytest.raises(ApiError) as ei:
        svc.receipts(mid)
    assert ei.value.code == "INTEGRITY_FAILURE"
    with pytest.raises(ApiError) as ei:
        svc.replay(mid)
    assert ei.value.code == "INTEGRITY_FAILURE"


def test_body_tamper_detected_and_fails_closed(tmp_path):
    svc, mid, _ = _service_with_mission(tmp_path, "tam1")
    c = _conn(svc)
    row = c.execute("SELECT sequence, body_json FROM mission_events WHERE mission_id=?"
                    " ORDER BY sequence DESC LIMIT 1", (mid,)).fetchone()
    body = json.loads(row["body_json"])
    body["payload"] = {"tampered": True}
    c.execute("UPDATE mission_events SET body_json=? WHERE mission_id=? AND sequence=?",
              (json.dumps(body), mid, row["sequence"]))
    _assert_fail_closed(svc, mid)
    svc.close()


def test_event_hash_column_only_stomp_detected(tmp_path):
    svc, mid, _ = _service_with_mission(tmp_path, "tam2")
    c = _conn(svc)
    c.execute("UPDATE mission_events SET event_hash=? WHERE mission_id=?",
              ("f" * 64, mid))
    _assert_fail_closed(svc, mid)
    svc.close()


def test_previous_hash_tamper_detected(tmp_path):
    svc, mid, _ = _service_with_mission(tmp_path, "tam3")
    c = _conn(svc)
    row = c.execute("SELECT sequence, body_json FROM mission_events WHERE mission_id=?"
                    " ORDER BY sequence DESC LIMIT 1", (mid,)).fetchone()
    body = json.loads(row["body_json"])
    body["previous_event_hash"] = "0" * 64
    # recompute hash so body self-consistency holds; the chain link must still break
    from recipe_engine.founder_runtime.contracts.events import MissionEvent
    ev = MissionEvent.from_dict(body)
    ev.compute_hash()
    body = ev.to_dict()
    c.execute("UPDATE mission_events SET body_json=?, event_hash=?, previous_event_hash=?"
              " WHERE mission_id=? AND sequence=?",
              (json.dumps(body), body["event_hash"], "0" * 64, mid, row["sequence"]))
    _assert_fail_closed(svc, mid)
    svc.close()


def test_event_deletion_detected(tmp_path):
    svc, mid, _ = _service_with_mission(tmp_path, "tam4")
    c = _conn(svc)
    c.execute("DELETE FROM mission_events WHERE mission_id=? AND sequence=(SELECT MAX(sequence)"
              " FROM mission_events WHERE mission_id=?)", (mid, mid))
    _assert_fail_closed(svc, mid)
    svc.close()


def test_event_insertion_detected(tmp_path):
    svc, mid, _ = _service_with_mission(tmp_path, "tam5")
    c = _conn(svc)
    rows = c.execute("SELECT body_json FROM mission_events WHERE mission_id=? ORDER BY sequence",
                     (mid,)).fetchall()
    body = json.loads(rows[-1]["body_json"])
    body["event_id"] = body["event_id"] + "_spliced"
    body["idempotency_key"] = body["idempotency_key"] + "_spliced"
    body["chain_position"] = body["chain_position"] + 100  # bypass db unique; gap detected
    c.execute("INSERT INTO mission_events(event_id, mission_id, prior_state, next_state,"
              " event_type, actor, reason, body_json, event_hash, chain_position,"
              " previous_event_hash, idempotency_key, timestamp)"
              " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (body["event_id"], mid, body["prior_state"], body["next_state"],
               body["event_type"], body["actor"], body["reason"], json.dumps(body),
               body["event_hash"], body["chain_position"], body["previous_event_hash"],
               body["idempotency_key"], body["timestamp"]))
    _assert_fail_closed(svc, mid)
    svc.close()


def test_event_reorder_detected(tmp_path):
    svc, mid, _ = _service_with_mission(tmp_path, "tam6")
    c = _conn(svc)
    rows = c.execute("SELECT sequence, body_json FROM mission_events WHERE mission_id=?"
                     " ORDER BY sequence DESC LIMIT 2", (mid,)).fetchall()
    a, b = rows[0], rows[1]
    c.execute("UPDATE mission_events SET body_json=? WHERE mission_id=? AND sequence=?",
              (a["body_json"], mid, b["sequence"]))
    c.execute("UPDATE mission_events SET body_json=? WHERE mission_id=? AND sequence=?",
              (b["body_json"], mid, a["sequence"]))
    _assert_fail_closed(svc, mid)
    svc.close()


def test_duplicate_sequence_detected(tmp_path):
    svc, mid, _ = _service_with_mission(tmp_path, "tam7")
    c = _conn(svc)
    row = c.execute("SELECT sequence, body_json FROM mission_events WHERE mission_id=?"
                    " ORDER BY sequence DESC LIMIT 1", (mid,)).fetchone()
    body = json.loads(row["body_json"])
    # duplicate sequence: the spliced row claims an already-used chain position
    # in its body (the column is offset only to bypass the DB unique index).
    body["event_id"] = body["event_id"] + "_dup"
    body["idempotency_key"] = body["idempotency_key"] + "_dup"
    dup_position = int(body["chain_position"])
    c.execute("INSERT INTO mission_events(event_id, mission_id, prior_state, next_state,"
              " event_type, actor, reason, body_json, event_hash, chain_position,"
              " previous_event_hash, idempotency_key, timestamp)"
              " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (body["event_id"], mid, body["prior_state"], body["next_state"],
               body["event_type"], body["actor"], body["reason"], json.dumps(body),
               body["event_hash"], dup_position + 100, body["previous_event_hash"],
               body["idempotency_key"], body["timestamp"]))
    _assert_fail_closed(svc, mid)
    svc.close()


def test_cross_mission_splice_detected(tmp_path):
    db = str(tmp_path / "m.sqlite")
    svc = MissionService(db)
    r1 = svc.create_mission(objective="status one", idempotency_key="xm1")
    r2 = svc.create_mission(objective="status two", idempotency_key="xm2")
    mid1 = r1["envelope"]["mission_id"]
    mid2 = r2["envelope"]["mission_id"]
    c = _conn(svc)
    # point the second mission's last event at the first mission (splice)
    row = c.execute("SELECT sequence FROM mission_events WHERE mission_id=?"
                    " ORDER BY sequence DESC LIMIT 1", (mid2,)).fetchone()
    # stomp the event's own mission binding AND its column to simulate a splice
    # (chain_position column offset only to bypass the DB unique index; the
    # verifier must catch the identity/chain break, not the database).
    body = json.loads(c.execute(
        "SELECT body_json FROM mission_events WHERE sequence=?", (row["sequence"],)
    ).fetchone()["body_json"])
    body["mission_id"] = mid1
    c.execute("UPDATE mission_events SET mission_id=?, body_json=?, chain_position=chain_position+100"
              " WHERE sequence=?",
              (mid1, json.dumps(body), row["sequence"]))
    integ = svc.runtime.store.verify_integrity()
    assert integ["ok"] is False
    with pytest.raises(ApiError) as ei:
        svc.get_mission(mid1)
    assert ei.value.code == "INTEGRITY_FAILURE"
    with pytest.raises(ApiError) as ei:
        svc.get_mission(mid2)
    assert ei.value.code == "INTEGRITY_FAILURE"
    svc.close()


def test_tail_truncation_detected(tmp_path):
    svc, mid, _ = _service_with_mission(tmp_path, "tam8")
    c = _conn(svc)
    # truncate the two newest events (projection still claims the full version)
    c.execute("DELETE FROM mission_events WHERE mission_id=? AND sequence IN"
              " (SELECT sequence FROM mission_events WHERE mission_id=?"
              "  ORDER BY sequence DESC LIMIT 2)", (mid, mid))
    _assert_fail_closed(svc, mid)
    svc.close()


def test_no_projection_leakage_after_corruption(tmp_path):
    svc, mid, _ = _service_with_mission(tmp_path, "tam9")
    c = _conn(svc)
    c.execute("UPDATE mission_events SET event_hash=? WHERE mission_id=?",
              ("a" * 64, mid))
    for call in (svc.get_mission, svc.events, svc.receipts, svc.replay,
                 svc.decision_packet, svc.proposed_plan):
        with pytest.raises(ApiError) as ei:
            call(mid)
        assert ei.value.code == "INTEGRITY_FAILURE"
        # error payload carries no projection, event bodies, or payloads
        assert "envelope" not in ei.value.details
        assert "events" not in ei.value.details
        assert "payload" not in json.dumps(ei.value.details)
    svc.close()


def test_corrupted_store_preserved_for_evidence(tmp_path):
    svc, mid, db = _service_with_mission(tmp_path, "tam10")
    c = _conn(svc)
    c.execute("UPDATE mission_events SET event_hash=? WHERE mission_id=?",
              ("b" * 64, mid))
    with pytest.raises(ApiError):
        svc.get_mission(mid)
    # store untouched by the failed reads — same stomped row remains
    still = c.execute("SELECT COUNT(*) AS n FROM mission_events WHERE mission_id=?"
                      " AND event_hash=?", (mid, "b" * 64)).fetchone()
    assert still["n"] >= 1
    svc.close()
