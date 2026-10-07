"""Governed completion of an executed mission (fr.reconcile_request.v1) — pure, fail closed.

Closes the RECONCILING seam. The Founder Runtime learns, from sealed evidence,
that a dispatched work spec executed (SUCCEEDED: the sealed dispatch receipt)
and was independently judged (COURT_PASSED: the Sentinel court id and the
content sha of its sealed record). By design neither is completion, and nothing
turned them into completion: ``complete_mission`` needs a validated fr.receipt.v1,
a verified outcome record and a canonical completion report, and no route built
them — so every executed mission stopped at RECONCILING.

This module is the evidence contract for that last transition. It judges
nothing (the court judged); it binds:

* ``validate_reconcile_request`` — shape only.
* ``execution_facts`` — what the mission's own chain already holds: the accepted
  SUCCEEDED receipt (hash recomputed again) and the accepted COURT_PASSED record.
* ``verify_court_record`` — the FULL sealed ``independent_sentinel_court`` record
  the court outcome named. Its workers content sha must be the record_sha the
  runtime accepted, and every identity it judged — mission, execution job,
  receipt, authority, candidate source — must be this mission's. A record that
  bound less than the facts an execution PASS rests on is not enough.
* ``build_execution_receipt`` / ``build_acceptance`` / ``build_outcome_record`` —
  the live_observed fr.receipt.v1, acceptance results and verified outcome,
  derived deterministically (no clock) so a retried reconcile rebuilds the same
  evidence byte for byte.
* continuation plans and lineage — what a completed mission declared it wants
  next, and the parent evidence its continuation child carries.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from recipe_engine.founder_runtime.contracts.completion import AcceptanceResult
from recipe_engine.founder_runtime.contracts.mission import Mission
from recipe_engine.founder_runtime.contracts.receipts import (
    SCHEMA_RECEIPT, ExecutionReceipt, ReceiptStatus, TestResult,
)
from recipe_engine.founder_runtime.contracts.work_spec import validate_work_spec_request
from recipe_engine.founder_runtime.errors import (
    MissionSchemaInvalid, ReconcileMalformed, ReconcileRejected,
)
from recipe_engine.founder_runtime.evidence.execution_outcome import (
    COURT_PASSED,
    OUTCOME_COURT_PASSED,
    OUTCOME_SUCCEEDED,
    contains_secret_shape,
    dispatch_receipt_sha,
    parse_authority_reference,
    workers_canonical_json,
)
from recipe_engine.founder_runtime.ids import hash_payload, stable_id
from recipe_engine.founder_runtime.intent.deliverables import (
    EXECUTION_ACCEPTANCE_CRITERIA, EXECUTION_DELIVERABLE_ID,
)

SCHEMA_RECONCILE_REQUEST = "fr.reconcile_request.v1"
SCHEMA_EXECUTION_EVIDENCE = "fr.execution_evidence.v1"
SCHEMA_OUTCOME = "fr.outcome.v1"
SCHEMA_CONTINUATION_PLAN = "fr.continuation_plan.v1"
SCHEMA_LINEAGE = "fr.lineage.v1"
API_VERSION = "fr-api-v1"

INDEPENDENT_COURT_KIND = "independent_sentinel_court"
COURT_SOURCE_ROLE = "SENTINEL"
EXECUTION_STEP_ID = "workers_execution"
EXECUTION_EXECUTOR = "neuruh-workers/fr-to-workers-dispatch-bridge"
#: What an execution PASS must rest on: the receipt it judged, and the three
#: facts the organ requires of every Founder Runtime execution
#: (sentinel_court.verify FR_REQUIRED_FACTS). Named by the record's own
#: evidence_items[*].bound, never by the caller.
REQUIRED_COURT_BINDINGS = frozenset({
    "receipt_sha", "execution_job", "job_succeeded_sha", "authority_record",
})
VERIFIED_RELATIONS = frozenset({
    "candidate_is_canonical_base", "candidate_descends_from_canonical_base",
})
#: A continuation chain is finite by construction.
MAX_CONTINUATION_DEPTH = 8
CONSEQUENCE_FLAGS = (
    "production_mutation", "external_message", "money_move", "irreversible", "private_ip_risk",
)

_REQUEST_REQUIRED = frozenset({"schema", "api_version", "mission_id", "execution_id", "court_record"})
_REQUEST_OPTIONAL = frozenset({"custody"})
_CONTINUATION_REQUIRED = frozenset({"objective", "work_spec"})
_CONTINUATION_OPTIONAL = frozenset({"done_criteria", "continuation"})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEAD40 = re.compile(r"^[0-9a-f]{40}$")
_UNSAFE_CAPABILITY_TOKENS = ("write", "delete", "deploy", "send", "shell")


def workers_content_sha(value: Any) -> str:
    """neuruh_workers.contracts.content_sha — how the board sealed record_sha."""
    return hashlib.sha256(workers_canonical_json(value).encode("utf-8")).hexdigest()


def _malformed(message: str, **details: Any) -> ReconcileMalformed:
    return ReconcileMalformed(message, details)


def _rejected(reason_code: str, message: str, **details: Any) -> ReconcileRejected:
    return ReconcileRejected(reason_code, message, details)


def _iso(value: Any) -> bool:
    return _parse_iso(value) is not None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _utc_ms(value: datetime) -> str:
    """One timestamp form for derived receipts, so ordering is a string compare."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


# ------------------------------------------------------------------ request --

def validate_reconcile_request(raw: Any) -> dict[str, Any]:
    """Shape only. Returns a normalised copy; raises ReconcileMalformed."""
    if not isinstance(raw, Mapping):
        raise _malformed("reconcile request must be an object", path="$")
    keys = set(raw)
    missing = sorted(_REQUEST_REQUIRED - keys)
    unknown = sorted(keys - _REQUEST_REQUIRED - _REQUEST_OPTIONAL)
    if missing or unknown:
        raise _malformed("reconcile request fields are incomplete or extended",
                         path="$", missing=missing, unknown=unknown)
    if raw["schema"] != SCHEMA_RECONCILE_REQUEST:
        raise _malformed("unsupported reconcile schema", path="$.schema", actual=str(raw["schema"])[:64])
    if raw["api_version"] != API_VERSION:
        raise _malformed("unsupported api_version", path="$.api_version")
    for field in ("mission_id", "execution_id"):
        value = raw[field]
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise _malformed("identifier is not in canonical form", path=f"$.{field}")
    record = raw["court_record"]
    if not isinstance(record, Mapping) or not record:
        raise _malformed("court_record must be the sealed court record object", path="$.court_record")
    try:
        text = workers_canonical_json(dict(record))
    except (TypeError, ValueError) as exc:
        raise _malformed("court_record is not canonical JSON", path="$.court_record") from exc
    if contains_secret_shape(text):
        raise _malformed("credential-shaped court record content refused", path="$.court_record")
    custody = raw.get("custody")
    if custody is not None and not isinstance(custody, Mapping):
        raise _malformed("custody must be an object when present", path="$.custody")
    return {
        "schema": SCHEMA_RECONCILE_REQUEST,
        "api_version": API_VERSION,
        "mission_id": raw["mission_id"],
        "execution_id": raw["execution_id"],
        "court_record": dict(record),
        "custody": dict(custody) if custody is not None else None,
    }


# ----------------------------------------------------------- chain evidence --

def execution_facts(mission: Mission, projection: Mapping[str, Any] | None,
                    outcomes: list[Mapping[str, Any]]) -> dict[str, Any]:
    """The execution evidence the mission's own chain holds, re-verified.

    ``outcomes`` is MissionRuntime.execution_outcomes(): every accepted
    fr.execution_outcome.v1 event in chain order.
    """
    if not isinstance(projection, Mapping):
        raise _rejected("EXECUTION_NOT_LEARNED", "mission holds no execution projection")
    if projection.get("phase") != OUTCOME_COURT_PASSED or projection.get("court") != COURT_PASSED:
        raise _rejected("COURT_NOT_PASSED", "completion requires an accepted independent court PASS",
                        phase=projection.get("phase"), court=projection.get("court"))
    if projection.get("verified") is not False:
        raise _rejected("PROJECTION_UNREADABLE", "execution projection claims a verification it cannot hold")
    execution_id = str(projection.get("execution_id") or "")
    succeeded = [o for o in outcomes
                 if o.get("outcome") == OUTCOME_SUCCEEDED and not o.get("implied")]
    court = [o for o in outcomes
             if o.get("outcome") == OUTCOME_COURT_PASSED and not o.get("implied")]
    if len(succeeded) != 1 or len(court) != 1:
        raise _rejected("EXECUTION_EVIDENCE_AMBIGUOUS",
                        "completion needs exactly one accepted SUCCEEDED and one COURT_PASSED",
                        succeeded=len(succeeded), court_passed=len(court))
    ok = dict(succeeded[0]["execution_outcome"])
    passed = dict(court[0]["execution_outcome"])
    receipt = dict(ok.get("receipt") or {})
    fields = dict(receipt.get("fields") or {})
    receipt_sha = str(receipt.get("receipt_sha") or "")
    if not fields or dispatch_receipt_sha(fields) != receipt_sha or fields.get("PAYLOAD_HASH") != receipt_sha:
        raise _rejected("RECEIPT_HASH_MISMATCH", "stored dispatch receipt no longer reproduces its seal")
    if receipt_sha != projection.get("receipt_sha"):
        raise _rejected("RECEIPT_BINDING_MISMATCH", "projection names a different receipt")
    court_body = dict(passed.get("court") or {})
    if (
        court_body.get("receipt_sha") != receipt_sha
        or court_body.get("court_id") != projection.get("court_id")
        or court_body.get("record_sha") != projection.get("court_record_sha")
    ):
        raise _rejected("COURT_BINDING_MISMATCH", "accepted court outcome disagrees with the projection")
    for key, expected in (("MISSION_ID", mission.mission_id), ("TASK_ID", execution_id),
                          ("JOB_ID", execution_id), ("WORK_SPEC_SHA256", mission.work_spec_sha256)):
        if fields.get(key) != expected:
            raise _rejected("RECEIPT_BINDING_MISMATCH", "sealed receipt names a different execution",
                            field=key)
    observed = [parsed for parsed in (_parse_iso(o["execution_outcome"].get("observed_at")) for o in outcomes)
                if parsed is not None]
    if not observed:
        raise _rejected("EXECUTION_EVIDENCE_UNTIMED", "accepted outcomes carry no readable observed_at")
    return {
        "execution_id": execution_id,
        "dispatched_version": projection.get("dispatched_version"),
        "authority_reference": str(projection.get("authority_reference") or ""),
        "receipt_sha": receipt_sha,
        "receipt_ref": str(receipt.get("receipt_ref") or ""),
        "receipt_fields": fields,
        "court_id": str(projection.get("court_id") or ""),
        "court_record_sha": str(projection.get("court_record_sha") or ""),
        "code_custody_required": fields.get("CODE_CUSTODY_REQUIRED") == "YES",
        # First and last moments the chain observed this execution; the court's
        # own seal is folded in by build_execution_receipt.
        "started_at": _utc_ms(min(observed)),
        "observed_until": _utc_ms(max(observed)),
        "succeeded_at": str(ok.get("observed_at") or ""),
        "court_observed_at": str(passed.get("observed_at") or ""),
    }


def verify_court_record(record: Mapping[str, Any], *, mission: Mission,
                        facts: Mapping[str, Any]) -> dict[str, Any]:
    """Bind the full sealed Sentinel record to this mission's accepted evidence."""
    record_sha = workers_content_sha(dict(record))
    if record_sha != facts["court_record_sha"]:
        raise _rejected("COURT_RECORD_MISMATCH",
                        "court record is not the sealed record the runtime accepted",
                        expected=facts["court_record_sha"], actual=record_sha)
    if (
        record.get("kind") != INDEPENDENT_COURT_KIND
        or record.get("independent") is not True
        or record.get("source_role") != COURT_SOURCE_ROLE
    ):
        raise _rejected("COURT_NOT_INDEPENDENT", "record is not an independent Sentinel court")
    if record.get("verdict") != "PASS":
        raise _rejected("COURT_RECORD_NOT_PASS", "sealed record does not carry a PASS",
                        verdict=str(record.get("verdict"))[:16])
    for key in ("findings", "exceptions", "required_repairs"):
        value = record.get(key)
        if not isinstance(value, list) or value:
            raise _rejected("COURT_RECORD_NOT_CLEAN", "a PASS with findings, exceptions or repairs is not clean",
                            field=key)
    fields = facts["receipt_fields"]
    expected = {
        "court_id": facts["court_id"],
        "mission_id": mission.mission_id,
        "task_id": facts["execution_id"],
        "receipt_sha": facts["receipt_sha"],
        "authority_ref": facts["authority_reference"],
        "source_head": fields.get("SOURCE_HEAD"),
        "candidate_sha": fields.get("SOURCE_HEAD"),
        "source_repo": fields.get("REPO"),
    }
    for key, value in expected.items():
        if not value or record.get(key) != value:
            raise _rejected("COURT_RECORD_BINDING_MISMATCH",
                            "sealed record judged a different identity than this mission executed",
                            field=key)
    relation = record.get("base_relation")
    if relation not in VERIFIED_RELATIONS:
        raise _rejected("COURT_RELATION_UNVERIFIED", "PASS rests on an unverified base relation",
                        relation=str(relation)[:64])
    bound: set[str] = set()
    for item in record.get("evidence_items") or []:
        if isinstance(item, Mapping):
            bound.update(str(b) for b in (item.get("bound") or []) if isinstance(b, str))
    missing = sorted(REQUIRED_COURT_BINDINGS - bound)
    if missing:
        raise _rejected("COURT_BINDINGS_INCOMPLETE",
                        "the court did not bind every fact an execution PASS rests on", missing=missing)
    if not _iso(record.get("sealed_at")):
        raise _rejected("COURT_RECORD_UNSEALED", "record carries no sealed_at")
    iar = record.get("iar_provenance") if isinstance(record.get("iar_provenance"), Mapping) else {}
    return {
        "court_id": facts["court_id"],
        "record_sha": record_sha,
        "sealed_at": str(record["sealed_at"]),
        "candidate_sha": str(record["candidate_sha"]),
        "canonical_sha": str(record.get("canonical_sha") or ""),
        "base_relation": str(relation),
        "bindings": sorted(bound),
        "iar_authorization_ref": str(iar.get("authorization_ref") or ""),
    }


def require_consequence_free(mission: Mission) -> None:
    """The completion this module builds is for work that changed nothing outside itself."""
    spec = mission.work_spec
    if spec is None:
        raise _rejected("WORK_SPEC_ABSENT", "only an executable work-spec mission completes here")
    flags = {
        "production_mutation": spec.production_mutation,
        "external_message": spec.external_message,
        "money_move": spec.money_move,
        "irreversible": spec.irreversible,
        "private_ip_risk": spec.private_ip_risk,
    }
    raised = [flag for flag in CONSEQUENCE_FLAGS if flags[flag] is not False]
    if raised:
        raise _rejected("CONSEQUENTIAL_WORK_SPEC", "consequential work does not complete on execution evidence",
                        flags=raised)


def require_execution_deliverables(mission: Mission) -> None:
    required = sorted(d.deliverable_id for d in mission.deliverables if d.required)
    if required != [EXECUTION_DELIVERABLE_ID]:
        raise _rejected("DELIVERABLES_NOT_EXECUTION_BOUND",
                        "mission deliverables are not the executed work spec; execution evidence cannot deliver them",
                        required=required)


# ------------------------------------------------------------- derivations --

def execution_capability(mission: Mission) -> str:
    workflow = mission.work_spec.workflow_name if mission.work_spec is not None else ""
    capability = f"workers.workflow.{workflow}"
    if not workflow or any(token in capability.casefold() for token in _UNSAFE_CAPABILITY_TOKENS):
        raise _rejected("UNSAFE_CAPABILITY", "workflow name reads as a consequential capability",
                        workflow=workflow[:128])
    return capability


def build_execution_receipt(mission: Mission, *, facts: Mapping[str, Any],
                            court: Mapping[str, Any],
                            governance_authorization_id: str | None) -> ExecutionReceipt:
    """The fr.receipt.v1 (live_observed) the completion verifier requires.

    Derived only from the mission's accepted chain and the verified record, so a
    retry reproduces it exactly and record_receipt treats it as the same receipt.
    """
    if not mission.plan_id:
        raise _rejected("PLAN_UNBOUND", "mission has no bound plan id")
    capability = execution_capability(mission)
    fields = facts["receipt_fields"]
    approval_id, _ = parse_authority_reference(facts["authority_reference"])
    outcome_data = {
        "schema": SCHEMA_EXECUTION_EVIDENCE,
        "execution_id": facts["execution_id"],
        "dispatched_version": facts["dispatched_version"],
        "work_spec_sha256": mission.work_spec_sha256,
        "workflow_name": mission.work_spec.workflow_name,
        "receipt_sha": facts["receipt_sha"],
        "job_succeeded_sha": fields.get("JOB_SUCCEEDED_SHA"),
        "ledger_tail_sha": fields.get("LEDGER_TAIL_SHA"),
        "context_digest": fields.get("CONTEXT_DIGEST"),
        "source_repo": fields.get("REPO"),
        "source_head": fields.get("SOURCE_HEAD"),
        "dispatch_result": fields.get("DISPATCH_RESULT"),
        "court_id": court["court_id"],
        "court_record_sha": court["record_sha"],
        "court_verdict": "PASS",
        "court_sealed_at": court["sealed_at"],
        "court_base_relation": court["base_relation"],
        "court_bindings": list(court["bindings"]),
        "authority_reference": facts["authority_reference"],
        "dispatch_approval_id": approval_id,
        "governance_authorization_id": governance_authorization_id,
        "code_custody_required": bool(facts["code_custody_required"]),
    }
    output_hash = hash_payload(outcome_data)
    input_hash = str(mission.work_spec_sha256)
    receipt = ExecutionReceipt(
        schema_version=SCHEMA_RECEIPT,
        receipt_id=stable_id("rcpt_exec", mission.mission_id, facts["execution_id"], facts["receipt_sha"]),
        mission_id=mission.mission_id,
        plan_id=str(mission.plan_id),
        step_id=EXECUTION_STEP_ID,
        executor=EXECUTION_EXECUTOR,
        capability=capability,
        started_at=facts["started_at"],
        ended_at=max(facts["observed_until"], _utc_ms(_parse_iso(court["sealed_at"]))),
        status=ReceiptStatus.SUCCESS,
        input_hash=input_hash,
        output_hash=output_hash,
        repository_metadata={
            "repo": fields.get("REPO"), "head": fields.get("SOURCE_HEAD"),
            "branch": fields.get("BRANCH"), "worktree": fields.get("WORKTREE"),
            "mutation": False,
        },
        files_changed=[],
        tests=[TestResult(name="independent_sentinel_court", passed=True, detail=court["court_id"])],
        artifacts=[],
        error=None,
        authority_reference=facts["authority_reference"],
        idempotency_key=f"exec_receipt:{mission.mission_id}:{facts['execution_id']}:{facts['receipt_sha']}",
        evidence_class="live_observed",
        trust_level="live_observed",
        producer_identity=EXECUTION_EXECUTOR,
        action_binding=f"{capability}:{input_hash}:{output_hash}",
        outcome_data=outcome_data,
        mutation_authorized=False,
        authority_contract={
            "schema_version": "fr.execution_authority.v1",
            "dispatch_approval_id": approval_id,
            "governance_authorization_id": governance_authorization_id,
            "work_spec_sha256": mission.work_spec_sha256,
        },
    )
    receipt.compute_hash()
    return receipt


def build_acceptance(receipt: ExecutionReceipt) -> list[AcceptanceResult]:
    data = receipt.outcome_data
    detail = {
        "sealed_dispatch_receipt_verified": f"PAYLOAD_HASH {data['receipt_sha']} recomputed from the sealed fields",
        "independent_court_passed": f"{data['court_id']} record {data['court_record_sha']} PASS, clean, "
                                    f"bound {','.join(data['court_bindings'])}",
        "authority_bound": f"{data['authority_reference']} (governance {data['governance_authorization_id']})",
        "no_consequential_effect": "work spec carries no production, external, money, irreversible or IP flag; "
                                   "no files changed; mutation_authorized false",
    }
    return [AcceptanceResult(criterion, True, receipt.receipt_id, detail[criterion])
            for criterion in EXECUTION_ACCEPTANCE_CRITERIA]


def build_outcome_record(mission: Mission, *, receipts: list[ExecutionReceipt],
                         execution_receipt: ExecutionReceipt,
                         lineage: Mapping[str, Any] | None) -> dict[str, Any]:
    """The verified outcome record. Satisfies MissionRuntime.record_outcome exactly
    (result/deliverable/evidence-class reconciliation) and carries the lineage a
    reader needs to walk from outcome back to authority, execution and court."""
    receipt_hashes = sorted(r.receipt_hash for r in receipts)
    deliverable_hash = hash_payload([d.to_dict() for d in mission.deliverables])
    classes = {r.evidence_class for r in receipts}
    data = execution_receipt.outcome_data
    material: dict[str, Any] = {
        "schema": SCHEMA_OUTCOME,
        "outcome_id": stable_id("outcome", mission.mission_id, mission.plan_id, data["execution_id"]),
        "mission_id": mission.mission_id,
        "plan_id": mission.plan_id,
        "status": "verified",
        "receipt_hashes": receipt_hashes,
        "deliverable_hash": deliverable_hash,
        "result_hash": hash_payload({
            "receipt_hashes": receipt_hashes, "deliverable_hash": deliverable_hash, "mutation": False,
        }),
        "evidence_class": next(iter(classes)) if len(classes) == 1 else "mixed",
        "mutation_authorized": False,
        "execution_receipt_id": execution_receipt.receipt_id,
        "execution_id": data["execution_id"],
        "work_spec_sha256": data["work_spec_sha256"],
        "receipt_sha": data["receipt_sha"],
        "job_succeeded_sha": data["job_succeeded_sha"],
        "court_id": data["court_id"],
        "court_record_sha": data["court_record_sha"],
        "authority_reference": data["authority_reference"],
        "governance_authorization_id": data["governance_authorization_id"],
        "source_head": data["source_head"],
        "observed_at": data["court_sealed_at"],
        "parent_mission_id": (lineage or {}).get("parent_mission_id"),
        "parent_outcome_id": (lineage or {}).get("parent_outcome_id"),
        "lineage_sha256": (lineage or {}).get("lineage_sha256"),
    }
    return {**material, "outcome_hash": hash_payload(material)}


# ------------------------------------------------------------ continuation --

def validate_continuation_plan(raw: Any, *, depth: int = 1) -> dict[str, Any]:
    """What a mission declares it wants next, once it is verifiably complete.

    Raises MissionSchemaInvalid (HTTP 422 at creation). The declared work spec is
    validated exactly as a create request's; its authority is NOT decided here —
    the continuation child passes its own gate when it exists.
    """
    if depth > MAX_CONTINUATION_DEPTH:
        raise MissionSchemaInvalid("continuation chain exceeds its bound",
                                   {"max_depth": MAX_CONTINUATION_DEPTH})
    if not isinstance(raw, Mapping):
        raise MissionSchemaInvalid("continuation must be an object", {})
    keys = set(raw)
    missing = sorted(_CONTINUATION_REQUIRED - keys)
    unknown = sorted(keys - _CONTINUATION_REQUIRED - _CONTINUATION_OPTIONAL)
    if missing or unknown:
        raise MissionSchemaInvalid("continuation fields are incomplete or extended",
                                   {"missing": missing, "unknown": unknown})
    objective = raw["objective"]
    done = raw.get("done_criteria", "")
    if not isinstance(objective, str) or not objective.strip() or len(objective) > 8000:
        raise MissionSchemaInvalid("continuation objective must be a bounded non-empty string", {})
    if not isinstance(done, str) or len(done) > 4000:
        raise MissionSchemaInvalid("continuation done_criteria must be a bounded string", {})
    work_spec = validate_work_spec_request(raw["work_spec"])
    nested = raw.get("continuation")
    material = {
        "schema": SCHEMA_CONTINUATION_PLAN,
        "depth": depth,
        "objective": objective,
        "done_criteria": done,
        "work_spec": work_spec,
        "continuation": (validate_continuation_plan(nested, depth=depth + 1)
                         if nested is not None else None),
    }
    return {**material, "plan_sha256": hash_payload(material)}


def verify_continuation_plan(plan: Any) -> dict[str, Any]:
    """A stored plan must still reproduce its own digest (read back from a mission body)."""
    if not isinstance(plan, Mapping) or plan.get("schema") != SCHEMA_CONTINUATION_PLAN:
        raise _rejected("CONTINUATION_PLAN_INVALID", "mission carries no readable continuation plan")
    material = {k: v for k, v in plan.items() if k != "plan_sha256"}
    if hash_payload(material) != plan.get("plan_sha256"):
        raise _rejected("CONTINUATION_PLAN_INVALID", "continuation plan no longer reproduces its digest")
    return dict(plan)


CONTINUATION_KEY_PREFIX = "continuation:"


def continuation_creation_key(parent_mission_id: str, plan_sha256: str) -> str:
    """Deterministic, so scheduling the same continuation twice is one mission.
    The prefix is reserved: public creates may not use it."""
    return f"{CONTINUATION_KEY_PREFIX}{parent_mission_id}:{plan_sha256[:32]}"


def build_lineage(parent: Mission, *, completion: Mapping[str, Any], outcome: Mapping[str, Any],
                  plan: Mapping[str, Any]) -> dict[str, Any]:
    """What a continuation child carries back to its verifiably completed parent."""
    parent_lineage = (parent.extensions or {}).get("lineage") or {}
    material = {
        "schema": SCHEMA_LINEAGE,
        "depth": int(plan.get("depth") or 1),
        "root_mission_id": parent_lineage.get("root_mission_id") or parent.mission_id,
        "parent_mission_id": parent.mission_id,
        "parent_state": parent.current_state.value,
        "parent_completion_hash": completion["completion_hash"],
        "parent_completed_at": completion["completed_at"],
        "parent_outcome_id": outcome["outcome_id"],
        "parent_outcome_hash": outcome["outcome_hash"],
        "parent_execution_id": outcome["execution_id"],
        "parent_receipt_sha": outcome["receipt_sha"],
        "parent_job_succeeded_sha": outcome["job_succeeded_sha"],
        "parent_court_id": outcome["court_id"],
        "parent_court_record_sha": outcome["court_record_sha"],
        "parent_work_spec_sha256": outcome["work_spec_sha256"],
        "parent_authority_reference": outcome["authority_reference"],
        "parent_lineage_sha256": parent_lineage.get("lineage_sha256"),
        "continuation_plan_sha256": plan["plan_sha256"],
    }
    return {**material, "lineage_sha256": hash_payload(material)}


def verify_lineage(lineage: Any) -> dict[str, Any]:
    if not isinstance(lineage, Mapping) or lineage.get("schema") != SCHEMA_LINEAGE:
        raise _rejected("LINEAGE_INVALID", "mission lineage is unreadable")
    material = {k: v for k, v in lineage.items() if k != "lineage_sha256"}
    if hash_payload(material) != lineage.get("lineage_sha256"):
        raise _rejected("LINEAGE_INVALID", "mission lineage no longer reproduces its digest")
    for key in ("parent_receipt_sha", "parent_court_record_sha", "parent_job_succeeded_sha"):
        if not isinstance(lineage.get(key), str) or not _SHA256.fullmatch(lineage[key]):
            raise _rejected("LINEAGE_INVALID", "lineage digest is not canonical", field=key)
    return dict(lineage)


__all__ = [
    "SCHEMA_RECONCILE_REQUEST", "SCHEMA_EXECUTION_EVIDENCE", "SCHEMA_OUTCOME",
    "SCHEMA_CONTINUATION_PLAN", "SCHEMA_LINEAGE", "EXECUTION_STEP_ID", "EXECUTION_EXECUTOR",
    "REQUIRED_COURT_BINDINGS", "MAX_CONTINUATION_DEPTH", "CONTINUATION_KEY_PREFIX",
    "build_acceptance", "build_execution_receipt", "build_lineage", "build_outcome_record",
    "continuation_creation_key", "execution_facts", "require_consequence_free",
    "require_execution_deliverables", "validate_continuation_plan", "validate_reconcile_request",
    "verify_continuation_plan", "verify_court_record", "verify_lineage", "workers_content_sha",
]
