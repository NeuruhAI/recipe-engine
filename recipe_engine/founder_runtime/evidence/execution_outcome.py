"""Execution-outcome write-back evidence (fr.execution_outcome.v1) — pure, fail closed.

Closes G-03 (A01 [13]): a Workers execution reached a sealed terminal receipt
while the Founder Runtime mission stayed QUEUED. The Founder Runtime is the
mission-state authority, so it must LEARN the outcome from evidence rather than
be told a state. This module is the whole contract:

* ``validate_execution_outcome`` — shape only, no mission needed.  Anything
  outside the exact vocabulary is ``ExecutionOutcomeMalformed`` (HTTP 400).
* ``verify_receipt_evidence`` — a SUCCEEDED outcome must carry the sealed
  ``nrh.receipt.fr-dispatch.v1`` receipt.  Its PAYLOAD_HASH is recomputed with
  the emitter's canonicalisation (neuruh_workers.contracts.content_sha) and every
  field the Founder Runtime has a stake in must agree with the envelope.  A
  receipt may never carry its own verdict.
* ``bind_to_mission`` — mission-id, work-spec, authority, execution-id and
  dispatched-version bindings; ordering (stale / out-of-order / conflict) against
  the mission's execution projection; the exact events to append.
* ``apply_outcome_to_projection`` — the deterministic projection the event
  projector derives from an appended event (``mission.extensions["execution"]``).

Outcome vocabulary (input): DISPATCHED, EXECUTING, SUCCEEDED, FAILED,
COURT_PASSED, COURT_FAILED.  COURT_PENDING is a projection value only: it is
what a SUCCEEDED execution IS until an independent court speaks.  Nothing here
reaches COMPLETED — execution completing is not verification, and a court
passing is not completion (that stays with the canonical completion verifier).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from recipe_engine.founder_runtime.errors import (
    ExecutionOutcomeMalformed,
    ExecutionOutcomeRejected,
    MissionSchemaInvalid,
)
from recipe_engine.founder_runtime.state.states import MissionState

SCHEMA_EXECUTION_OUTCOME = "fr.execution_outcome.v1"
SCHEMA_EXECUTION_PROJECTION = "fr.execution_projection.v1"
API_VERSION = "fr-api-v1"
FR_DISPATCH_RECEIPT_SCHEMA = "nrh.receipt.fr-dispatch.v1"
EXECUTION_PROJECTION_KEY = "execution"

OUTCOME_DISPATCHED = "DISPATCHED"
OUTCOME_EXECUTING = "EXECUTING"
OUTCOME_SUCCEEDED = "SUCCEEDED"
OUTCOME_FAILED = "FAILED"
OUTCOME_COURT_PASSED = "COURT_PASSED"
OUTCOME_COURT_FAILED = "COURT_FAILED"
OUTCOMES: tuple[str, ...] = (
    OUTCOME_DISPATCHED, OUTCOME_EXECUTING, OUTCOME_SUCCEEDED, OUTCOME_FAILED,
    OUTCOME_COURT_PASSED, OUTCOME_COURT_FAILED,
)
COURT_OUTCOMES = frozenset({OUTCOME_COURT_PASSED, OUTCOME_COURT_FAILED})
TERMINAL_EXECUTION_OUTCOMES = frozenset({OUTCOME_SUCCEEDED, OUTCOME_FAILED})
# Monotone order: a lower-ranked outcome after a higher one is stale; equal rank
# with a different outcome is a contradiction.
RANK = {
    OUTCOME_DISPATCHED: 1, OUTCOME_EXECUTING: 2,
    OUTCOME_SUCCEEDED: 3, OUTCOME_FAILED: 3,
    OUTCOME_COURT_PASSED: 4, OUTCOME_COURT_FAILED: 4,
}
# The mission state each accepted phase leaves the mission in.
STATE_FOR_PHASE = {
    OUTCOME_DISPATCHED: MissionState.EXECUTING,
    OUTCOME_EXECUTING: MissionState.EXECUTING,
    OUTCOME_SUCCEEDED: MissionState.VERIFYING,
    OUTCOME_FAILED: MissionState.FAILED_RETRYABLE,
    OUTCOME_COURT_PASSED: MissionState.RECONCILING,
    OUTCOME_COURT_FAILED: MissionState.FAILED_RETRYABLE,
}
COURT_NOT_REQUESTED = "NOT_REQUESTED"
COURT_PENDING = "PENDING"
COURT_PASSED = "PASSED"
COURT_FAILED = "FAILED"

_TOP_LEVEL_REQUIRED = frozenset({
    "schema", "api_version", "mission_id", "mission_version", "work_spec_sha256",
    "execution_id", "outcome", "authority_reference", "observed_at", "source",
})
_TOP_LEVEL_OPTIONAL = frozenset({"receipt", "court", "failure"})
_SOURCE_FIELDS = frozenset({"role", "component", "repo", "head"})
_RECEIPT_FIELDS = frozenset({"schema", "fields", "receipt_sha", "receipt_ref"})
_COURT_REQUIRED = frozenset({"court_id", "verdict", "receipt_sha", "record_sha", "independent", "source_role"})
_COURT_OPTIONAL = frozenset({"source_head", "findings", "court_ref"})
_FAILURE_FIELDS = frozenset({"error_class", "detail"})

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEAD40 = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_RECEIPT_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_AUTHORITY = re.compile(r"^fr-api-v1:approval:(?P<approval>[^:\r\n]+):work-spec:(?P<sha>[0-9a-f]{64})$")
_SECRET_SHAPES = (
    re.compile(r"secret_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9\-_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[abpsr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bntn_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-or-[A-Za-z0-9\-]{16,}"),
    re.compile(r"Bearer [A-Za-z0-9\-_\.]{30,}"),
)
_MAX_RECEIPT_FIELDS = 64
_MAX_RECEIPT_VALUE = 4096
_MAX_DETAIL = 2000
_MAX_FINDINGS = 64

# Every field the workers bridge writes into a sealed dispatch receipt
# (neuruh_workers.organ_consumers._fr_dispatch_receipt_text) — required verbatim.
DISPATCH_RECEIPT_REQUIRED_FIELDS: frozenset[str] = frozenset({
    "RECEIPT_SCHEMA", "MISSION_ID", "MISSION_VERSION", "WORK_SPEC_SHA256", "IDEMPOTENCY_KEY",
    "DISPATCH_RESULT", "STAGE_ID", "TASK_ID", "SOURCE_HEAD", "AUTHORITY_REFERENCE", "EVENT_TARGET",
    "EVENT_IMPLEMENTATION_STATUS", "REPO", "WORKTREE", "BRANCH", "REMOTE", "LEDGER_TAIL_SHA",
    "JOB_ID", "CONTEXT_DIGEST", "EVIDENCE_REFS", "JOB_SUCCEEDED_SHA", "SENTINEL_MODEL_EXECUTION",
    "SENTINEL_CLOSURE", "PRIME_VERDICT", "NETWORK_POLICY", "SOURCE_ROLE", "SOURCE_COMPONENT",
    "CODE_CUSTODY_REQUIRED", "PAYLOAD_HASH",
})
DISPATCH_SOURCE_ROLE = "DISPATCH_BRIDGE"
DISPATCH_SOURCE_COMPONENT = "fr-to-workers-dispatch-bridge"
DISPATCH_STAGE_ID = "fr-to-workers-dispatch"
NOT_JUDGED = "NOT_JUDGED"
COURT_SOURCE_ROLE = "SENTINEL"


# ----------------------------------------------------------------- hashing --

def workers_canonical_json(value: Any) -> str:
    """neuruh_workers.contracts.canonical_json — the emitter's canonical form, verbatim.

    Deliberately NOT the Founder Runtime's own canonical_json: the receipt was
    hashed by the workers bridge, so it is re-hashed exactly the way it was made.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def dispatch_receipt_sha(fields: Mapping[str, str]) -> str:
    """PAYLOAD_HASH of a sealed PRIME/dispatch receipt: sha256 over the fields minus PAYLOAD_HASH."""
    body = {str(k): str(v) for k, v in fields.items() if k != "PAYLOAD_HASH"}
    return hashlib.sha256(workers_canonical_json(body).encode("utf-8")).hexdigest()


def parse_receipt_text(text: str) -> dict[str, str]:
    """KEY=VALUE receipt text -> fields (neuruh_workers.prime_receipts.parse_prime_receipt).

    A duplicated key is refused here rather than silently last-wins: the
    emitter's validator deadletters duplicates, so accepting them would trust
    what the emitter itself refuses.
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key in fields:
            raise ExecutionOutcomeMalformed("receipt text repeats a field", {"field": key})
        fields[key] = value.strip()
    return fields


def contains_secret_shape(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_SHAPES)


# --------------------------------------------------------------- validation --

def _malformed(message: str, **details: Any) -> ExecutionOutcomeMalformed:
    return ExecutionOutcomeMalformed(message, details)


def _rejected(reason_code: str, message: str, **details: Any) -> ExecutionOutcomeRejected:
    return ExecutionOutcomeRejected(reason_code, message, details)


def _require_str(value: Any, path: str, *, pattern: re.Pattern[str] | None = None,
                 max_len: int = 512, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _malformed("field must be a string", path=path)
    if "\n" in value or "\r" in value or "\x00" in value:
        raise _malformed("field must be a single line", path=path)
    if not allow_empty and not value.strip():
        raise _malformed("field must not be empty", path=path)
    if len(value) > max_len:
        raise _malformed("field exceeds bound", path=path, max=max_len)
    if pattern is not None and not pattern.fullmatch(value):
        raise _malformed("field is not in canonical form", path=path)
    return value


def _require_keys(mapping: Mapping[str, Any], *, required: frozenset[str],
                  optional: frozenset[str], path: str) -> None:
    if not isinstance(mapping, Mapping):
        raise _malformed("object expected", path=path)
    keys = set(mapping)
    if any(not isinstance(key, str) for key in keys):
        raise _malformed("object keys must be strings", path=path)
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    if missing or unknown:
        raise _malformed("object fields are incomplete or extended", path=path,
                         missing=missing, unknown=unknown)


def _require_iso(value: Any, path: str) -> str:
    text = _require_str(value, path, max_len=64)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _malformed("timestamp is not ISO-8601", path=path) from exc
    return text


def parse_authority_reference(value: str) -> tuple[str, str]:
    """-> (approval_id, work_spec_sha256) or ExecutionOutcomeMalformed."""
    match = _AUTHORITY.fullmatch(value)
    if not match:
        raise _malformed("authority_reference must name an FR approval and work-spec digest",
                         path="$.authority_reference")
    return match.group("approval"), match.group("sha")


def _validate_receipt_shape(raw: Any, *, path: str = "$.receipt") -> dict[str, Any]:
    _require_keys(raw, required=_RECEIPT_FIELDS, optional=frozenset(), path=path)
    schema = _require_str(raw["schema"], f"{path}.schema", max_len=64)
    if schema != FR_DISPATCH_RECEIPT_SCHEMA:
        raise _malformed("unsupported receipt schema", path=f"{path}.schema", actual=schema)
    receipt_sha = _require_str(raw["receipt_sha"], f"{path}.receipt_sha", pattern=_SHA256)
    receipt_ref = _require_str(raw["receipt_ref"], f"{path}.receipt_ref", max_len=1024, allow_empty=True)
    fields_raw = raw["fields"]
    if not isinstance(fields_raw, Mapping):
        raise _malformed("receipt fields must be an object", path=f"{path}.fields")
    if len(fields_raw) > _MAX_RECEIPT_FIELDS:
        raise _malformed("receipt carries too many fields", path=f"{path}.fields")
    fields: dict[str, str] = {}
    for key, value in fields_raw.items():
        if not isinstance(key, str) or not _RECEIPT_KEY.fullmatch(key):
            raise _malformed("receipt field name is not canonical", path=f"{path}.fields")
        fields[key] = _require_str(value, f"{path}.fields.{key}", max_len=_MAX_RECEIPT_VALUE, allow_empty=True)
    missing = sorted(DISPATCH_RECEIPT_REQUIRED_FIELDS - set(fields))
    if missing:
        raise _malformed("dispatch receipt fields missing", path=f"{path}.fields", missing=missing)
    if not _SHA256.fullmatch(fields["PAYLOAD_HASH"]):
        raise _malformed("PAYLOAD_HASH must be lowercase 64-hex", path=f"{path}.fields.PAYLOAD_HASH")
    if contains_secret_shape("\n".join(f"{k}={v}" for k, v in fields.items())):
        raise _malformed("credential-shaped receipt content refused", path=f"{path}.fields")
    return {"schema": schema, "fields": fields, "receipt_sha": receipt_sha, "receipt_ref": receipt_ref}


def _validate_court_shape(raw: Any, *, outcome: str, path: str = "$.court") -> dict[str, Any]:
    _require_keys(raw, required=_COURT_REQUIRED, optional=_COURT_OPTIONAL, path=path)
    court_id = _require_str(raw["court_id"], f"{path}.court_id", pattern=_ID)
    verdict = _require_str(raw["verdict"], f"{path}.verdict", max_len=16)
    expected_verdict = "PASS" if outcome == OUTCOME_COURT_PASSED else "FAIL"
    if verdict != expected_verdict:
        raise _malformed("court verdict disagrees with the outcome", path=f"{path}.verdict",
                         verdict=verdict, outcome=outcome)
    receipt_sha = _require_str(raw["receipt_sha"], f"{path}.receipt_sha", pattern=_SHA256)
    record_sha = _require_str(raw["record_sha"], f"{path}.record_sha", pattern=_SHA256)
    if raw["independent"] is not True:
        raise _malformed("a court outcome must come from an independent court", path=f"{path}.independent")
    source_role = _require_str(raw["source_role"], f"{path}.source_role", max_len=64)
    if source_role != COURT_SOURCE_ROLE:
        raise _malformed("court source_role must be SENTINEL", path=f"{path}.source_role", actual=source_role)
    court: dict[str, Any] = {
        "court_id": court_id, "verdict": verdict, "receipt_sha": receipt_sha,
        "record_sha": record_sha, "independent": True, "source_role": source_role,
    }
    if "source_head" in raw:
        court["source_head"] = _require_str(raw["source_head"], f"{path}.source_head", pattern=_HEAD40)
    if "court_ref" in raw:
        court["court_ref"] = _require_str(raw["court_ref"], f"{path}.court_ref", max_len=1024, allow_empty=True)
    if "findings" in raw:
        findings = raw["findings"]
        if not isinstance(findings, list) or len(findings) > _MAX_FINDINGS:
            raise _malformed("court findings must be a bounded array", path=f"{path}.findings")
        court["findings"] = [
            _require_str(item, f"{path}.findings[{index}]", max_len=500) for index, item in enumerate(findings)
        ]
    return court


def _validate_failure_shape(raw: Any, *, path: str = "$.failure") -> dict[str, str]:
    _require_keys(raw, required=_FAILURE_FIELDS, optional=frozenset(), path=path)
    return {
        "error_class": _require_str(raw["error_class"], f"{path}.error_class", max_len=128),
        "detail": _require_str(raw["detail"], f"{path}.detail", max_len=_MAX_DETAIL, allow_empty=True),
    }


def validate_execution_outcome(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Shape validation. Returns a normalised deep copy; raises ExecutionOutcomeMalformed."""
    _require_keys(raw, required=_TOP_LEVEL_REQUIRED, optional=_TOP_LEVEL_OPTIONAL, path="$")
    schema = _require_str(raw["schema"], "$.schema", max_len=64)
    if schema != SCHEMA_EXECUTION_OUTCOME:
        raise _malformed("unsupported execution outcome schema", path="$.schema", actual=schema)
    api_version = _require_str(raw["api_version"], "$.api_version", max_len=32)
    if api_version != API_VERSION:
        raise _malformed("unsupported api_version", path="$.api_version", actual=api_version)
    mission_id = _require_str(raw["mission_id"], "$.mission_id", pattern=_ID)
    mission_version = raw["mission_version"]
    if type(mission_version) is not int or mission_version < 1:
        raise _malformed("mission_version must be a positive integer", path="$.mission_version")
    work_spec_sha256 = _require_str(raw["work_spec_sha256"], "$.work_spec_sha256", pattern=_SHA256)
    execution_id = _require_str(raw["execution_id"], "$.execution_id", pattern=_ID)
    outcome = _require_str(raw["outcome"], "$.outcome", max_len=32)
    if outcome not in OUTCOMES:
        raise _malformed("unknown outcome", path="$.outcome", actual=outcome, allowed=list(OUTCOMES))
    authority_reference = _require_str(raw["authority_reference"], "$.authority_reference", max_len=256)
    parse_authority_reference(authority_reference)  # shape only; binding is mission-relative
    observed_at = _require_iso(raw["observed_at"], "$.observed_at")
    _require_keys(raw["source"], required=_SOURCE_FIELDS, optional=frozenset(), path="$.source")
    source = {
        "role": _require_str(raw["source"]["role"], "$.source.role", max_len=64),
        "component": _require_str(raw["source"]["component"], "$.source.component", max_len=128),
        "repo": _require_str(raw["source"]["repo"], "$.source.repo", max_len=256),
        "head": _require_str(raw["source"]["head"], "$.source.head", pattern=_HEAD40),
    }

    normalized: dict[str, Any] = {
        "schema": schema, "api_version": api_version, "mission_id": mission_id,
        "mission_version": mission_version, "work_spec_sha256": work_spec_sha256,
        "execution_id": execution_id, "outcome": outcome,
        "authority_reference": authority_reference, "observed_at": observed_at,
        "source": source, "implied": False,
    }
    has_receipt = raw.get("receipt") is not None
    has_court = raw.get("court") is not None
    has_failure = raw.get("failure") is not None
    if outcome == OUTCOME_SUCCEEDED:
        if not has_receipt:
            raise _malformed("a SUCCEEDED outcome requires the sealed dispatch receipt", path="$.receipt")
        if has_court or has_failure:
            raise _malformed("a SUCCEEDED outcome carries a receipt only", path="$")
        normalized["receipt"] = _validate_receipt_shape(raw["receipt"])
    elif outcome in COURT_OUTCOMES:
        if not has_court:
            raise _malformed("a court outcome requires the court record", path="$.court")
        if has_receipt or has_failure:
            raise _malformed("a court outcome carries the court record only", path="$")
        normalized["court"] = _validate_court_shape(raw["court"], outcome=outcome)
    elif outcome == OUTCOME_FAILED:
        if has_receipt or has_court:
            raise _malformed("a FAILED outcome carries no receipt or court record", path="$")
        if has_failure:
            normalized["failure"] = _validate_failure_shape(raw["failure"])
    else:  # DISPATCHED / EXECUTING
        if has_receipt or has_court or has_failure:
            raise _malformed("a progress outcome carries no receipt, court record or failure", path="$")
    return normalized


def verify_receipt_evidence(outcome: Mapping[str, Any]) -> None:
    """Hash integrity, then every field the Founder Runtime has a stake in.

    Raises ExecutionOutcomeRejected(RECEIPT_HASH_MISMATCH | RECEIPT_BINDING_MISMATCH).
    """
    if outcome["outcome"] != OUTCOME_SUCCEEDED:
        return
    receipt = outcome["receipt"]
    fields = receipt["fields"]
    computed = dispatch_receipt_sha(fields)
    if fields["PAYLOAD_HASH"] != computed:
        raise _rejected("RECEIPT_HASH_MISMATCH", "PAYLOAD_HASH does not cover the receipt body",
                        declared=fields["PAYLOAD_HASH"], computed=computed)
    if receipt["receipt_sha"] != computed:
        raise _rejected("RECEIPT_HASH_MISMATCH", "receipt_sha disagrees with the sealed PAYLOAD_HASH",
                        declared=receipt["receipt_sha"], computed=computed)
    expected = {
        "RECEIPT_SCHEMA": FR_DISPATCH_RECEIPT_SCHEMA,
        "MISSION_ID": outcome["mission_id"],
        "MISSION_VERSION": str(outcome["mission_version"]),
        "WORK_SPEC_SHA256": outcome["work_spec_sha256"],
        "AUTHORITY_REFERENCE": outcome["authority_reference"],
        "TASK_ID": outcome["execution_id"],
        "JOB_ID": outcome["execution_id"],
        "DISPATCH_RESULT": OUTCOME_SUCCEEDED,
        "STAGE_ID": DISPATCH_STAGE_ID,
        "EVENT_TARGET": "receipt.sealed",
        "SOURCE_HEAD": outcome["source"]["head"],
        "REPO": outcome["source"]["repo"],
        "SOURCE_ROLE": DISPATCH_SOURCE_ROLE,
        "SOURCE_COMPONENT": DISPATCH_SOURCE_COMPONENT,
        # sentinel_closure_in_receipt_is_not_pass: a receipt cannot judge itself.
        "SENTINEL_CLOSURE": NOT_JUDGED,
        "PRIME_VERDICT": NOT_JUDGED,
    }
    for key, value in expected.items():
        if fields.get(key) != value:
            raise _rejected("RECEIPT_BINDING_MISMATCH", "sealed receipt disagrees with the outcome envelope",
                            field=key, expected=value, actual=fields.get(key))
    policy = fields["NETWORK_POLICY"].lower()
    if not ("127.0.0.1" in policy or "localhost" in policy or "::1" in policy):
        raise _rejected("RECEIPT_BINDING_MISMATCH", "dispatch receipt must be loopback-only", field="NETWORK_POLICY")
    if not os.path.isabs(fields["WORKTREE"]):
        raise _rejected("RECEIPT_BINDING_MISMATCH", "dispatch receipt WORKTREE must be absolute", field="WORKTREE")
    if fields["CODE_CUSTODY_REQUIRED"] not in ("YES", "NO"):
        raise _rejected("RECEIPT_BINDING_MISMATCH", "CODE_CUSTODY_REQUIRED must be YES or NO", field="CODE_CUSTODY_REQUIRED")
    for key in ("LEDGER_TAIL_SHA", "CONTEXT_DIGEST", "JOB_SUCCEEDED_SHA", "IDEMPOTENCY_KEY"):
        if not _SHA256.fullmatch(fields[key]):
            raise _rejected("RECEIPT_BINDING_MISMATCH", "dispatch receipt digest is not lowercase 64-hex", field=key)


# ------------------------------------------------------------ projection --

def outcome_identity(envelope: Mapping[str, Any]) -> str:
    """What makes two envelopes 'the same evidence' for replay detection."""
    outcome = envelope["outcome"]
    if outcome == OUTCOME_SUCCEEDED:
        return f"receipt_sha:{envelope['receipt']['receipt_sha']}"
    if outcome in COURT_OUTCOMES:
        court = envelope["court"]
        return f"court:{court['court_id']}:{court['record_sha']}:{court['receipt_sha']}"
    return f"execution:{envelope['execution_id']}"


def apply_outcome_to_projection(previous: Mapping[str, Any] | None, envelope: Mapping[str, Any],
                                *, event_id: str) -> dict[str, Any]:
    """Deterministic projection update. Total for kernel-written events; raises
    MissionSchemaInvalid for a tampered payload."""
    try:
        outcome = str(envelope["outcome"])
        if outcome not in OUTCOMES:
            raise KeyError(outcome)
        if previous is None:
            projection: dict[str, Any] = {
                "schema": SCHEMA_EXECUTION_PROJECTION,
                "execution_id": str(envelope["execution_id"]),
                "dispatched_version": int(envelope["mission_version"]),
                "work_spec_sha256": str(envelope["work_spec_sha256"]),
                "authority_reference": str(envelope["authority_reference"]),
                "phase": None,
                "court": COURT_NOT_REQUESTED,
                "receipt_sha": None,
                "court_id": None,
                "court_record_sha": None,
                "verified": False,
                "outcomes": [],
            }
        else:
            projection = deepcopy(dict(previous))
        receipt = envelope.get("receipt") or {}
        court = envelope.get("court") or {}
        entry = {
            "outcome": outcome,
            "implied": bool(envelope.get("implied", False)),
            "event_id": event_id,
            "observed_at": str(envelope["observed_at"]),
            "receipt_sha": receipt.get("receipt_sha"),
            "court_id": court.get("court_id"),
            "identity": outcome_identity(envelope),
        }
        projection["outcomes"] = list(projection.get("outcomes") or []) + [entry]
        projection["phase"] = outcome
        if outcome == OUTCOME_SUCCEEDED:
            projection["receipt_sha"] = str(receipt["receipt_sha"])
            projection["court"] = COURT_PENDING
        elif outcome == OUTCOME_FAILED:
            projection["court"] = COURT_NOT_REQUESTED
        elif outcome == OUTCOME_COURT_PASSED:
            projection["court"] = COURT_PASSED
            projection["court_id"] = str(court["court_id"])
            projection["court_record_sha"] = str(court["record_sha"])
        elif outcome == OUTCOME_COURT_FAILED:
            projection["court"] = COURT_FAILED
            projection["court_id"] = str(court["court_id"])
            projection["court_record_sha"] = str(court["record_sha"])
        # Never verified here: verification/completion belongs to the canonical
        # completion verifier, not to execution or court write-back.
        projection["verified"] = False
        return projection
    except (KeyError, TypeError, ValueError) as exc:
        raise MissionSchemaInvalid("execution outcome event payload is malformed",
                                   {"event_id": event_id, "error": type(exc).__name__}) from exc


# ---------------------------------------------------------------- binding --

@dataclass(frozen=True)
class OutcomeStep:
    prior_state: MissionState
    next_state: MissionState
    envelope: dict[str, Any]
    reason: str
    idempotency_key: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class OutcomeDecision:
    replay: bool
    steps: tuple[OutcomeStep, ...] = field(default_factory=tuple)


def _implied_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    implied = {key: deepcopy(envelope[key]) for key in (
        "schema", "api_version", "mission_id", "mission_version", "work_spec_sha256",
        "execution_id", "authority_reference", "observed_at", "source",
    )}
    implied["outcome"] = OUTCOME_EXECUTING
    implied["implied"] = True
    implied["implied_by"] = envelope["outcome"]
    return implied


def _evidence_refs(envelope: Mapping[str, Any]) -> tuple[str, ...]:
    refs = [f"execution:{envelope['execution_id']}"]
    if envelope.get("receipt"):
        refs.append(f"receipt_sha:{envelope['receipt']['receipt_sha']}")
    if envelope.get("court"):
        refs.append(f"court:{envelope['court']['court_id']}")
        refs.append(f"court_record_sha:{envelope['court']['record_sha']}")
    return tuple(refs)


_REASONS = {
    OUTCOME_DISPATCHED: "execution_dispatched",
    OUTCOME_EXECUTING: "execution_started",
    OUTCOME_SUCCEEDED: "execution_succeeded_court_pending",
    OUTCOME_FAILED: "execution_failed",
    OUTCOME_COURT_PASSED: "court_passed_reconciling",
    OUTCOME_COURT_FAILED: "court_failed",
}


def bind_to_mission(
    outcome: Mapping[str, Any],
    *,
    mission_id: str,
    mission_state: MissionState,
    mission_version: int,
    work_spec_sha256: str | None,
    dispatch_approval_id: str | None,
    projection: Mapping[str, Any] | None,
) -> OutcomeDecision:
    """Every binding and ordering rule. Returns the events to append (or a replay).

    Raises ExecutionOutcomeRejected with reason_code in:
      MISSION_MISMATCH, MISSION_TERMINAL, WORK_SPEC_ABSENT, WORK_SPEC_MISMATCH,
      AUTHORITY_MISMATCH, MISSION_STATE_CONFLICT, NOT_DISPATCHED_STATE,
      MISSION_VERSION_MISMATCH, EXECUTION_ID_MISMATCH, OUTCOME_STALE,
      OUTCOME_CONFLICT, OUTCOME_OUT_OF_ORDER, RECEIPT_BINDING_MISMATCH.
    """
    kind = outcome["outcome"]
    if outcome["mission_id"] != mission_id:
        raise _rejected("MISSION_MISMATCH", "outcome names a different mission",
                        route_mission_id=mission_id, envelope_mission_id=outcome["mission_id"])
    if mission_state in (MissionState.COMPLETED, MissionState.COMPLETED_WITH_EXCEPTIONS,
                         MissionState.FAILED_FINAL, MissionState.CANCELLED, MissionState.SUPERSEDED):
        raise _rejected("MISSION_TERMINAL", "terminal mission cannot learn an execution outcome",
                        state=mission_state.value)
    if not work_spec_sha256:
        raise _rejected("WORK_SPEC_ABSENT", "mission has no immutable work specification; nothing was dispatchable")
    if outcome["work_spec_sha256"] != work_spec_sha256:
        raise _rejected("WORK_SPEC_MISMATCH", "outcome is bound to a different work specification",
                        expected=work_spec_sha256, actual=outcome["work_spec_sha256"])
    approval_id, authority_sha = parse_authority_reference(outcome["authority_reference"])
    if authority_sha != work_spec_sha256:
        raise _rejected("AUTHORITY_MISMATCH", "authority_reference names a different work specification",
                        expected=work_spec_sha256, actual=authority_sha)
    if not dispatch_approval_id or approval_id != dispatch_approval_id:
        raise _rejected("AUTHORITY_MISMATCH", "authority_reference is not this mission's consumed dispatch approval",
                        expected=dispatch_approval_id, actual=approval_id)

    execution_id = outcome["execution_id"]
    if projection is None:
        if mission_state == MissionState.PAUSED:
            raise _rejected("MISSION_STATE_CONFLICT", "paused mission refuses outcomes until resumed",
                            state=mission_state.value)
        if mission_state != MissionState.QUEUED:
            raise _rejected("NOT_DISPATCHED_STATE", "mission was never dispatchable in its current state",
                            state=mission_state.value)
        if outcome["mission_version"] != mission_version:
            raise _rejected("MISSION_VERSION_MISMATCH", "outcome is bound to a version the dispatch did not consume",
                            expected=mission_version, actual=outcome["mission_version"])
        if kind in COURT_OUTCOMES:
            raise _rejected("OUTCOME_OUT_OF_ORDER", "a court cannot judge execution evidence FR never received")
        verify_receipt_evidence(outcome)
        steps: list[OutcomeStep] = []
        if kind in (OUTCOME_DISPATCHED, OUTCOME_EXECUTING):
            steps.append(OutcomeStep(MissionState.QUEUED, MissionState.EXECUTING, dict(outcome), _REASONS[kind],
                                     f"execution_outcome:{execution_id}:{kind}", _evidence_refs(outcome)))
        else:  # SUCCEEDED / FAILED straight from QUEUED: the EXECUTING hop is implied, and said so
            implied = _implied_envelope(outcome)
            steps.append(OutcomeStep(MissionState.QUEUED, MissionState.EXECUTING, implied, "execution_observed",
                                     f"execution_outcome:{execution_id}:EXECUTING:implied", _evidence_refs(implied)))
            steps.append(OutcomeStep(MissionState.EXECUTING, STATE_FOR_PHASE[kind], dict(outcome), _REASONS[kind],
                                     f"execution_outcome:{execution_id}:{kind}", _evidence_refs(outcome)))
        return OutcomeDecision(replay=False, steps=tuple(steps))

    # A dispatch already learned by FR: bind identity, version and state.
    if projection.get("execution_id") != execution_id:
        raise _rejected("EXECUTION_ID_MISMATCH", "mission was dispatched as a different execution",
                        expected=projection.get("execution_id"), actual=execution_id)
    if outcome["mission_version"] != projection.get("dispatched_version"):
        raise _rejected("MISSION_VERSION_MISMATCH", "outcome is bound to a version the dispatch did not consume",
                        expected=projection.get("dispatched_version"), actual=outcome["mission_version"])
    phase = projection.get("phase")
    if phase not in RANK:
        raise _rejected("MISSION_STATE_CONFLICT", "execution projection is unreadable", phase=phase)
    if mission_state != STATE_FOR_PHASE[phase]:
        raise _rejected("MISSION_STATE_CONFLICT", "mission state no longer matches its execution phase",
                        state=mission_state.value, phase=phase)
    verify_receipt_evidence(outcome)
    identity = outcome_identity(outcome)
    for entry in projection.get("outcomes") or []:
        if entry.get("outcome") == kind and not entry.get("implied"):
            if entry.get("identity") == identity:
                return OutcomeDecision(replay=True)
            raise _rejected("OUTCOME_CONFLICT", "the same outcome was already recorded with different evidence",
                            outcome=kind, recorded=entry.get("identity"), offered=identity)
    if RANK[kind] < RANK[phase]:
        raise _rejected("OUTCOME_STALE", "outcome precedes what the mission already learned",
                        phase=phase, outcome=kind)
    if RANK[kind] == RANK[phase]:
        if kind == phase:  # only reachable for an implied EXECUTING; a real one is a replay of the fact
            return OutcomeDecision(replay=True)
        raise _rejected("OUTCOME_CONFLICT", "outcome contradicts the recorded terminal result",
                        phase=phase, outcome=kind)
    if kind in COURT_OUTCOMES:
        if phase != OUTCOME_SUCCEEDED:
            raise _rejected("OUTCOME_OUT_OF_ORDER", "a court cannot judge execution evidence FR never received",
                            phase=phase)
        if outcome["court"]["receipt_sha"] != projection.get("receipt_sha"):
            raise _rejected("RECEIPT_BINDING_MISMATCH", "court is bound to a receipt FR did not accept",
                            expected=projection.get("receipt_sha"), actual=outcome["court"]["receipt_sha"])
    elif kind in TERMINAL_EXECUTION_OUTCOMES and phase not in (OUTCOME_DISPATCHED, OUTCOME_EXECUTING):
        raise _rejected("OUTCOME_OUT_OF_ORDER", "terminal execution outcome after a terminal phase", phase=phase)
    step = OutcomeStep(mission_state, STATE_FOR_PHASE[kind], dict(outcome), _REASONS[kind],
                       f"execution_outcome:{execution_id}:{kind}", _evidence_refs(outcome))
    return OutcomeDecision(replay=False, steps=(step,))


__all__ = [
    "SCHEMA_EXECUTION_OUTCOME", "SCHEMA_EXECUTION_PROJECTION", "FR_DISPATCH_RECEIPT_SCHEMA",
    "EXECUTION_PROJECTION_KEY", "OUTCOMES", "RANK", "STATE_FOR_PHASE",
    "OutcomeDecision", "OutcomeStep",
    "apply_outcome_to_projection", "bind_to_mission", "dispatch_receipt_sha",
    "outcome_identity", "parse_authority_reference", "parse_receipt_text",
    "validate_execution_outcome", "verify_receipt_evidence", "workers_canonical_json",
]
