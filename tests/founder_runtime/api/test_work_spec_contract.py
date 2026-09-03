"""Authority-bound Founder Runtime work specification contract."""

from __future__ import annotations

import json

import pytest

from recipe_engine.founder_runtime.api.service import ApiError, MissionService
from recipe_engine.founder_runtime.contracts.approval import ApprovalDecision
from recipe_engine.founder_runtime.contracts.work_spec import (
    SCHEMA_WORK_SPEC,
    WorkSpec,
)
from recipe_engine.founder_runtime.runtime import approval_resolver as resolver
from recipe_engine.founder_runtime.runtime.approvals import gated_action_for
from recipe_engine.founder_runtime.state.states import MissionState
from tests.founder_runtime.api.test_approval_resolver import SHA, _Governance


GATE_ACTION, GATE_SCOPE = gated_action_for(
    MissionState.AWAITING_APPROVAL,
    MissionState.QUEUED,
)


def _spec(**updates):
    value = {
        "schema": SCHEMA_WORK_SPEC,
        "workflow_name": "property-research",
        "params": {"address": "123 Main St, Benton Harbor, MI", "county": "Berrien"},
        "intelligence_layer": "L0_DETERMINISTIC",
        "evidence_refs": ["workflow:property-research@1.0.0"],
        "production_mutation": False,
        "external_message": False,
        "money_move": False,
        "irreversible": False,
        "private_ip_risk": False,
        "code_custody_required": False,
    }
    value.update(updates)
    return value


def _service(tmp_path, name="work-spec", **updates):
    options = {
        "governance": _Governance(),
        "attested_source_sha": SHA,
    }
    options.update(updates)
    return MissionService(str(tmp_path / f"{name}.sqlite"), **options)


def _create(service, *, key="work-spec-create", spec=None):
    return service.create_mission(
        objective="Read-only public-record research plan for the supplied address.",
        work_spec=_spec() if spec is None else spec,
        idempotency_key=key,
    )


def test_01_legacy_create_without_work_spec_still_works(service):
    result = service.create_mission(objective="status check", idempotency_key="legacy")
    assert result["envelope"]["work_spec"] is None
    assert result["envelope"]["work_spec_sha256"] is None


def test_02_no_spec_mission_never_gets_a_default_workflow(service):
    result = service.create_mission(objective="status check", idempotency_key="no-default")
    mission = service.get_mission(result["envelope"]["mission_id"])["mission"]
    assert mission["work_spec"] is None
    assert "workflow_name" not in mission


def test_03_valid_v1_spec_persists_exact_authoritative_values(tmp_path):
    service = _service(tmp_path)
    result = _create(service)
    stored = service.get_mission(result["envelope"]["mission_id"])["mission"]["work_spec"]
    assert stored["schema"] == SCHEMA_WORK_SPEC
    assert stored["workflow_name"] == "property-research"
    assert stored["params"] == _spec()["params"]
    assert stored["intelligence_layer"] == "L0_DETERMINISTIC"
    service.close()


def test_04_spec_survives_service_reconstruction(tmp_path):
    service = _service(tmp_path, "reconstruct")
    result = _create(service)
    mission_id = result["envelope"]["mission_id"]
    before = service.get_mission(mission_id)
    service.close()
    reopened = _service(tmp_path, "reconstruct", auto_resolve=False)
    after = reopened.get_mission(mission_id)
    assert after["mission"]["work_spec"] == before["mission"]["work_spec"]
    assert after["mission"]["work_spec_sha256"] == before["mission"]["work_spec_sha256"]
    reopened.close()


def test_05_work_spec_hash_is_deterministic_for_canonical_material():
    first = _spec()
    second = {key: first[key] for key in reversed(tuple(first))}
    assert WorkSpec.create("msn_fixed", first).sha256() == WorkSpec.create("msn_fixed", second).sha256()


def test_06_exact_spec_appears_in_dispatchable_list_representation(tmp_path):
    service = _service(tmp_path, "listed")
    result = _create(service)
    mission_id = result["envelope"]["mission_id"]
    listed = next(item for item in service.list_missions()["missions"] if item["mission_id"] == mission_id)
    assert listed["state"] == "QUEUED"
    assert listed["work_spec"] == service.get_mission(mission_id)["mission"]["work_spec"]
    assert listed["work_spec_sha256"] == listed["authority"]["work_spec_sha256"]
    service.close()


def test_07_objective_appears_in_dispatchable_list_representation(tmp_path):
    service = _service(tmp_path, "objective")
    result = _create(service)
    listed = service.list_missions()["missions"][0]
    assert listed["objective"] == "Read-only public-record research plan for the supplied address."
    assert listed["mission_id"] == result["envelope"]["mission_id"]
    service.close()


def test_08_governance_authority_receipt_binds_work_spec_hash(tmp_path):
    governance = _Governance()
    service = _service(tmp_path, "authority", governance=governance)
    result = _create(service)
    mission_id = result["envelope"]["mission_id"]
    work_spec_sha256 = result["envelope"]["work_spec_sha256"]
    approval = service.runtime.store.list_approvals(mission_id)[-1]
    assert result["envelope"]["authority"]["work_spec_authorized"] is True
    assert resolver.bound_work_spec_sha256(approval) == work_spec_sha256
    assert governance.calls[-1]["facts"]["precondition_hash"] == work_spec_sha256
    service.close()


def test_09_post_approval_workflow_change_is_refused(tmp_path):
    service = _service(tmp_path, "workflow-change")
    result = _create(service, key="immutable")
    with pytest.raises(ApiError) as caught:
        _create(service, key="immutable", spec=_spec(workflow_name="cockpit-health-snapshot"))
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert service.get_mission(result["envelope"]["mission_id"])["envelope"]["state"] == "QUEUED"
    service.close()


def test_10_post_approval_param_change_is_refused(tmp_path):
    service = _service(tmp_path, "param-change")
    result = _create(service, key="immutable")
    with pytest.raises(ApiError) as caught:
        _create(service, key="immutable", spec=_spec(params={"address": "999 Other St"}))
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    stored = service.get_mission(result["envelope"]["mission_id"])["envelope"]["work_spec"]
    assert stored["params"]["address"].startswith("123 Main")
    service.close()


def test_11_unknown_work_spec_schema_fails_closed(tmp_path):
    service = _service(tmp_path, "schema")
    with pytest.raises(ApiError) as caught:
        _create(service, spec=_spec(schema="fr.work_spec.v2"))
    assert caught.value.code == "WORK_SPEC_INVALID"
    assert service.list_missions()["count"] == 0
    service.close()


def test_12_unsupported_intelligence_layer_fails_closed(tmp_path):
    service = _service(tmp_path, "layer")
    with pytest.raises(ApiError) as caught:
        _create(service, spec=_spec(intelligence_layer="L5_UNKNOWN"))
    assert caught.value.code == "WORK_SPEC_INVALID"
    service.close()


def test_13_malformed_params_fail_closed(tmp_path):
    service = _service(tmp_path, "params")
    with pytest.raises(ApiError) as caught:
        _create(service, spec=_spec(params=["not", "an", "object"]))
    assert caught.value.code == "WORK_SPEC_INVALID"
    service.close()


def test_14_malformed_consequence_flags_fail_closed(tmp_path):
    service = _service(tmp_path, "flags")
    with pytest.raises(ApiError) as caught:
        _create(service, spec=_spec(money_move=1))
    assert caught.value.code == "WORK_SPEC_INVALID"
    service.close()


def test_15_tampered_spec_hash_fails_closed(tmp_path):
    service = _service(tmp_path, "tamper")
    result = _create(service)
    mission_id = result["envelope"]["mission_id"]
    connection = service.runtime.store._c()
    row = connection.execute(
        "SELECT body_json FROM missions WHERE mission_id=?",
        (mission_id,),
    ).fetchone()
    body = json.loads(row["body_json"])
    body["work_spec"]["params"]["address"] = "tampered"
    connection.execute(
        "UPDATE missions SET body_json=? WHERE mission_id=?",
        (json.dumps(body, sort_keys=True), mission_id),
    )
    with pytest.raises(ApiError) as caught:
        service.get_mission(mission_id)
    assert caught.value.code == "INTEGRITY_FAILURE"
    service.close()


def test_16_old_authority_replay_cannot_authorize_changed_spec(tmp_path):
    seed_governance = _Governance()
    failing_governance = _Governance(adapter_failure=True)
    service = _service(
        tmp_path,
        "authority-replay",
        governance=failing_governance,
        auto_resolve=False,
    )
    result = _create(service)
    mission_id = result["envelope"]["mission_id"]
    mission = service.runtime.get_mission(mission_id)
    requirement = mission.authority_requirements[0]
    now = service.clock.now_iso()
    facts = resolver.resolver_facts(
        mission_id,
        requirement,
        action=GATE_ACTION,
        scope=GATE_SCOPE,
        source_sha=SHA,
        nonce="rn_" + "a" * 29,
        issued_at=now,
        work_spec_sha256=mission.work_spec_sha256,
    )
    envelope = seed_governance.authorize(
        facts,
        founder_approval=None,
        meta=None,
    ).payload
    approval = resolver.governance_decided_approval(
        envelope,
        mission_id=mission_id,
        requirement=requirement,
        action=GATE_ACTION,
        scope=GATE_SCOPE,
        now_iso=now,
        source_sha=SHA,
        work_spec_sha256=mission.work_spec_sha256,
    )
    service.runtime.record_approval(approval)
    changed = service.runtime.get_mission(mission_id)
    changed.work_spec = WorkSpec.create(mission_id, _spec(params={"address": "different"}))
    changed.work_spec_sha256 = changed.work_spec.sha256()
    outcome = service._resolve_with_governance(
        changed,
        requirement,
        action=GATE_ACTION,
        scope=GATE_SCOPE,
    )
    assert outcome["outcome"] == resolver.UNRESOLVED
    assert outcome["reason_code"].startswith("GOVERNANCE_ADAPTER_")
    assert len(failing_governance.calls) == 1
    assert service.runtime.store.get_approval(approval.approval_id).decision == ApprovalDecision.ALLOW
    service.close()


def test_17_no_spec_queued_mission_remains_eligible_and_unconsumed(tmp_path):
    service = _service(tmp_path, "no-spec")
    result = service.create_mission(objective="status check", idempotency_key="no-spec")
    before = result["envelope"]
    after = service.list_missions()["missions"][0]
    assert before["state"] == after["state"] == "QUEUED"
    assert before["version"] == after["version"]
    assert after["work_spec"] is None
    assert after["authority"]["state"] == "APPROVED"
    service.close()


def test_18_representation_constructs_one_workers_candidate_with_zero_inference(tmp_path):
    service = _service(tmp_path, "candidate")
    envelope = _create(service)["envelope"]
    work_spec = envelope["work_spec"]
    candidate = {
        "candidate_id": work_spec["candidate_id"],
        "workflow_name": work_spec["workflow_name"],
        "params": work_spec["params"],
        "layer": work_spec["intelligence_layer"],
        "evidence_refs": tuple(work_spec["evidence_refs"]),
        "production_mutation": work_spec["production_mutation"],
        "external_message": work_spec["external_message"],
        "money_move": work_spec["money_move"],
        "irreversible": work_spec["irreversible"],
        "private_ip_risk": work_spec["private_ip_risk"],
    }
    ticket = {
        "mission_id": envelope["mission_id"],
        "objective": envelope["objective"],
        "fr_state": envelope["state"],
        "fr_api_version": envelope["api_version"],
        "code_custody_required": work_spec["code_custody_required"],
    }
    assert candidate["workflow_name"] == "property-research"
    assert candidate["params"] == _spec()["params"]
    assert ticket["fr_state"] == "QUEUED"
    assert len([candidate]) == 1
    service.close()


def test_19_restart_preserves_mission_version_authority_spec_and_hash(tmp_path):
    service = _service(tmp_path, "restart")
    result = _create(service)
    mission_id = result["envelope"]["mission_id"]
    before = service.get_mission(mission_id)["envelope"]
    service.close()
    reopened = _service(tmp_path, "restart", auto_resolve=False)
    after = reopened.get_mission(mission_id)["envelope"]
    for key in ("mission_id", "version", "state", "work_spec", "work_spec_sha256"):
        assert after[key] == before[key]
    assert after["authority"]["state"] == before["authority"]["state"] == "APPROVED"
    assert after["authority"]["work_spec_authorized"] is True
    reopened.close()


def test_20_contract_court_authorizes_no_outbound_message(tmp_path):
    service = _service(tmp_path, "no-message")
    envelope = _create(service)["envelope"]
    assert envelope["work_spec"]["external_message"] is False
    assert envelope["authority"]["external_action_authorized"] is False
    service.close()


def test_21_contract_court_authorizes_no_money_movement(tmp_path):
    service = _service(tmp_path, "no-money")
    envelope = _create(service)["envelope"]
    assert envelope["work_spec"]["money_move"] is False
    assert all("spend_usd:0" not in ref for ref in envelope["evidence_refs"])
    service.close()


def test_22_contract_court_authorizes_no_production_action(tmp_path):
    service = _service(tmp_path, "no-production")
    envelope = _create(service)["envelope"]
    assert envelope["work_spec"]["production_mutation"] is False
    assert envelope["authority"]["production_persistence_authorized"] is False
    service.close()


def test_23_repo_contract_court_never_executes_workers(tmp_path):
    service = _service(tmp_path, "no-workers")
    result = _create(service)
    mission_id = result["envelope"]["mission_id"]
    assert result["envelope"]["authority"]["executed"] is False
    assert "workers_dispatch" in result["envelope"]["blocked_actions"]
    assert service.runtime.store.list_receipts(mission_id) == []
    service.close()


def test_24_http_create_accepts_the_optional_work_spec(running_server):
    client, _port = running_server
    result = client.create_mission(
        "Read-only public-record research plan for the supplied address.",
        work_spec=_spec(),
        idempotency_key="http-work-spec",
    )
    assert result["envelope"]["work_spec"]["schema"] == SCHEMA_WORK_SPEC
    assert result["envelope"]["work_spec_sha256"]


def test_25_restart_with_tampered_persisted_material_fails_closed(tmp_path):
    service = _service(tmp_path, "restart-tamper")
    mission_id = _create(service)["envelope"]["mission_id"]
    connection = service.runtime.store._c()
    row = connection.execute(
        "SELECT body_json FROM missions WHERE mission_id=?",
        (mission_id,),
    ).fetchone()
    body = json.loads(row["body_json"])
    body["work_spec_sha256"] = "0" * 64
    connection.execute(
        "UPDATE missions SET body_json=? WHERE mission_id=?",
        (json.dumps(body, sort_keys=True), mission_id),
    )
    service.close()
    reopened = _service(tmp_path, "restart-tamper", auto_resolve=False)
    with pytest.raises(ApiError) as caught:
        reopened.get_mission(mission_id)
    assert caught.value.code == "INTEGRITY_FAILURE"
    reopened.close()
