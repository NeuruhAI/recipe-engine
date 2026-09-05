"""Shared fixtures for the Founder Runtime execution-outcome write-back tests.

Everything here is computed on the TEST side, independently of the runtime
implementation, so a test proves the contract rather than the code's opinion of
itself: receipt hashes are recomputed with the emitter's (neuruh-workers)
canonicalisation, and envelopes are assembled from a mission's own envelope.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from recipe_engine.founder_runtime.api.service import MissionService
from recipe_engine.founder_runtime.contracts.work_spec import SCHEMA_WORK_SPEC
from tests.founder_runtime.api.test_approval_resolver import SHA, _Governance

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MISSION_B_RECEIPT = FIXTURES / "mission_b_fr_dispatch_receipt.txt"
# Live, immutable evidence (A01 [7]): PAYLOAD_HASH of the Mission B PRIME receipt.
MISSION_B_PAYLOAD_HASH = "50a9875f2d6501bbf3edebed64aa65944f27bc85144776e00dfe0e3948213f38"
MISSION_B_ID = "msn_4ab9161624124dcda0c5a2a445a3a818"

WORKERS_HEAD = "3a1429b59f12086d3ac1cebc3d6b516d88707c43"
FIXTURE_WORKTREE = "/tmp/neuruh-workers-fixture-worktree"
JOB_ID = "job-0123456789abcdef01234567"
RECEIPT_SCHEMA = "nrh.receipt.fr-dispatch.v1"


def workers_canonical_json(value: Any) -> str:
    """neuruh_workers.contracts.canonical_json, verbatim."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def workers_content_sha(value: Any) -> str:
    return hashlib.sha256(workers_canonical_json(value).encode("utf-8")).hexdigest()


def parse_receipt_lines(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        fields[key.strip()] = value.strip()
    return fields


def seal(fields: Mapping[str, str]) -> dict[str, str]:
    """Attach PAYLOAD_HASH exactly as neuruh_workers.prime_receipts.seal_receipt_text does."""
    body = {k: v for k, v in fields.items() if k != "PAYLOAD_HASH"}
    return {**body, "PAYLOAD_HASH": workers_content_sha(body)}


def work_spec(**updates: Any) -> dict[str, Any]:
    value = {
        "schema": SCHEMA_WORK_SPEC,
        "workflow_name": "property-research",
        "params": {"address": "77 Orchard Ln, Benton Harbor, MI", "county": "Berrien"},
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


def queued_service(tmp_path, name: str = "writeback") -> MissionService:
    """A service whose fixture Governance auto-resolves a work-spec mission to QUEUED."""
    return MissionService(str(tmp_path / f"{name}.sqlite"), governance=_Governance(),
                          attested_source_sha=SHA)


def create_queued(service: MissionService, *, key: str = "wb-create",
                  spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = service.create_mission(
        objective="Read-only public-record research plan for the supplied address.",
        work_spec=work_spec() if spec is None else dict(spec),
        idempotency_key=key,
    )
    assert result["envelope"]["state"] == "QUEUED", result["envelope"]["state"]
    assert result["envelope"]["authority"]["dispatch_approval_id"]
    return result["envelope"]


def authority_reference(approval_id: str, work_spec_sha256: str) -> str:
    return f"fr-api-v1:approval:{approval_id}:work-spec:{work_spec_sha256}"


def sealed_receipt_fields(env: Mapping[str, Any], *, job_id: str = JOB_ID,
                          dispatch_result: str = "SUCCEEDED",
                          overrides: Mapping[str, str] | None = None,
                          drop: tuple[str, ...] = ()) -> dict[str, str]:
    """Build a sealed nrh.receipt.fr-dispatch.v1 the way the workers bridge does."""
    mission_id = env["mission_id"]
    version = env["version"]
    sha = env["work_spec_sha256"]
    approval_id = env["authority"]["dispatch_approval_id"]
    values = [
        ("RECEIPT_SCHEMA", RECEIPT_SCHEMA),
        ("MISSION_ID", mission_id),
        ("MISSION_VERSION", str(version)),
        ("WORK_SPEC_SHA256", sha),
        ("IDEMPOTENCY_KEY", workers_content_sha({"mission_id": mission_id, "job": job_id})),
        ("DISPATCH_RESULT", dispatch_result),
        ("STAGE_ID", "fr-to-workers-dispatch"),
        ("TASK_ID", job_id),
        ("SOURCE_HEAD", WORKERS_HEAD),
        ("AUTHORITY_REFERENCE", authority_reference(approval_id, sha)),
        ("EVENT_TARGET", "receipt.sealed"),
        ("EVENT_IMPLEMENTATION_STATUS", "EMITTED"),
        ("REPO", "NeuruhAI/neuruh-workers"),
        ("WORKTREE", FIXTURE_WORKTREE),
        ("BRANCH", "main"),
        ("REMOTE", "origin"),
        ("LEDGER_TAIL_SHA", workers_content_sha({"ledger": job_id})),
        ("JOB_ID", job_id),
        ("CONTEXT_DIGEST", workers_content_sha({"context": job_id})),
        ("EVIDENCE_REFS", "workflow:property-research@1.0.0"),
        ("JOB_SUCCEEDED_SHA", workers_content_sha({"ledger": job_id})),
        ("SENTINEL_MODEL_EXECUTION", "BLOCKED"),
        ("SENTINEL_CLOSURE", "NOT_JUDGED"),
        ("PRIME_VERDICT", "NOT_JUDGED"),
        ("NETWORK_POLICY", "127.0.0.1:local-only"),
        ("SOURCE_ROLE", "DISPATCH_BRIDGE"),
        ("SOURCE_COMPONENT", "fr-to-workers-dispatch-bridge"),
        ("CODE_CUSTODY_REQUIRED", "NO"),
    ]
    fields = {k: v for k, v in values if k not in drop}
    fields.update(dict(overrides or {}))
    return seal(fields)


def outcome_envelope(env: Mapping[str, Any], outcome: str, *, job_id: str = JOB_ID,
                     receipt_fields: Mapping[str, str] | None = None,
                     court: Mapping[str, Any] | None = None,
                     failure: Mapping[str, Any] | None = None,
                     observed_at: str = "2026-09-04T23:14:56.000Z",
                     **overrides: Any) -> dict[str, Any]:
    """An fr.execution_outcome.v1 envelope bound to ``env`` (a mission envelope)."""
    sha = env["work_spec_sha256"]
    approval_id = env["authority"]["dispatch_approval_id"]
    body: dict[str, Any] = {
        "schema": "fr.execution_outcome.v1",
        "api_version": "fr-api-v1",
        "mission_id": env["mission_id"],
        "mission_version": env["version"],
        "work_spec_sha256": sha,
        "execution_id": job_id,
        "outcome": outcome,
        "authority_reference": authority_reference(approval_id, sha),
        "observed_at": observed_at,
        "source": {
            "role": "DISPATCH_BRIDGE",
            "component": "fr-to-workers-dispatch-bridge",
            "repo": "NeuruhAI/neuruh-workers",
            "head": WORKERS_HEAD,
        },
    }
    if outcome == "SUCCEEDED":
        fields = dict(receipt_fields) if receipt_fields is not None else sealed_receipt_fields(env, job_id=job_id)
        body["receipt"] = {
            "schema": RECEIPT_SCHEMA,
            "fields": fields,
            "receipt_sha": fields["PAYLOAD_HASH"],
            "receipt_ref": f"/tmp/switchboard/receipts/{job_id}.txt",
        }
    elif receipt_fields is not None:
        body["receipt"] = {
            "schema": RECEIPT_SCHEMA,
            "fields": dict(receipt_fields),
            "receipt_sha": receipt_fields["PAYLOAD_HASH"],
            "receipt_ref": f"/tmp/switchboard/receipts/{job_id}.txt",
        }
    if outcome in ("COURT_PASSED", "COURT_FAILED"):
        default_court = {
            "court_id": "court-0123456789abcdef01234567",
            "verdict": "PASS" if outcome == "COURT_PASSED" else "FAIL",
            "receipt_sha": (receipt_fields or {}).get("PAYLOAD_HASH", "0" * 64),
            "record_sha": workers_content_sha({"court": outcome, "job": job_id}),
            "source_head": WORKERS_HEAD,
            "independent": True,
            "source_role": "SENTINEL",
            "findings": [],
        }
        default_court.update(dict(court or {}))
        body["court"] = default_court
    elif court is not None:
        body["court"] = dict(court)
    if failure is not None:
        body["failure"] = dict(failure)
    body.update(overrides)
    return body


def court_envelope(env: Mapping[str, Any], outcome: str, succeeded_body: Mapping[str, Any],
                   **court_overrides: Any) -> dict[str, Any]:
    """A court outcome bound to the receipt of an accepted SUCCEEDED envelope."""
    sha = succeeded_body["receipt"]["receipt_sha"]
    return outcome_envelope(env, outcome, job_id=succeeded_body["execution_id"],
                            court={"receipt_sha": sha, **court_overrides})
