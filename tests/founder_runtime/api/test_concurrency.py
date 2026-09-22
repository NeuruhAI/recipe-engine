"""Concurrency: stale version, approval race, optimistic control."""

from __future__ import annotations

from tests.founder_runtime.deciders import FOUNDER

import threading

import pytest

from recipe_engine.founder_runtime.api.service import ApiError


def test_stale_version_rejected(service):
    res = service.create_mission(objective="status", idempotency_key="cc1")
    mid = res["envelope"]["mission_id"]
    with pytest.raises(ApiError) as ei:
        service.pause(mid, idempotency_key="pp1", expected_version=999)
    assert ei.value.status == 409
    assert ei.value.code == "VERSION_CONFLICT"


def test_approval_race_single_winner(service):
    res = service.create_mission(objective="status", idempotency_key="cc2")
    mid = res["envelope"]["mission_id"]
    ver = res["envelope"]["version"]
    results = []
    errors = []

    def _approve(key):
        try:
            results.append(service.approve_plan(mid, reason="race", idempotency_key=key, expected_version=ver, decider=FOUNDER))
        except ApiError as e:
            errors.append(e)

    threads = [threading.Thread(target=_approve, args=(f"race-{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # exactly one fresh approval wins; others either replay or conflict, never double-apply
    fresh = [r for r in results if not r.get("idempotent_replay")]
    assert len(fresh) == 1
    # mission has exactly one ALLOW approval recorded for this action
    rec = service.receipts(mid)
    allows = [a for a in rec["approvals"] if a["decision"] == "allow"]
    assert len(allows) == 1


def test_second_distinct_approval_after_first_conflicts(service):
    res = service.create_mission(objective="status", idempotency_key="cc3")
    mid = res["envelope"]["mission_id"]
    ver = res["envelope"]["version"]
    service.approve_plan(mid, reason="first", idempotency_key="a1", expected_version=ver, decider=FOUNDER)
    # state advanced; a new (different key) approval at the old version is stale
    with pytest.raises(ApiError) as ei:
        service.approve_plan(mid, reason="second", idempotency_key="a2", expected_version=ver, decider=FOUNDER)
    assert ei.value.code in ("VERSION_CONFLICT", "NOT_AWAITING_APPROVAL", "MISSION_TERMINAL")


def test_concurrent_pause_resume_serialized(service):
    res = service.create_mission(objective="status", idempotency_key="cc4")
    mid = res["envelope"]["mission_id"]
    errs = []

    def _pause(i):
        try:
            service.pause(mid, idempotency_key=f"pz-{i}",
                          expected_version=res["envelope"]["version"])
        except ApiError as e:
            errs.append(e)

    threads = [threading.Thread(target=_pause, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # final state is well-defined (PAUSED); no corruption
    state = service.get_mission(mid)["envelope"]["state"]
    assert state == "PAUSED"
    integ = service.runtime.store.verify_integrity()
    assert integ["ok"]
