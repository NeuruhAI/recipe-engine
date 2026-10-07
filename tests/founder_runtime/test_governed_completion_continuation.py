"""Governed completion (RECONCILING -> COMPLETED) and continuation from a completed mission.

Before this, every executed mission ended at RECONCILING: a court outcome parks
it there by design and nothing built the validated receipt, verified outcome
and canonical completion report complete_mission requires. These tests pin the
reconcile contract from the evidence side: the sealed court record is built on
the TEST side the way the Sentinel organ seals it, and its digest is the one the
COURT_PASSED write-back carried, so the runtime is proven to re-verify, not to
trust.

Covered: fresh success, replay/idempotence, missing/tampered evidence, stale
state, wrong pin (identity), Governance refusal of a continuation, and a
continuation that itself reaches COMPLETED with lineage intact.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Any, Mapping

import pytest

from recipe_engine.founder_runtime.api.service import ApiError, MissionService
from recipe_engine.founder_runtime.contracts.mission import DeliverableStatus
from recipe_engine.founder_runtime.events.replay import replay_mission
from recipe_engine.founder_runtime.evidence.reconciliation import (
    EXECUTION_EXECUTOR, SCHEMA_RECONCILE_REQUEST, continuation_creation_key, workers_content_sha,
)
from recipe_engine.founder_runtime.intent.deliverables import EXECUTION_DELIVERABLE_ID
from tests.founder_runtime.api.test_approval_resolver import SHA, _Governance
from tests.founder_runtime.execution_outcome_fixtures import (
    JOB_ID, WORKERS_HEAD, court_envelope, create_queued, outcome_envelope,
    queued_service, sealed_receipt_fields, work_spec,
)

COURT_ID = "court-0123456789abcdef01234567"
SEALED_AT = "2026-09-04T23:15:30Z"


def court_record(env: Mapping[str, Any], ok: Mapping[str, Any], *, court_id: str = COURT_ID,
                 **overrides: Any) -> dict[str, Any]:
    """An independent_sentinel_court record shaped like the organ's sealed record."""
    fields = ok["receipt"]["fields"]
    record: dict[str, Any] = {
        "kind": "independent_sentinel_court",
        "independent": True,
        "source_role": "SENTINEL",
        "verdict": "PASS",
        "court_id": court_id,
        "mission_id": env["mission_id"],
        "task_id": ok["execution_id"],
        "job_id": "job-c0c0c0c0c0c0c0c0c0c0c0c0",
        "plan_id": "fr-to-workers-dispatch",
        "receipt_sha": fields["PAYLOAD_HASH"],
        "receipt_ref": ok["receipt"]["receipt_ref"],
        "authority_ref": ok["authority_reference"],
        "source_head": fields["SOURCE_HEAD"],
        "source_repo": fields["REPO"],
        "candidate_sha": fields["SOURCE_HEAD"],
        "canonical_sha": fields["SOURCE_HEAD"],
        "canonical_base_sha": fields["SOURCE_HEAD"],
        "base_relation": "candidate_is_canonical_base",
        "findings": [],
        "exceptions": [],
        "required_repairs": [],
        "evidence_items": [
            {"kind": "prime_receipt_txt", "bound": ["receipt_sha", "receipt_mission"]},
            {"kind": "switchboard_events_jsonl", "bound": ["receipt_sealed", "receipt_created"]},
            {"kind": "workers_execution_ledger_jsonl", "bound": ["execution_job", "job_succeeded_sha"]},
            {"kind": "governance_authorize_receipts_jsonl", "bound": ["authority_record"]},
        ],
        "iar_provenance": {"authorized": True, "authorization_ref": "iar:ollama-llama3.2:e8570a9c636e"},
        "sealed_at": SEALED_AT,
    }
    record.update(overrides)
    return record


def to_reconciling(service: MissionService, env: Mapping[str, Any], *, job_id: str = JOB_ID,
                   record_overrides: Mapping[str, Any] | None = None,
                   receipt_overrides: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Drive a QUEUED mission through SUCCEEDED + COURT_PASSED; return the sealed record."""
    mid = env["mission_id"]
    fields = sealed_receipt_fields(env, job_id=job_id, overrides=receipt_overrides)
    ok = outcome_envelope(env, "SUCCEEDED", job_id=job_id, receipt_fields=fields,
                          observed_at="2026-09-04T23:14:56.000Z")
    service.record_execution_outcome(mid, body=ok, idempotency_key=f"ok-{mid}")
    record = court_record(env, ok, **dict(record_overrides or {}))
    passed = court_envelope(env, "COURT_PASSED", ok, court_id=record["court_id"],
                            record_sha=workers_content_sha(record))
    passed["observed_at"] = "2026-09-04T23:16:02.000Z"
    res = service.record_execution_outcome(mid, body=passed, idempotency_key=f"court-{mid}")
    assert res["state_after"] == "RECONCILING"
    return {"ok": ok, "record": record}


def reconcile_body(env: Mapping[str, Any], record: Mapping[str, Any], *,
                   job_id: str = JOB_ID, **overrides: Any) -> dict[str, Any]:
    body = {
        "schema": SCHEMA_RECONCILE_REQUEST,
        "api_version": "fr-api-v1",
        "mission_id": env["mission_id"],
        "execution_id": job_id,
        "court_record": dict(record),
    }
    body.update(overrides)
    return body


def continuation_request(**updates: Any) -> dict[str, Any]:
    value = {
        "objective": "Read-only public-record research plan for the neighbouring parcel.",
        "done_criteria": "sealed receipt and independent court PASS for the neighbouring parcel",
        "work_spec": work_spec(params={"address": "79 Orchard Ln, Benton Harbor, MI", "county": "Berrien"}),
    }
    value.update(updates)
    return value


# ------------------------------------------------------------------ success --
def test_fresh_success_reaches_completed_with_a_verified_outcome(tmp_path):
    service = queued_service(tmp_path, "fresh")
    env = create_queued(service)
    mid = env["mission_id"]
    mission = service.runtime.get_mission(mid)
    assert [d.deliverable_id for d in mission.deliverables] == [EXECUTION_DELIVERABLE_ID]
    evidence = to_reconciling(service, env)

    res = service.reconcile_mission(mid, body=reconcile_body(env, evidence["record"]),
                                    idempotency_key="rec-1")
    assert res["applied"] is True and res["state_before"] == "RECONCILING"
    assert res["state_after"] == "COMPLETED" and res["envelope"]["state"] == "COMPLETED"

    outcome = res["outcome"]
    fields = evidence["ok"]["receipt"]["fields"]
    assert outcome["status"] == "verified" and outcome["evidence_class"] == "live_observed"
    assert outcome["execution_id"] == JOB_ID
    assert outcome["receipt_sha"] == fields["PAYLOAD_HASH"]
    assert outcome["job_succeeded_sha"] == fields["JOB_SUCCEEDED_SHA"]
    assert outcome["court_id"] == COURT_ID
    assert outcome["court_record_sha"] == workers_content_sha(evidence["record"])
    assert outcome["authority_reference"] == evidence["ok"]["authority_reference"]
    assert outcome["governance_authorization_id"] == "auth_0001"
    assert outcome["source_head"] == WORKERS_HEAD
    assert outcome["observed_at"] == SEALED_AT
    assert outcome["mutation_authorized"] is False

    completion = res["completion"]
    assert completion["final_state"] == "COMPLETED"
    assert completion["outcome_ref"] == outcome["outcome_id"]
    assert completion["evidence_refs"] == [res["receipt"]["receipt_id"]]
    assert completion["approvals_used"] == [env["authority"]["dispatch_approval_id"]]
    assert completion["authority_refs"] == [evidence["ok"]["authority_reference"]]
    assert completion["remaining_risks"] == [] and completion["recommended_follow_on"] == []
    assert {a["criterion"] for a in completion["acceptance_criteria_results"]} == {
        "sealed_dispatch_receipt_verified", "independent_court_passed", "authority_bound",
        "no_consequential_effect"}

    receipt = res["receipt"]
    assert receipt["evidence_class"] == "live_observed" and receipt["executor"] == EXECUTION_EXECUTOR
    assert receipt["files_changed"] == [] and receipt["mutation_authorized"] is False
    assert receipt["started_at"] <= receipt["ended_at"]

    # Stored, replayable, and exposed.
    stored = service.runtime.store.get_completion(mid)
    assert stored is not None and stored.completion_hash == completion["completion_hash"]
    assert service.runtime.outcome_record(outcome["outcome_id"], mid) == outcome
    mission = service.runtime.get_mission(mid)
    assert mission.deliverables[0].status == DeliverableStatus.DELIVERED
    assert replay_mission(service.runtime.store, mid)["final_state"] == "COMPLETED"
    assert service.runtime.store.verify_mission_chain(mid)["ok"] is True
    assert res["envelope"]["completion"] == {"completion_hash": completion["completion_hash"],
                                             "outcome_ref": outcome["outcome_id"]}
    assert res["continuation"] is None
    service.close()


def test_replay_is_idempotent_at_both_layers(tmp_path):
    service = queued_service(tmp_path, "replay")
    env = create_queued(service)
    mid = env["mission_id"]
    evidence = to_reconciling(service, env)
    body = reconcile_body(env, evidence["record"])
    first = service.reconcile_mission(mid, body=body, idempotency_key="rec-same")
    version = service.runtime.get_mission(mid).version
    events = len(service.runtime.store.list_events(mid))

    cached = service.reconcile_mission(mid, body=body, idempotency_key="rec-same")
    assert cached["idempotent_replay"] is True and cached["applied"] is False
    kernel = service.reconcile_mission(mid, body=body, idempotency_key="rec-fresh-key")
    assert kernel["idempotent_replay"] is True and kernel["applied"] is False
    assert kernel["completion"]["completion_hash"] == first["completion"]["completion_hash"]
    assert kernel["outcome"] == first["outcome"]
    assert service.runtime.get_mission(mid).version == version
    assert len(service.runtime.store.list_events(mid)) == events
    with pytest.raises(ApiError) as caught:
        service.reconcile_mission(mid, body=reconcile_body(env, evidence["record"], execution_id="job-ffffffffffffffffffffffff"),
                                  idempotency_key="rec-same")
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    service.close()


# ------------------------------------------------------ fail-closed evidence --
@pytest.mark.parametrize("mutate, status, code", [
    (lambda body: body.pop("court_record"), 400, "RECONCILE_MALFORMED"),
    (lambda body: body.update(extra=True), 400, "RECONCILE_MALFORMED"),
    (lambda body: body.update(schema="fr.reconcile_request.v0"), 400, "RECONCILE_MALFORMED"),
    (lambda body: body["court_record"].update(verdict="PASS ", note="tampered after sealing"), 409,
     "COURT_RECORD_MISMATCH"),
    (lambda body: body.update(court_record={"court_id": COURT_ID}), 409, "COURT_RECORD_MISMATCH"),
    (lambda body: body.update(execution_id="job-ffffffffffffffffffffffff"), 409, "EXECUTION_ID_MISMATCH"),
    (lambda body: body.update(mission_id="msn_someone_else"), 409, "MISSION_MISMATCH"),
])
def test_missing_or_tampered_evidence_fails_closed(tmp_path, mutate, status, code):
    service = queued_service(tmp_path, "tamper")
    env = create_queued(service)
    mid = env["mission_id"]
    evidence = to_reconciling(service, env)
    before = service.runtime.get_mission(mid)
    body = reconcile_body(env, evidence["record"])
    body["court_record"] = dict(body["court_record"])
    mutate(body)
    with pytest.raises(ApiError) as caught:
        service.reconcile_mission(mid, body=body, idempotency_key=f"tamper-{code}")
    assert (caught.value.status, caught.value.code) == (status, code), caught.value.details
    after = service.runtime.get_mission(mid)
    assert after.current_state.value == "RECONCILING" and after.version == before.version
    assert service.runtime.store.list_receipts(mid) == []
    assert service.runtime.store.get_completion(mid) is None
    service.close()


@pytest.mark.parametrize("overrides, code", [
    ({"findings": ["unexplained drift"]}, "COURT_RECORD_NOT_CLEAN"),
    ({"exceptions": ["waived"]}, "COURT_RECORD_NOT_CLEAN"),
    ({"verdict": "FAIL"}, "COURT_RECORD_NOT_PASS"),
    ({"kind": "prime_verdict"}, "COURT_NOT_INDEPENDENT"),
    ({"independent": False}, "COURT_NOT_INDEPENDENT"),
    ({"mission_id": "msn_0000000000000000000000000000000"}, "COURT_RECORD_BINDING_MISMATCH"),
    ({"task_id": "job-ffffffffffffffffffffffff"}, "COURT_RECORD_BINDING_MISMATCH"),
    ({"receipt_sha": "e" * 64}, "COURT_RECORD_BINDING_MISMATCH"),
    ({"base_relation": "unverified"}, "COURT_RELATION_UNVERIFIED"),
    ({"evidence_items": [{"kind": "prime_receipt_txt", "bound": ["receipt_sha"]}]}, "COURT_BINDINGS_INCOMPLETE"),
    ({"sealed_at": ""}, "COURT_RECORD_UNSEALED"),
])
def test_a_sealed_record_that_judged_less_fails_closed(tmp_path, overrides, code):
    """The digest matches (FR accepted exactly this record), yet the record itself is not enough."""
    service = queued_service(tmp_path, "weak-court")
    env = create_queued(service)
    mid = env["mission_id"]
    evidence = to_reconciling(service, env, record_overrides=overrides)
    with pytest.raises(ApiError) as caught:
        service.reconcile_mission(mid, body=reconcile_body(env, evidence["record"]), idempotency_key="weak")
    assert caught.value.status == 409 and caught.value.code == code, caught.value.details
    assert service.runtime.get_mission(mid).current_state.value == "RECONCILING"
    assert service.runtime.store.list_receipts(mid) == []
    service.close()


# ----------------------------------------------------------- stale / pins --
def test_stale_state_fails_closed(tmp_path):
    service = queued_service(tmp_path, "stale")
    env = create_queued(service)
    mid = env["mission_id"]
    fields = sealed_receipt_fields(env)
    ok = outcome_envelope(env, "SUCCEEDED", receipt_fields=fields)
    record = court_record(env, ok)

    with pytest.raises(ApiError) as caught:  # QUEUED: nothing executed yet
        service.reconcile_mission(mid, body=reconcile_body(env, record), idempotency_key="s-q")
    assert caught.value.code == "NOT_RECONCILING"
    service.record_execution_outcome(mid, body=ok, idempotency_key="s-ok")
    with pytest.raises(ApiError) as caught:  # VERIFYING: court still pending
        service.reconcile_mission(mid, body=reconcile_body(env, record), idempotency_key="s-v")
    assert caught.value.code == "NOT_RECONCILING"
    assert service.runtime.get_mission(mid).current_state.value == "VERIFYING"

    passed = court_envelope(env, "COURT_PASSED", ok, record_sha=workers_content_sha(record))
    service.record_execution_outcome(mid, body=passed, idempotency_key="s-court")
    service.reconcile_mission(mid, body=reconcile_body(env, record), idempotency_key="s-ok-rec")
    other = court_record(env, ok, sealed_at="2026-09-05T00:00:00Z")
    with pytest.raises(ApiError) as caught:  # COMPLETED on different evidence
        service.reconcile_mission(mid, body=reconcile_body(env, other), idempotency_key="s-conflict")
    assert caught.value.code == "RECONCILE_CONFLICT"
    service.close()


@pytest.mark.parametrize("overrides", [
    {"source_head": "b" * 40},
    {"candidate_sha": "b" * 40},
    {"source_repo": "NeuruhAI/some-other-repo"},
    {"authority_ref": "fr-api-v1:approval:appr_forged:work-spec:" + "c" * 64},
])
def test_a_court_that_judged_a_different_pin_or_authority_fails_closed(tmp_path, overrides):
    service = queued_service(tmp_path, "pin")
    env = create_queued(service)
    evidence = to_reconciling(service, env, record_overrides=overrides)
    with pytest.raises(ApiError) as caught:
        service.reconcile_mission(env["mission_id"], body=reconcile_body(env, evidence["record"]),
                                  idempotency_key="pin")
    assert caught.value.code == "COURT_RECORD_BINDING_MISMATCH"
    service.close()


def test_code_custody_work_does_not_complete_on_execution_evidence(tmp_path):
    service = queued_service(tmp_path, "custody")
    env = create_queued(service, spec=work_spec(code_custody_required=True))
    evidence = to_reconciling(service, env, receipt_overrides={"CODE_CUSTODY_REQUIRED": "YES"})
    with pytest.raises(ApiError) as caught:
        service.reconcile_mission(env["mission_id"], body=reconcile_body(env, evidence["record"]),
                                  idempotency_key="custody")
    assert caught.value.code == "CUSTODY_PROMOTION_EVIDENCE_REQUIRED"
    assert service.runtime.get_mission(env["mission_id"]).current_state.value == "RECONCILING"
    service.close()


def test_receipt_that_disagrees_with_the_work_spec_on_custody_fails_closed(tmp_path):
    service = queued_service(tmp_path, "custody-lie")
    env = create_queued(service, spec=work_spec(code_custody_required=True))
    evidence = to_reconciling(service, env)  # receipt says CODE_CUSTODY_REQUIRED=NO
    with pytest.raises(ApiError) as caught:
        service.reconcile_mission(env["mission_id"], body=reconcile_body(env, evidence["record"]),
                                  idempotency_key="custody-lie")
    assert caught.value.code == "RECEIPT_BINDING_MISMATCH"
    service.close()


# ------------------------------------------------------------ continuation --
def _parent_with_continuation(service: MissionService, **plan_updates: Any) -> dict[str, Any]:
    result = service.create_mission(
        objective="Read-only public-record research plan for the supplied address.",
        work_spec=work_spec(), continuation=continuation_request(**plan_updates),
        idempotency_key="parent-create")
    env = result["envelope"]
    assert env["state"] == "QUEUED"
    assert env["continuation"]["child_mission_id"] is None
    return env


def test_continuation_is_scheduled_only_from_the_completed_mission_with_lineage(tmp_path):
    service = queued_service(tmp_path, "continuation")
    parent = _parent_with_continuation(service)
    pid = parent["mission_id"]
    key = parent["continuation"]["creation_key"]
    assert key == continuation_creation_key(pid, parent["continuation"]["plan_sha256"])
    evidence = to_reconciling(service, parent)
    assert service.runtime.store.get_mission_by_creation_key(key) is None  # nothing before completion

    res = service.reconcile_mission(pid, body=reconcile_body(parent, evidence["record"]),
                                    idempotency_key="parent-rec")
    assert res["state_after"] == "COMPLETED"
    assert res["completion"]["recommended_follow_on"] == [key]
    cont = res["continuation"]
    assert cont["scheduled"] is True and cont["creation_key"] == key
    child_id = cont["child_mission_id"]
    assert cont["child_state"] == "QUEUED", cont["authority_resolution"]
    assert cont["authority_resolution"]["reason_code"] == "AUTO_ALLOW_OBSERVE"

    child = service.get_mission(child_id)["envelope"]
    lineage = child["lineage"]
    assert lineage["parent_mission_id"] == pid
    # never surfaced as parent_mission_id: the workers bridge reads that as
    # "resume this parent's waiting jobs", which a COMPLETED parent must not get
    assert "parent_mission_id" not in child
    assert lineage["parent_completion_hash"] == res["completion"]["completion_hash"]
    assert lineage["parent_outcome_id"] == res["outcome"]["outcome_id"]
    assert lineage["parent_outcome_hash"] == res["outcome"]["outcome_hash"]
    assert lineage["parent_receipt_sha"] == evidence["ok"]["receipt"]["receipt_sha"]
    assert lineage["parent_job_succeeded_sha"] == evidence["ok"]["receipt"]["fields"]["JOB_SUCCEEDED_SHA"]
    assert lineage["parent_court_id"] == COURT_ID
    assert lineage["parent_court_record_sha"] == workers_content_sha(evidence["record"])
    assert lineage["root_mission_id"] == pid and lineage["depth"] == 1
    # the child's authority is its own: a fresh Governance allow, not the parent's
    assert child["authority"]["dispatch_approval_id"] != parent["authority"]["dispatch_approval_id"]
    assert service.get_mission(pid)["envelope"]["continuation"]["child_mission_id"] == child_id

    # scheduling again (a retried reconcile) is the same child, not a second one
    again = service.reconcile_mission(pid, body=reconcile_body(parent, evidence["record"]),
                                      idempotency_key="parent-rec-retry")
    assert again["continuation"]["child_mission_id"] == child_id
    assert again["continuation"]["idempotent_replay"] is True
    assert sum(1 for m in service.runtime.store.list_missions()
               if (m.extensions or {}).get("lineage")) == 1
    service.close()


def test_continuation_child_reaches_completed_and_carries_lineage_into_its_outcome(tmp_path):
    service = queued_service(tmp_path, "chain")
    parent = _parent_with_continuation(service)
    evidence = to_reconciling(service, parent)
    res = service.reconcile_mission(parent["mission_id"], body=reconcile_body(parent, evidence["record"]),
                                    idempotency_key="chain-parent")
    child = service.get_mission(res["continuation"]["child_mission_id"])["envelope"]
    child_job = "job-1123456789abcdef01234567"
    child_evidence = to_reconciling(service, child, job_id=child_job)
    done = service.reconcile_mission(child["mission_id"],
                                     body=reconcile_body(child, child_evidence["record"], job_id=child_job),
                                     idempotency_key="chain-child")
    assert done["state_after"] == "COMPLETED"
    assert done["outcome"]["parent_mission_id"] == parent["mission_id"]
    assert done["outcome"]["parent_outcome_id"] == res["outcome"]["outcome_id"]
    assert done["outcome"]["lineage_sha256"] == child["lineage"]["lineage_sha256"]
    assert done["continuation"] is None  # the plan declared no further step
    service.close()


def test_continuation_passes_its_own_gate_governance_can_refuse_it(tmp_path):
    """External consequence parks with the exact founder gate; nothing is inherited."""
    service = queued_service(tmp_path, "refuse")
    parent = _parent_with_continuation(
        service, objective="Send the owner an email about the parcel.",
        work_spec=work_spec(external_message=True))
    evidence = to_reconciling(service, parent)
    res = service.reconcile_mission(parent["mission_id"], body=reconcile_body(parent, evidence["record"]),
                                    idempotency_key="refuse-rec")
    assert res["state_after"] == "COMPLETED"
    cont = res["continuation"]
    assert cont["scheduled"] is True
    assert cont["child_state"] == "AWAITING_APPROVAL"
    assert cont["authority_resolution"]["outcome"] == "founder_required"
    child = service.get_mission(cont["child_mission_id"])["envelope"]
    assert child["authority"]["execution_authorized"] is False
    service.close()


def test_continuation_under_a_denying_governance_is_blocked_on_policy(tmp_path):
    gov = _Governance()
    service = MissionService(str(tmp_path / "deny.sqlite"), governance=gov, attested_source_sha=SHA)
    parent = _parent_with_continuation(service)
    evidence = to_reconciling(service, parent)
    gov.deny_classes.add("read")
    res = service.reconcile_mission(parent["mission_id"], body=reconcile_body(parent, evidence["record"]),
                                    idempotency_key="deny-rec")
    assert res["continuation"]["child_state"] == "BLOCKED_POLICY"
    service.close()


def test_continuation_under_an_unqualified_runtime_identity_parks(tmp_path):
    """Wrong pin: Governance refuses the runtime's identity; the child parks, never runs."""
    gov = _Governance()
    service = MissionService(str(tmp_path / "pin.sqlite"), governance=gov, attested_source_sha=SHA)
    parent = _parent_with_continuation(service)
    evidence = to_reconciling(service, parent)
    gov.pinned_sha = "520ae4381def8be52486935e1b9c53da31e52202"
    res = service.reconcile_mission(parent["mission_id"], body=reconcile_body(parent, evidence["record"]),
                                    idempotency_key="pin-rec")
    cont = res["continuation"]
    assert cont["child_state"] == "AWAITING_APPROVAL"
    assert cont["authority_resolution"]["reason_code"] == "SOURCE_SHA_MISMATCH"
    service.close()


def test_continuation_declarations_are_validated_and_bound_to_the_creation_key(tmp_path):
    service = queued_service(tmp_path, "declare")
    with pytest.raises(ApiError) as caught:
        service.create_mission(objective="Read-only status report.", continuation=continuation_request(),
                               idempotency_key="no-spec")
    assert caught.value.code == "CONTINUATION_INVALID"
    with pytest.raises(ApiError) as caught:
        service.create_mission(objective="Read-only research plan.", work_spec=work_spec(),
                               continuation=continuation_request(extra=1), idempotency_key="extended")
    assert caught.value.code == "CONTINUATION_INVALID"
    nested: dict[str, Any] = continuation_request()
    for _ in range(9):
        nested = continuation_request(continuation=nested)
    with pytest.raises(ApiError) as caught:
        service.create_mission(objective="Read-only research plan.", work_spec=work_spec(),
                               continuation=nested, idempotency_key="too-deep")
    assert caught.value.code == "CONTINUATION_INVALID"
    service.create_mission(objective="Read-only research plan.", work_spec=work_spec(),
                           continuation=continuation_request(), idempotency_key="bound")
    with pytest.raises(ApiError) as caught:
        service.create_mission(objective="Read-only research plan.", work_spec=work_spec(),
                               continuation=continuation_request(done_criteria="something else"),
                               idempotency_key="bound")
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    service.close()


# --------------------------------------------------------- crash recovery --
@pytest.mark.parametrize("crash_at", ["receipt_row", "outcome_row", "completion_row"])
def test_a_crash_between_an_event_and_its_row_is_repaired_by_the_retry(tmp_path, monkeypatch, crash_at):
    """Each evidence step commits its event, then its side-table row. A process
    dying between the two must not strand the mission: the retry rebuilds the
    missing row from the logged event instead of re-recording (which the store
    would refuse as a reused idempotency key over a different body)."""
    from recipe_engine.founder_runtime.contracts.events import EventType
    from recipe_engine.founder_runtime.persistence.sqlite import SqliteStore
    from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime

    service = queued_service(tmp_path, f"crash-{crash_at}")
    parent = _parent_with_continuation(service)
    evidence = to_reconciling(service, parent)
    armed = {"on": True}

    class Crash(RuntimeError):
        pass

    if crash_at == "receipt_row":
        original = SqliteStore.save_receipt
        def crashing(self, receipt):
            if armed.pop("on", False):
                raise Crash("killed between the receipt event and its row")
            return original(self, receipt)
        monkeypatch.setattr(SqliteStore, "save_receipt", crashing)
    elif crash_at == "outcome_row":
        original = MissionRuntime._record_same_state_event
        def crashing(self, mission_id, event_type, *args, **kwargs):
            result = original(self, mission_id, event_type, *args, **kwargs)
            if event_type == EventType.OUTCOME_RECORDED and armed.pop("on", False):
                raise Crash("killed between the outcome event and its row")
            return result
        monkeypatch.setattr(MissionRuntime, "_record_same_state_event", crashing)
    else:
        original = SqliteStore.save_completion
        def crashing(self, report):
            if armed.pop("on", False):
                raise Crash("killed between the completion event and its row")
            return original(self, report)
        monkeypatch.setattr(SqliteStore, "save_completion", crashing)

    body = reconcile_body(parent, evidence["record"])
    with pytest.raises(Crash):
        service.reconcile_mission(parent["mission_id"], body=body, idempotency_key="crash-1")
    res = service.reconcile_mission(parent["mission_id"], body=body, idempotency_key="crash-2")
    assert res["state_after"] == "COMPLETED"
    assert res["continuation"]["scheduled"] is True
    mid = parent["mission_id"]
    completion = service.runtime.store.get_completion(mid)
    assert completion is not None and completion.completion_hash == res["completion"]["completion_hash"]
    assert service.runtime.outcome_record(completion.outcome_ref, mid) == res["outcome"]
    assert len(service.runtime.store.list_receipts(mid)) == 1
    assert service.runtime.store.verify_mission_chain(mid)["ok"] is True
    service.close()


def test_the_continuation_key_namespace_is_reserved_and_lineage_checked(tmp_path):
    service = queued_service(tmp_path, "squat")
    parent = _parent_with_continuation(service)
    key = parent["continuation"]["creation_key"]
    with pytest.raises(ApiError) as caught:
        service.create_mission(objective="Read-only research plan.", work_spec=work_spec(),
                               idempotency_key=key)
    assert caught.value.code == "IDEMPOTENCY_KEY_RESERVED"
    # Even a mission that reached the key some other way is not taken for the child.
    squatter = service.runtime.create_mission_from_text("Read-only research plan.",
                                                        creation_idempotency_key=key, work_spec=work_spec())
    assert service.get_mission(parent["mission_id"])["envelope"]["continuation"]["child_mission_id"] is None
    evidence = to_reconciling(service, parent)
    res = service.reconcile_mission(parent["mission_id"], body=reconcile_body(parent, evidence["record"]),
                                    idempotency_key="squat-rec")
    assert res["state_after"] == "COMPLETED"
    assert res["continuation"] == {"scheduled": False, "code": "CONTINUATION_KEY_SQUATTED",
                                   "message": res["continuation"]["message"],
                                   "details": {"creation_key": key, "mission_id": squatter.mission_id}}
    service.close()


# ------------------------------------------------------------------- HTTP --
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_reconcile_route_over_loopback_http(tmp_path):
    pytest.importorskip("fastapi")
    uvicorn = pytest.importorskip("uvicorn")
    from recipe_engine.founder_runtime.api.auth import OperatorAuth, generate_token
    from recipe_engine.founder_runtime.api.client import ApiClientError, FounderRuntimeClient
    from recipe_engine.founder_runtime.api.openapi import generate
    from recipe_engine.founder_runtime.api.server import build_app

    assert "post" in generate()["paths"]["/v1/missions/{mission_id}/reconcile"]
    token = generate_token()
    os.environ["FR_API_OPERATOR_TOKEN"] = token
    service = MissionService(str(tmp_path / "http.sqlite"), governance=_Governance(), attested_source_sha=SHA)
    server = uvicorn.Server(uvicorn.Config(build_app(service, OperatorAuth(), probe_live=False),
                                           host="127.0.0.1", port=(port := _free_port()), log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    client = FounderRuntimeClient(token, port=port)
    for _ in range(100):
        try:
            client.health()
            break
        except Exception:
            time.sleep(0.05)
    try:
        created = client.create_mission("Read-only public-record research plan for the supplied address.",
                                        work_spec=work_spec(), continuation=continuation_request(),
                                        idempotency_key="http-parent")
        env = created["envelope"]
        evidence = to_reconciling(service, env)
        with pytest.raises(ApiClientError) as caught:
            client._req("POST", f"/v1/missions/{env['mission_id']}/reconcile",
                        body=reconcile_body(env, evidence["record"]))
        assert caught.value.status == 428
        res = client.reconcile(env["mission_id"], reconcile_body(env, evidence["record"]))
        assert res["state_after"] == "COMPLETED" and res["continuation"]["scheduled"] is True
        listed = {m["mission_id"]: m for m in client.list_missions()["missions"]}
        child = listed[res["continuation"]["child_mission_id"]]
        assert child["lineage"]["parent_mission_id"] == env["mission_id"]
        assert child["lineage"]["parent_completion_hash"] == res["completion"]["completion_hash"]
        assert listed[env["mission_id"]]["completion"]["outcome_ref"] == res["outcome"]["outcome_id"]
        replay = client.reconcile(env["mission_id"], reconcile_body(env, evidence["record"]))
        assert replay["idempotent_replay"] is True
    finally:
        server.should_exit = True
        time.sleep(0.1)
        service.close()
