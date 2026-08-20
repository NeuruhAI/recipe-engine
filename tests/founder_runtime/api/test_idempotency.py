"""Idempotency: duplicate create/approval replay, conflicting replay fails."""

from __future__ import annotations

import pytest

from recipe_engine.founder_runtime.api.service import ApiError


def test_duplicate_create_replays_same_mission(service):
    a = service.create_mission(objective="status", done_criteria="d", idempotency_key="dup1")
    b = service.create_mission(objective="status", done_criteria="d", idempotency_key="dup1")
    assert b.get("idempotent_replay") is True
    assert a["envelope"]["mission_id"] == b["envelope"]["mission_id"]


def test_conflicting_create_replay_fails(service):
    service.create_mission(objective="status", idempotency_key="dup2")
    with pytest.raises(ApiError) as ei:
        service.create_mission(objective="different objective", idempotency_key="dup2")
    assert ei.value.status == 409
    assert ei.value.code == "IDEMPOTENCY_CONFLICT"


def test_duplicate_approval_replays_receipt(service):
    res = service.create_mission(objective="status", idempotency_key="dup3")
    mid = res["envelope"]["mission_id"]
    ver = res["envelope"]["version"]
    a1 = service.approve_plan(mid, reason="ok", idempotency_key="ap1", expected_version=ver)
    a2 = service.approve_plan(mid, reason="ok", idempotency_key="ap1", expected_version=ver)
    assert a2.get("idempotent_replay") is True
    assert a1["approval"]["approval_id"] == a2["approval"]["approval_id"]


def test_conflicting_approval_replay_fails(service):
    res = service.create_mission(objective="status", idempotency_key="dup4")
    mid = res["envelope"]["mission_id"]
    ver = res["envelope"]["version"]
    service.approve_plan(mid, reason="ok", idempotency_key="ap2", expected_version=ver)
    with pytest.raises(ApiError) as ei:
        service.approve_plan(mid, reason="changed", idempotency_key="ap2", expected_version=ver)
    assert ei.value.code == "IDEMPOTENCY_CONFLICT"


def test_reject_requires_reason(service):
    res = service.create_mission(objective="status", idempotency_key="dup5")
    mid = res["envelope"]["mission_id"]
    with pytest.raises(ApiError) as ei:
        service.reject_plan(mid, reason="", idempotency_key="rj1",
                            expected_version=res["envelope"]["version"])
    assert ei.value.status == 422


def test_reject_records_authority_state(service):
    res = service.create_mission(objective="status", idempotency_key="dup6")
    mid = res["envelope"]["mission_id"]
    out = service.reject_plan(mid, reason="too risky", idempotency_key="rj2",
                              expected_version=res["envelope"]["version"])
    assert out["authority_state"] == "deny"
    assert out["executed"] is False
