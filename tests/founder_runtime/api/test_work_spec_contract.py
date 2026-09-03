"""Authority-bound Founder Runtime work specification contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from recipe_engine.founder_runtime.api.openapi import generate
from recipe_engine.founder_runtime.api.service import ApiError, MissionService
from recipe_engine.founder_runtime.contracts.approval import (
    SCHEMA_APPROVAL,
    Approval,
    ApprovalDecision,
)
from recipe_engine.founder_runtime.contracts.work_spec import (
    SCHEMA_WORK_SPEC,
    WorkSpec,
)
from recipe_engine.founder_runtime.errors import AuthorityDenied
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
    assert result["envelope"]["authority"]["work_spec_execution_authorized"] is True
    assert result["envelope"]["authority"]["execution_authorized"] is True
    assert result["envelope"]["authority"]["dispatch_approval_id"] == approval.approval_id
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
    default_root = Path(__file__).resolve().parents[3].parent / "neuruh-workers"
    workers_root = Path(os.environ.get("NEURUH_WORKERS_ROOT", default_root))
    if not (workers_root / "neuruh_workers" / "founder_runtime.py").is_file():
        pytest.skip("canonical neuruh-workers checkout is unavailable")
    script = """
import json
import sys

from neuruh_workers.founder_runtime import (
    DispatchTicket,
    IntelligenceLayer,
    WorkCandidate,
    compile_context,
    map_founder_runtime_dispatch,
)
from neuruh_workers.workflows import build_catalog

envelope = json.load(sys.stdin)
catalog = build_catalog()
ticket, candidate = map_founder_runtime_dispatch(envelope, catalog=catalog)
assert isinstance(ticket, DispatchTicket)
assert isinstance(candidate, WorkCandidate)
assert candidate.layer is IntelligenceLayer.L0
definition = catalog.get(candidate.workflow_name)
context = compile_context(ticket, candidate)
assert definition.version == "1.0.0"
assert definition.read_only is True
assert candidate.params == {
    "address": "123 Main St, Benton Harbor, MI",
    "county": "Berrien",
}
assert context["candidate"]["layer"] == "L0_DETERMINISTIC"
assert context["fr_state"] == "QUEUED"
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(workers_root), env.get("PYTHONPATH", "")],
    ).rstrip(os.pathsep)
    subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(envelope),
        text=True,
        env=env,
        check=True,
    )
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


@pytest.mark.parametrize("bound_hash", [None, "0" * 64])
def test_26_kernel_rejects_unbound_or_mismatched_work_spec_approval(tmp_path, bound_hash):
    service = _service(tmp_path, "kernel-binding", auto_resolve=False)
    result = _create(service)
    mission_id = result["envelope"]["mission_id"]
    mission = service.runtime.get_mission(mission_id)
    evidence_refs = []
    if bound_hash is not None:
        evidence_refs.append(resolver.WORK_SPEC_SHA256_EVIDENCE_PREFIX + bound_hash)
    approval = Approval(
        schema_version=SCHEMA_APPROVAL,
        approval_id=f"appr_kernel_{bound_hash or 'missing'}",
        mission_id=mission_id,
        action_class=mission.authority_requirements[0].authority_class.value,
        exact_requested_action=GATE_ACTION,
        scope=GATE_SCOPE,
        requested_by="court",
        decided_by="governance:kernel-binding-court",
        decision=ApprovalDecision.ALLOW,
        conditions=[],
        expiry=None,
        evidence_refs=evidence_refs,
        created_at=service.clock.now_iso(),
        decided_at=service.clock.now_iso(),
        idempotency_key=f"kernel-binding:{bound_hash or 'missing'}",
    )
    service.runtime.record_approval(approval)
    service.close()
    service = _service(tmp_path, "kernel-binding", auto_resolve=False)
    with pytest.raises(AuthorityDenied):
        service.runtime.transition(
            mission_id,
            MissionState.QUEUED,
            reason="kernel_binding_court",
            approval_id=approval.approval_id,
        )
    assert service.runtime.get_mission(mission_id).current_state == MissionState.AWAITING_APPROVAL
    assert service.runtime.store.get_approval(approval.approval_id).decision == ApprovalDecision.ALLOW
    service.close()


def test_27_plan_approval_is_not_advertised_as_dispatch_authority(tmp_path):
    service = _service(tmp_path, "plan-authority", auto_resolve=False)
    created = _create(service)
    approved = service.approve_plan(
        created["envelope"]["mission_id"],
        reason="plan only",
        idempotency_key="plan-only",
        expected_version=created["envelope"]["version"],
    )
    assert approved["envelope"]["state"] == "APPROVED_NO_EXECUTION"
    assert approved["envelope"]["authority"]["state"] == "PLAN_APPROVED_NO_EXECUTION"
    assert approved["envelope"]["authority"]["work_spec_authorized"] is False
    assert approved["envelope"]["authority"]["work_spec_execution_authorized"] is False
    assert approved["envelope"]["authority"]["execution_authorized"] is False
    assert approved["envelope"]["authority"]["dispatch_approval_id"] is None
    service.close()


def test_28_creation_retry_survives_api_idempotency_cache_loss(tmp_path):
    service = _service(tmp_path, "cache-loss")
    first = _create(service, key="cache-loss")
    mission_id = first["envelope"]["mission_id"]
    version = first["envelope"]["version"]
    candidate_id = first["envelope"]["work_spec"]["candidate_id"]
    event_count = len(service.runtime.store.list_events(mission_id))
    approval_count = len(service.runtime.store.list_approvals(mission_id))
    immediate = _create(service, key="cache-loss")
    assert immediate["idempotent_replay"] is True
    assert immediate["envelope"]["mission_id"] == mission_id
    assert immediate["envelope"]["work_spec"]["candidate_id"] == candidate_id
    service._idem.execute("DELETE FROM idempotency WHERE key=?", ("cache-loss",))
    service.close()

    reopened = _service(tmp_path, "cache-loss")
    replay = _create(reopened, key="cache-loss")
    assert replay["idempotent_replay"] is True
    assert replay["envelope"]["mission_id"] == mission_id
    assert replay["envelope"]["version"] == version
    assert replay["envelope"]["work_spec"]["candidate_id"] == candidate_id
    assert len(reopened.runtime.store.list_events(mission_id)) == event_count
    assert len(reopened.runtime.store.list_approvals(mission_id)) == approval_count
    reopened.close()


@pytest.mark.parametrize(
    "params",
    [
        {"confidence": 1.5},
        {"nested": {"values": [1, True, None, {"confidence": 1.5}]}},
    ],
)
def test_29_float_params_fail_closed_before_persistence(tmp_path, params):
    service = _service(tmp_path, "float-params")
    with pytest.raises(ApiError) as caught:
        _create(service, spec=_spec(params=params))
    assert caught.value.code == "WORK_SPEC_INVALID"
    assert service.list_missions()["count"] == 0
    service.close()


def test_30_list_and_get_openapi_responses_publish_dispatch_contract():
    spec = generate()
    components = spec["components"]["schemas"]
    assert "MissionEnvelopeBody" in components
    envelope_properties = components["MissionEnvelopeBody"]["properties"]
    for field in (
        "api_version",
        "mission_id",
        "state",
        "version",
        "objective",
        "work_spec",
        "work_spec_sha256",
        "authority",
    ):
        assert field in envelope_properties
    authority_ref = envelope_properties["authority"]["$ref"].rsplit("/", 1)[-1]
    authority_properties = components[authority_ref]["properties"]
    for field in (
        "state",
        "work_spec_sha256",
        "work_spec_execution_authorized",
        "execution_authorized",
        "dispatch_approval_id",
    ):
        assert field in authority_properties
    for path in ("/v1/missions", "/v1/missions/{mission_id}"):
        operation = spec["paths"][path]["get"]
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert "$ref" in schema


@pytest.mark.parametrize(
    "changed_spec",
    [
        _spec(workflow_name="field-pin-enrichment"),
        _spec(params={"address": "999 Other St"}),
        _spec(private_ip_risk=True),
    ],
)
def test_31_changed_spec_conflicts_after_api_cache_loss(tmp_path, changed_spec):
    service = _service(tmp_path, "changed-after-cache-loss")
    first = _create(service, key="changed-after-cache-loss")
    mission_id = first["envelope"]["mission_id"]
    service._idem.execute(
        "DELETE FROM idempotency WHERE key=?",
        ("changed-after-cache-loss",),
    )
    service.close()

    reopened = _service(tmp_path, "changed-after-cache-loss")
    with pytest.raises(ApiError) as caught:
        _create(
            reopened,
            key="changed-after-cache-loss",
            spec=changed_spec,
        )
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert reopened.get_mission(mission_id)["envelope"]["mission_id"] == mission_id
    assert reopened.list_missions()["count"] == 1
    reopened.close()


def test_32_execution_authority_requires_queued_state(tmp_path):
    service = _service(tmp_path, "dispatch-state")
    created = _create(service)
    mission_id = created["envelope"]["mission_id"]
    queued_version = created["envelope"]["version"]
    paused = service.pause(
        mission_id,
        idempotency_key="pause-after-authority",
        expected_version=queued_version,
    )
    authority = paused["envelope"]["authority"]
    assert paused["envelope"]["state"] == "PAUSED"
    assert authority["state"] == "EXECUTION_APPROVED_NOT_DISPATCHABLE"
    assert authority["work_spec_execution_authorized"] is False
    assert authority["execution_authorized"] is False
    assert authority["dispatch_approval_id"] is None
    service.close()


def test_33_tampered_consumed_approval_cannot_project_execution_authority(tmp_path):
    service = _service(tmp_path, "tampered-approval")
    created = _create(service)
    mission_id = created["envelope"]["mission_id"]
    approval = service.runtime.store.list_approvals(mission_id)[-1]
    row = service.runtime.store._c().execute(
        "SELECT body_json FROM approvals WHERE approval_id=?",
        (approval.approval_id,),
    ).fetchone()
    body = json.loads(row["body_json"])
    body["conditions"].append("tampered-after-consumption")
    service.runtime.store._c().execute(
        "UPDATE approvals SET body_json=? WHERE approval_id=?",
        (json.dumps(body, sort_keys=True), approval.approval_id),
    )

    authority = service.get_mission(mission_id)["envelope"]["authority"]
    assert authority["state"] == "PENDING"
    assert authority["execution_authorized"] is False
    assert authority["work_spec_execution_authorized"] is False
    assert authority["dispatch_approval_id"] is None
    service.close()
