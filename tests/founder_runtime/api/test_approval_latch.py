"""R1 — mandatory expected version + exactly one terminal authority decision."""

from __future__ import annotations

from tests.founder_runtime.deciders import FOUNDER

import threading

import pytest

from recipe_engine.founder_runtime.api.service import ApiError


def _make(service, key):
    return service.create_mission(objective="status", idempotency_key=key)


def test_approve_without_expected_version_428(service):
    res = _make(service, "lv1")
    mid = res["envelope"]["mission_id"]
    with pytest.raises(ApiError) as ei:
        service.approve_plan(mid, reason="ok", idempotency_key="lv1a", decider=FOUNDER)
    assert ei.value.status == 428
    assert ei.value.code == "EXPECTED_VERSION_REQUIRED"


def test_reject_without_expected_version_428(service):
    res = _make(service, "lv2")
    mid = res["envelope"]["mission_id"]
    with pytest.raises(ApiError) as ei:
        service.reject_plan(mid, reason="no", idempotency_key="lv2a", decider=FOUNDER)
    assert ei.value.status == 428


def test_pause_resume_cancel_without_expected_version_428(service):
    res = _make(service, "lv3")
    mid = res["envelope"]["mission_id"]
    ver = res["envelope"]["version"]
    with pytest.raises(ApiError) as ei:
        service.pause(mid, idempotency_key="lv3p")
    assert ei.value.status == 428
    service.pause(mid, idempotency_key="lv3p", expected_version=ver)
    with pytest.raises(ApiError) as ei:
        service.resume(mid, idempotency_key="lv3r")
    assert ei.value.status == 428
    with pytest.raises(ApiError) as ei:
        service.cancel(mid, idempotency_key="lv3c")
    assert ei.value.status == 428


def test_16_concurrent_allows_single_winner(service):
    res = _make(service, "lv16")
    mid = res["envelope"]["mission_id"]
    ver = res["envelope"]["version"]
    results, errors = [], []

    def _approve(i):
        try:
            results.append(service.approve_plan(mid, reason="race",
                                                idempotency_key=f"lv16-{i}",
                                                expected_version=ver, decider=FOUNDER))
        except ApiError as e:
            errors.append(e)

    threads = [threading.Thread(target=_approve, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    fresh = [r for r in results if not r.get("idempotent_replay")]
    assert len(fresh) == 1, f"expected exactly one winner, got {len(fresh)}"
    assert len(errors) == 15
    assert all(e.status == 409 for e in errors)
    rec = service.receipts(mid)
    allows = [a for a in rec["approvals"] if a["decision"] == "allow"]
    assert len(allows) == 1
    events = service.events(mid)
    approval_events = [e for e in events["events"] if e["event_type"] == "approval_recorded"]
    assert len(approval_events) == 1


def test_concurrent_allow_vs_reject_single_terminal(service):
    res = _make(service, "lvr")
    mid = res["envelope"]["mission_id"]
    ver = res["envelope"]["version"]
    results, errors = [], []

    def _allow():
        try:
            results.append(("allow", service.approve_plan(mid, reason="go",
                                                          idempotency_key="lvr-a",
                                                          expected_version=ver, decider=FOUNDER)))
        except ApiError as e:
            errors.append(("allow", e))

    def _reject():
        try:
            results.append(("deny", service.reject_plan(mid, reason="stop",
                                                        idempotency_key="lvr-r",
                                                        expected_version=ver, decider=FOUNDER)))
        except ApiError as e:
            errors.append(("deny", e))

    threads = [threading.Thread(target=_allow), threading.Thread(target=_reject)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 1
    assert len(errors) == 1
    assert errors[0][1].status == 409
    rec = service.receipts(mid)
    terminal = [a for a in rec["approvals"] if a["decision"] in ("allow", "deny")]
    assert len(terminal) == 1


def test_same_key_replay_same_receipt_no_new_event(service):
    res = _make(service, "lvrp")
    mid = res["envelope"]["mission_id"]
    ver = res["envelope"]["version"]
    before = service.events(mid)["count"]
    a1 = service.approve_plan(mid, reason="ok", idempotency_key="lvrp1", expected_version=ver, decider=FOUNDER)
    after_first = service.events(mid)["count"]
    a2 = service.approve_plan(mid, reason="ok", idempotency_key="lvrp1", expected_version=ver, decider=FOUNDER)
    assert a2.get("idempotent_replay") is True
    assert a1["approval"]["approval_id"] == a2["approval"]["approval_id"]
    assert service.events(mid)["count"] == after_first > before
    got = service.get_mission(mid)
    # exactly one approval event + one parking transition; no further increments
    assert got["envelope"]["version"] == ver + 2


def test_key_body_collision_409(service):
    res = _make(service, "lvcol")
    mid = res["envelope"]["mission_id"]
    ver = res["envelope"]["version"]
    service.approve_plan(mid, reason="ok", idempotency_key="lvcol1", expected_version=ver, decider=FOUNDER)
    with pytest.raises(ApiError) as ei:
        service.approve_plan(mid, reason="changed", idempotency_key="lvcol1", expected_version=ver, decider=FOUNDER)
    assert ei.value.status == 409
    assert ei.value.code == "IDEMPOTENCY_CONFLICT"


def test_later_conflicting_decision_refused(service):
    res = _make(service, "lvconf")
    mid = res["envelope"]["mission_id"]
    ver = res["envelope"]["version"]
    service.approve_plan(mid, reason="ok", idempotency_key="lvconf1", expected_version=ver, decider=FOUNDER)
    new_ver = service.get_mission(mid)["envelope"]["version"]
    with pytest.raises(ApiError) as ei:
        service.reject_plan(mid, reason="changed mind", idempotency_key="lvconf2",
                            expected_version=new_ver, decider=FOUNDER)
    assert ei.value.status == 409
    assert ei.value.code == "DECISION_CONFLICT"
    rec = service.receipts(mid)
    terminal = [a for a in rec["approvals"] if a["decision"] in ("allow", "deny")]
    assert len(terminal) == 1


def test_no_stacked_approvals_identical_replay(service):
    res = _make(service, "lvstack")
    mid = res["envelope"]["mission_id"]
    ver = res["envelope"]["version"]
    service.approve_plan(mid, reason="ok", idempotency_key="lvstack1", expected_version=ver, decider=FOUNDER)
    new_ver = service.get_mission(mid)["envelope"]["version"]
    # same semantic decision with a different key is a replay, not a new approval
    out = service.approve_plan(mid, reason="ok", idempotency_key="lvstack2",
                               expected_version=new_ver, decider=FOUNDER)
    assert out["approval"]["decision"] == "allow"
    rec = service.receipts(mid)
    allows = [a for a in rec["approvals"] if a["decision"] == "allow"]
    assert len(allows) == 1
    approval_events = [e for e in service.events(mid)["events"]
                       if e["event_type"] == "approval_recorded"]
    assert len(approval_events) == 1


def test_version_increments_exactly_once_per_decision_event(service):
    res = _make(service, "lvonce")
    mid = res["envelope"]["mission_id"]
    ver = res["envelope"]["version"]
    service.approve_plan(mid, reason="ok", idempotency_key="lvonce1", expected_version=ver, decider=FOUNDER)
    got = service.get_mission(mid)
    # exactly one approval event and exactly one parking transition (2 appends)
    assert got["envelope"]["version"] == ver + 2
    approval_events = [e for e in service.events(mid)["events"]
                       if e["event_type"] == "approval_recorded"]
    assert len(approval_events) == 1
