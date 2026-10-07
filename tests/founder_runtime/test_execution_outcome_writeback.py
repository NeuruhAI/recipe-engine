"""R02D — Founder Runtime learns the terminal execution outcome (G-03, H03).

RED FIRST. The proven defect (A01 [13]): a Workers execution reaches a sealed
terminal receipt while the Founder Runtime mission stays QUEUED, receipts table
empty, WAL untouched. These tests state the contract the write-back must meet:

  event/evidence driven · idempotent · mission-id bound · work-spec bound ·
  execution-id bound · receipt-hash bound · cannot touch another mission ·
  cannot regress a terminal mission · same terminal outcome never applied twice
  consequentially · fail closed on malformed or untrusted evidence.

DISPATCHED / EXECUTING / SUCCEEDED / FAILED / COURT_PENDING / COURT_PASSED /
COURT_FAILED stay distinct. Execution completing is NOT verification: SUCCEEDED
parks the mission at VERIFYING with the court pending; COURT_PASSED moves it to
RECONCILING; nothing here ever reaches COMPLETED.
"""

from __future__ import annotations

import pytest

from recipe_engine.founder_runtime.api.service import ApiError, MissionService
from recipe_engine.founder_runtime.state.states import MissionState
from tests.founder_runtime.execution_outcome_fixtures import (
    JOB_ID,
    MISSION_B_ID,
    MISSION_B_PAYLOAD_HASH,
    MISSION_B_RECEIPT,
    court_envelope,
    create_queued,
    outcome_envelope,
    parse_receipt_lines,
    queued_service,
    seal,
    sealed_receipt_fields,
    workers_content_sha,
)


def _post(service: MissionService, body, *, key: str | None = None):
    key = key or f"k:{body['execution_id']}:{body['outcome']}:{workers_content_sha(body)[:12]}"
    return service.record_execution_outcome(body["mission_id"], body=body, idempotency_key=key)


def _mission(service: MissionService, mission_id: str):
    return service.get_mission(mission_id)


def _snapshot(service: MissionService, mission_id: str) -> dict:
    detail = _mission(service, mission_id)
    return {
        "state": detail["envelope"]["state"],
        "version": detail["envelope"]["version"],
        "events": service.events(mission_id)["count"],
        "execution": detail["mission"]["extensions"].get("execution"),
    }


def _rejected(service: MissionService, body, code: str, *, status: int = 409, key: str | None = None):
    before = _snapshot(service, body["mission_id"]) if service.runtime.store.get_mission(body["mission_id"]) else None
    with pytest.raises(ApiError) as excinfo:
        _post(service, body, key=key)
    assert excinfo.value.code == code, (excinfo.value.code, excinfo.value.message, excinfo.value.details)
    assert excinfo.value.status == status
    if before is not None:
        assert _snapshot(service, body["mission_id"]) == before, "a refused write-back must change nothing"
    return excinfo.value


# ---------------------------------------------------------------- RED: defect --

def test_red_g03_execution_terminal_but_founder_runtime_never_learns(tmp_path):
    """The seam: a sealed SUCCEEDED receipt exists; FR must reach a terminal
    execution state and expose that receipt. Before R02D this raised
    AttributeError (no write-back path at all)."""
    service = queued_service(tmp_path)
    env = create_queued(service)
    assert env["state"] == "QUEUED"
    body = outcome_envelope(env, "SUCCEEDED")
    result = _post(service, body)
    assert result["applied"] is True
    assert result["state_before"] == "QUEUED"
    assert result["state_after"] == "VERIFYING"
    detail = _mission(service, env["mission_id"])
    assert detail["envelope"]["state"] == "VERIFYING"
    receipts = service.receipts(env["mission_id"])
    sealed = [o for o in receipts["execution_outcomes"] if o["outcome"] == "SUCCEEDED"]
    assert len(sealed) == 1
    assert sealed[0]["execution_outcome"]["receipt"]["fields"] == body["receipt"]["fields"]
    assert receipts["execution"]["receipt_sha"] == body["receipt"]["receipt_sha"]
    service.close()


def test_red_golden_mission_b_receipt_hash_is_reproduced_by_the_runtime():
    """FR must recompute the emitter's PAYLOAD_HASH (sha256 of workers
    canonical JSON of the fields minus PAYLOAD_HASH) on the real Mission B
    receipt, byte-identical to the live immutable file."""
    from recipe_engine.founder_runtime.evidence.execution_outcome import (
        dispatch_receipt_sha, parse_receipt_text,
    )
    text = MISSION_B_RECEIPT.read_text(encoding="utf-8")
    fields = parse_receipt_text(text)
    assert fields == parse_receipt_lines(text)
    assert fields["MISSION_ID"] == MISSION_B_ID
    assert fields["PAYLOAD_HASH"] == MISSION_B_PAYLOAD_HASH
    assert dispatch_receipt_sha(fields) == MISSION_B_PAYLOAD_HASH
    # and the test-side, implementation-independent recomputation agrees
    assert seal(fields)["PAYLOAD_HASH"] == MISSION_B_PAYLOAD_HASH


# --------------------------------------------------- distinct outcome states --

def test_dispatched_executing_succeeded_are_distinct_and_none_is_completed(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    mid = env["mission_id"]

    r1 = _post(service, outcome_envelope(env, "DISPATCHED"))
    assert (r1["state_before"], r1["state_after"]) == ("QUEUED", "EXECUTING")
    ex = _snapshot(service, mid)["execution"]
    assert ex["phase"] == "DISPATCHED" and ex["court"] == "NOT_REQUESTED"
    assert ex["execution_id"] == JOB_ID and ex["dispatched_version"] == env["version"]

    r2 = _post(service, outcome_envelope(env, "EXECUTING"))
    assert (r2["state_before"], r2["state_after"]) == ("EXECUTING", "EXECUTING")
    assert r2["applied"] is True  # same-state audit event, not a replay
    ex = _snapshot(service, mid)["execution"]
    assert ex["phase"] == "EXECUTING"

    r3 = _post(service, outcome_envelope(env, "SUCCEEDED"))
    assert (r3["state_before"], r3["state_after"]) == ("EXECUTING", "VERIFYING")
    ex = _snapshot(service, mid)["execution"]
    assert ex["phase"] == "SUCCEEDED" and ex["court"] == "PENDING"
    assert ex["verified"] is False
    assert [o["outcome"] for o in ex["outcomes"]] == ["DISPATCHED", "EXECUTING", "SUCCEEDED"]
    assert _mission(service, mid)["envelope"]["state"] not in ("COMPLETED", "COMPLETED_WITH_EXCEPTIONS")
    service.close()


def test_succeeded_with_court_pending_is_not_verified_and_cannot_complete(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    mid = env["mission_id"]
    _post(service, outcome_envelope(env, "SUCCEEDED"))
    detail = _mission(service, mid)
    assert detail["envelope"]["state"] == "VERIFYING"
    ex = detail["mission"]["extensions"]["execution"]
    assert ex["court"] == "PENDING" and ex["verified"] is False
    # the tick's view: no longer dispatchable, and never "executed" by FR itself
    listed = next(m for m in service.list_missions()["missions"] if m["mission_id"] == mid)
    assert listed["state"] == "VERIFYING"
    assert listed["authority"]["work_spec_execution_authorized"] is False
    assert listed["authority"]["executed"] is False
    # completion is still only reachable through the canonical verifier
    from recipe_engine.founder_runtime.errors import StateTransitionInvalid
    with pytest.raises(StateTransitionInvalid):
        service.runtime.transition(mid, MissionState.COMPLETED, reason="shortcut")
    service.close()


def test_court_passed_after_success_reconciles_but_does_not_complete(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    mid = env["mission_id"]
    ok = outcome_envelope(env, "SUCCEEDED")
    _post(service, ok)
    r = _post(service, court_envelope(env, "COURT_PASSED", ok))
    assert (r["state_before"], r["state_after"]) == ("VERIFYING", "RECONCILING")
    ex = _snapshot(service, mid)["execution"]
    assert ex["phase"] == "COURT_PASSED" and ex["court"] == "PASSED"
    assert ex["receipt_sha"] == ok["receipt"]["receipt_sha"]
    assert ex["court_id"] == "court-0123456789abcdef01234567"
    assert ex["verified"] is False
    assert _mission(service, mid)["envelope"]["state"] == "RECONCILING"
    service.close()


def test_court_failure_after_successful_execution(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    mid = env["mission_id"]
    ok = outcome_envelope(env, "SUCCEEDED")
    _post(service, ok)
    r = _post(service, court_envelope(env, "COURT_FAILED", ok))
    assert (r["state_before"], r["state_after"]) == ("VERIFYING", "FAILED_RETRYABLE")
    ex = _snapshot(service, mid)["execution"]
    assert ex["phase"] == "COURT_FAILED" and ex["court"] == "FAILED"
    assert ex["receipt_sha"] == ok["receipt"]["receipt_sha"]  # evidence retained, verdict distinct
    # the terminal-ish states do not collapse: an execution FAILED is a different phase
    assert ex["phase"] != "FAILED"
    service.close()


def test_failed_after_queued_is_execution_failure_not_court_failure(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    mid = env["mission_id"]
    r = _post(service, outcome_envelope(env, "FAILED", failure={"error_class": "WORKFLOW_STEP_FAILED",
                                                                   "detail": "step 2 raised"}))
    assert (r["state_before"], r["state_after"]) == ("QUEUED", "FAILED_RETRYABLE")
    ex = _snapshot(service, mid)["execution"]
    assert ex["phase"] == "FAILED" and ex["court"] == "NOT_REQUESTED" and ex["receipt_sha"] is None
    # the implied EXECUTING hop is recorded honestly, not fabricated as a sender claim
    assert [(o["outcome"], o["implied"]) for o in ex["outcomes"]] == [("EXECUTING", True), ("FAILED", False)]
    # a later SUCCEEDED for the same execution contradicts the terminal result
    _rejected(service, outcome_envelope(env, "SUCCEEDED"), "OUTCOME_CONFLICT")
    # and a court cannot judge evidence FR never received
    _rejected(service, outcome_envelope(env, "COURT_PASSED"), "OUTCOME_OUT_OF_ORDER")
    service.close()


# ------------------------------------------------------------ idempotency --

def test_duplicate_write_back_is_idempotent_and_non_consequential(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    mid = env["mission_id"]
    body = outcome_envelope(env, "SUCCEEDED")
    first = _post(service, body, key="same-key")
    after_first = _snapshot(service, mid)
    # exact replay, same key
    again = _post(service, body, key="same-key")
    assert again["idempotent_replay"] is True and again["applied"] is False
    assert _snapshot(service, mid) == after_first
    # same evidence, DIFFERENT key (restarted sender): still not applied twice
    fresh = _post(service, body, key="another-key")
    assert fresh["idempotent_replay"] is True and fresh["applied"] is False
    assert _snapshot(service, mid) == after_first
    assert first["events_appended"] and not fresh["events_appended"]
    service.close()


def test_same_key_with_different_body_is_an_idempotency_conflict(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    body = outcome_envelope(env, "DISPATCHED")
    _post(service, body, key="k1")
    other = outcome_envelope(env, "EXECUTING")
    with pytest.raises(ApiError) as excinfo:
        _post(service, other, key="k1")
    assert excinfo.value.code == "IDEMPOTENCY_CONFLICT"
    service.close()


# ------------------------------------------------------------- bindings --

def test_wrong_mission_id_cannot_mutate_an_unrelated_mission(tmp_path):
    service = queued_service(tmp_path)
    a = create_queued(service, key="a")
    b = create_queued(service, key="b", spec=None)
    assert a["mission_id"] != b["mission_id"]
    body_for_a = outcome_envelope(a, "SUCCEEDED")
    before_b = _snapshot(service, b["mission_id"])
    before_a = _snapshot(service, a["mission_id"])
    with pytest.raises(ApiError) as excinfo:
        service.record_execution_outcome(b["mission_id"], body=body_for_a, idempotency_key="x")
    assert excinfo.value.code == "MISSION_MISMATCH"
    assert _snapshot(service, b["mission_id"]) == before_b
    assert _snapshot(service, a["mission_id"]) == before_a
    # a receipt whose MISSION_ID names another mission is refused even on its own route
    fields = sealed_receipt_fields(a, overrides={"MISSION_ID": b["mission_id"]})
    _rejected(service, outcome_envelope(a, "SUCCEEDED", receipt_fields=fields), "RECEIPT_BINDING_MISMATCH")
    service.close()


def test_wrong_work_spec_sha256_is_refused(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    wrong = "a" * 64
    _rejected(service, outcome_envelope(env, "DISPATCHED", work_spec_sha256=wrong), "WORK_SPEC_MISMATCH")
    # a receipt whose WORK_SPEC_SHA256 disagrees with the envelope is refused too
    fields = sealed_receipt_fields(env, overrides={"WORK_SPEC_SHA256": wrong})
    _rejected(service, outcome_envelope(env, "SUCCEEDED", receipt_fields=fields), "RECEIPT_BINDING_MISMATCH")
    service.close()


def test_wrong_receipt_hash_is_refused(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    fields = sealed_receipt_fields(env)
    # tamper a field AFTER sealing: PAYLOAD_HASH no longer covers the body
    tampered = {**fields, "JOB_SUCCEEDED_SHA": "b" * 64}
    _rejected(service, outcome_envelope(env, "SUCCEEDED", receipt_fields=tampered), "RECEIPT_HASH_MISMATCH")
    # envelope receipt_sha disagrees with PAYLOAD_HASH
    body = outcome_envelope(env, "SUCCEEDED", receipt_fields=fields)
    body["receipt"]["receipt_sha"] = "c" * 64
    _rejected(service, body, "RECEIPT_HASH_MISMATCH")
    # forged PAYLOAD_HASH over a tampered body
    forged = {**tampered, "PAYLOAD_HASH": "d" * 64}
    body = outcome_envelope(env, "SUCCEEDED", receipt_fields=forged)
    _rejected(service, body, "RECEIPT_HASH_MISMATCH")
    service.close()


def test_wrong_authority_reference_is_refused(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service, key="one")
    other = create_queued(service, key="two")
    sha = env["work_spec_sha256"]
    foreign = f"fr-api-v1:approval:{other['authority']['dispatch_approval_id']}:work-spec:{sha}"
    _rejected(service, outcome_envelope(env, "DISPATCHED", authority_reference=foreign), "AUTHORITY_MISMATCH")
    unknown = f"fr-api-v1:approval:appr_{'0' * 32}:work-spec:{sha}"
    _rejected(service, outcome_envelope(env, "DISPATCHED", authority_reference=unknown), "AUTHORITY_MISMATCH")
    wrong_sha = f"fr-api-v1:approval:{env['authority']['dispatch_approval_id']}:work-spec:{'e' * 64}"
    _rejected(service, outcome_envelope(env, "DISPATCHED", authority_reference=wrong_sha), "AUTHORITY_MISMATCH")
    service.close()


def test_mission_version_must_be_the_dispatched_version(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    _rejected(service, outcome_envelope(env, "DISPATCHED", mission_version=env["version"] + 1),
              "MISSION_VERSION_MISMATCH")
    _post(service, outcome_envelope(env, "DISPATCHED"))
    # FR's version advanced with the event; the binding stays the DISPATCHED version
    now = _mission(service, env["mission_id"])["envelope"]["version"]
    assert now == env["version"] + 1
    # a receipt and envelope that agree with each other but name the CURRENT
    # version instead of the dispatched one are consistently wrong
    _rejected(service, outcome_envelope({**env, "version": now}, "SUCCEEDED"), "MISSION_VERSION_MISMATCH")
    r = _post(service, outcome_envelope(env, "SUCCEEDED"))
    assert r["state_after"] == "VERIFYING"
    service.close()


def test_execution_id_is_bound_for_the_life_of_the_dispatch(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    _post(service, outcome_envelope(env, "DISPATCHED"))
    _rejected(service, outcome_envelope(env, "SUCCEEDED", job_id="job-ffffffffffffffffffffffff"),
              "EXECUTION_ID_MISMATCH")
    service.close()


# ------------------------------------------------------------- ordering --

def test_stale_event_after_terminal_execution_is_refused(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    ok = outcome_envelope(env, "SUCCEEDED")
    _post(service, ok)
    _rejected(service, outcome_envelope(env, "DISPATCHED"), "OUTCOME_STALE")
    _rejected(service, outcome_envelope(env, "EXECUTING"), "OUTCOME_STALE")
    # a contradicting terminal for the same execution
    _rejected(service, outcome_envelope(env, "FAILED"), "OUTCOME_CONFLICT")
    # after the court: an identical replay of the accepted evidence is still a
    # non-consequential replay; anything earlier is stale; anything different is a conflict
    before = _snapshot(service, env["mission_id"])
    _post(service, court_envelope(env, "COURT_PASSED", ok))
    after_court = _snapshot(service, env["mission_id"])
    assert after_court != before
    again = _post(service, ok)
    assert again["idempotent_replay"] is True and again["applied"] is False
    assert _snapshot(service, env["mission_id"]) == after_court
    _rejected(service, outcome_envelope(env, "DISPATCHED"), "OUTCOME_STALE")
    other = sealed_receipt_fields(env, overrides={"LEDGER_TAIL_SHA": "8" * 64})
    _rejected(service, outcome_envelope(env, "SUCCEEDED", receipt_fields=other), "OUTCOME_CONFLICT")
    _rejected(service, court_envelope(env, "COURT_FAILED", ok), "OUTCOME_CONFLICT")
    service.close()


def test_out_of_order_court_before_execution_is_refused(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    _rejected(service, outcome_envelope(env, "COURT_PASSED"), "OUTCOME_OUT_OF_ORDER")
    _post(service, outcome_envelope(env, "DISPATCHED"))
    _rejected(service, outcome_envelope(env, "COURT_PASSED"), "OUTCOME_OUT_OF_ORDER")
    ok = outcome_envelope(env, "SUCCEEDED")
    _post(service, ok)
    # a court bound to a different receipt than the one FR accepted
    _rejected(service, court_envelope(env, "COURT_PASSED", ok, receipt_sha="f" * 64), "RECEIPT_BINDING_MISMATCH")
    service.close()


def test_same_outcome_with_different_evidence_is_a_conflict_not_a_replay(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    _post(service, outcome_envelope(env, "SUCCEEDED"))
    other_receipt = sealed_receipt_fields(env, overrides={"LEDGER_TAIL_SHA": "9" * 64})
    _rejected(service, outcome_envelope(env, "SUCCEEDED", receipt_fields=other_receipt), "OUTCOME_CONFLICT")
    service.close()


# ------------------------------------------------------- mission guards --

def test_terminal_mission_is_never_regressed(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    mid = env["mission_id"]
    service.cancel(mid, idempotency_key="cancel", expected_version=env["version"])
    assert _mission(service, mid)["envelope"]["state"] == "CANCELLED"
    for outcome in ("DISPATCHED", "SUCCEEDED", "FAILED"):
        _rejected(service, outcome_envelope(env, outcome), "MISSION_TERMINAL")
    assert _mission(service, mid)["envelope"]["state"] == "CANCELLED"
    service.close()


def test_mission_not_dispatched_by_fr_cannot_receive_an_outcome(tmp_path):
    # no governance port: the mission parks at AWAITING_APPROVAL with no consumed gate
    service = MissionService(str(tmp_path / "parked.sqlite"))
    parked = service.create_mission(objective="status", idempotency_key="p")["envelope"]
    assert parked["state"] == "AWAITING_APPROVAL"
    fake = {**parked, "work_spec_sha256": "1" * 64,
            "authority": {**parked["authority"], "dispatch_approval_id": "appr_" + "0" * 32}}
    with pytest.raises(ApiError) as excinfo:
        _post(service, outcome_envelope(fake, "DISPATCHED"))
    assert excinfo.value.code in ("WORK_SPEC_ABSENT", "NOT_DISPATCHED_STATE", "AUTHORITY_MISMATCH")
    assert _mission(service, parked["mission_id"])["envelope"]["state"] == "AWAITING_APPROVAL"
    service.close()


def test_paused_mission_refuses_outcomes_until_resumed(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    mid = env["mission_id"]
    service.pause(mid, idempotency_key="pause", expected_version=env["version"])
    assert _mission(service, mid)["envelope"]["state"] == "PAUSED"
    _rejected(service, outcome_envelope(env, "DISPATCHED"), "MISSION_STATE_CONFLICT")
    service.close()


def test_unknown_mission_is_404(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    body = outcome_envelope(env, "DISPATCHED", mission_id="msn_" + "0" * 32)
    with pytest.raises(ApiError) as excinfo:
        service.record_execution_outcome(body["mission_id"], body=body, idempotency_key="x")
    assert excinfo.value.status == 404
    service.close()


# ---------------------------------------------------------- fail closed --

@pytest.mark.parametrize("mutate", [
    lambda b: b.update({"unexpected": 1}),
    lambda b: b.update({"schema": "fr.execution_outcome.v2"}),
    lambda b: b.update({"outcome": "COURT_PENDING"}),          # projection-only vocabulary
    lambda b: b.update({"outcome": "COMPLETED"}),              # never an input
    lambda b: b.update({"work_spec_sha256": "not-hex"}),
    lambda b: b.update({"mission_version": "15"}),
    lambda b: b.update({"execution_id": ""}),
    lambda b: b.update({"authority_reference": "self"}),
    lambda b: b["source"].update({"head": "abc"}),
    lambda b: b.pop("source"),
])
def test_malformed_envelopes_fail_closed(tmp_path, mutate):
    service = queued_service(tmp_path)
    env = create_queued(service)
    body = outcome_envelope(env, "DISPATCHED")
    mutate(body)
    _rejected(service, body, "EXECUTION_OUTCOME_MALFORMED", status=400)
    service.close()


def test_succeeded_without_receipt_or_with_self_verdict_is_refused(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    body = outcome_envelope(env, "SUCCEEDED")
    body.pop("receipt")
    _rejected(service, body, "EXECUTION_OUTCOME_MALFORMED", status=400)
    # a receipt may not carry its own verdict (sentinel_closure_in_receipt_is_not_pass)
    for key in ("SENTINEL_CLOSURE", "PRIME_VERDICT"):
        fields = sealed_receipt_fields(env, overrides={key: "PASS"})
        _rejected(service, outcome_envelope(env, "SUCCEEDED", receipt_fields=fields), "RECEIPT_BINDING_MISMATCH")
    # DISPATCH_RESULT other than SUCCEEDED cannot back a SUCCEEDED outcome
    fields = sealed_receipt_fields(env, dispatch_result="FAILED")
    _rejected(service, outcome_envelope(env, "SUCCEEDED", receipt_fields=fields), "RECEIPT_BINDING_MISMATCH")
    # a receipt missing a required field is malformed
    fields = sealed_receipt_fields(env, drop=("LEDGER_TAIL_SHA",))
    _rejected(service, outcome_envelope(env, "SUCCEEDED", receipt_fields=fields), "EXECUTION_OUTCOME_MALFORMED", status=400)
    # a credential-shaped value is refused
    fields = sealed_receipt_fields(env, overrides={"EVIDENCE_REFS": "ghp_" + "A" * 36})
    _rejected(service, outcome_envelope(env, "SUCCEEDED", receipt_fields=fields), "EXECUTION_OUTCOME_MALFORMED", status=400)
    service.close()


def test_court_envelope_must_be_coherent(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    ok = outcome_envelope(env, "SUCCEEDED")
    _post(service, ok)
    _rejected(service, court_envelope(env, "COURT_PASSED", ok, verdict="FAIL"), "EXECUTION_OUTCOME_MALFORMED", status=400)
    _rejected(service, court_envelope(env, "COURT_PASSED", ok, independent=False), "EXECUTION_OUTCOME_MALFORMED", status=400)
    _rejected(service, court_envelope(env, "COURT_PASSED", ok, source_role="DISPATCH_BRIDGE"), "EXECUTION_OUTCOME_MALFORMED", status=400)
    _rejected(service, court_envelope(env, "COURT_PASSED", ok, record_sha="zz"), "EXECUTION_OUTCOME_MALFORMED", status=400)
    body = court_envelope(env, "COURT_PASSED", ok)
    body.pop("court")
    _rejected(service, body, "EXECUTION_OUTCOME_MALFORMED", status=400)
    # a court outcome may not smuggle a receipt; a DISPATCHED outcome may not carry one
    _rejected(service, court_envelope(env, "COURT_PASSED", ok, **{}) | {"receipt": ok["receipt"]},
              "EXECUTION_OUTCOME_MALFORMED", status=400)
    service.close()


# ------------------------------------------------------ restart / replay --

def test_restart_replays_projection_and_refuses_double_application(tmp_path):
    db = tmp_path / "restart.sqlite"
    service = queued_service(tmp_path, "restart")
    env = create_queued(service)
    mid = env["mission_id"]
    ok = outcome_envelope(env, "SUCCEEDED")
    _post(service, ok, key="k-succeeded")
    before = _snapshot(service, mid)
    service.close()

    reopened = MissionService(str(db))
    # integrity: stored projection == rebuilt-from-events projection
    replay = reopened.replay(mid)["replay"]
    assert replay["rebuilt_matches_stored"] is True
    assert replay["final_state"] == "VERIFYING"
    assert replay["mission"]["extensions"]["execution"] == before["execution"]
    assert _snapshot(reopened, mid) == before
    # the same evidence after restart is a replay, not a second application
    again = reopened.record_execution_outcome(mid, body=ok, idempotency_key="k-after-restart")
    assert again["idempotent_replay"] is True and again["applied"] is False
    assert _snapshot(reopened, mid) == before
    # and the court can still land on the restarted runtime
    r = reopened.record_execution_outcome(mid, body=court_envelope(env, "COURT_PASSED", ok), idempotency_key="k-court")
    assert r["state_after"] == "RECONCILING"
    assert reopened.replay(mid)["replay"]["rebuilt_matches_stored"] is True
    reopened.close()


def test_events_carry_the_evidence_and_are_hash_chained(tmp_path):
    service = queued_service(tmp_path)
    env = create_queued(service)
    mid = env["mission_id"]
    ok = outcome_envelope(env, "SUCCEEDED")
    r = _post(service, ok)
    events = service.events(mid)["events"]
    mine = [e for e in events if e["event_type"] == "execution_outcome_recorded"]
    assert [e["event_id"] for e in mine] == r["events_appended"]
    assert [e["prior_state"] + "->" + e["next_state"] for e in mine] == ["QUEUED->EXECUTING", "EXECUTING->VERIFYING"]
    assert mine[0]["payload"]["execution_outcome"]["implied"] is True
    assert mine[1]["payload"]["execution_outcome"]["receipt"]["receipt_sha"] == ok["receipt"]["receipt_sha"]
    assert f"receipt_sha:{ok['receipt']['receipt_sha']}" in mine[1]["evidence_refs"]
    assert service.runtime.store.verify_mission_chain(mid)["ok"] is True
    service.close()
