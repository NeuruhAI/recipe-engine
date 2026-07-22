"""Pure P2-A.1 response-shape validators for future live adapters.

These functions accept already-observed mappings.  They perform no file,
process, environment, database, or network I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from recipe_engine.founder_runtime.adapters.base import OperationalWriteRef, WriteClass
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, validate_payload_hash
from recipe_engine.founder_runtime.errors import (
    MalformedResponse,
    ProvenanceInvalid,
    QdrantCollectionMismatch,
    QdrantCountChanged,
    QdrantCountMismatch,
    QdrantPayloadProvenanceMismatch,
    UnsafeCapability,
)


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
QDRANT_COLLECTION = "neuruh_vault_canon"
QDRANT_POINT_BASELINE = 3049
QDRANT_SCROLL_REQUEST = {"limit": 1, "with_payload": True, "with_vector": False}
QDRANT_MANIFEST_ID = "vault-canon-remediated-20260718T054050Z-cct22"
QDRANT_MANIFEST_SCHEMA = "neuruh.memory-source-manifest.v1"
IAR_HEALTH_KEYS = frozenset(
    {"enabled", "active_backend", "active_model", "backends", "degraded_reason"}
)
AEGIS_VERDICTS = frozenset({"VALID", "CONTRADICTORY", "VALID_NO_CONTRACT"})
GOVERNANCE_DECISIONS = frozenset({"allow", "escalate", "block"})
AXON_CONTAINMENT_CODES = frozenset(
    {"E_TRAVERSAL", "E_OUT_OF_WORKTREE", "E_SYMLINK_ESCAPE", "E_PRODUCTION_TARGET"}
)
AXON_TOOL_ALLOWLIST = frozenset({"file_read", "web_fetch"})
# Reviewed endpoint-specific schema for GET /health of the promoted AXON
# gateway f3a94d1a (v2.4.0-p1.1), transcribed from src/gateway/types.ts
# HealthResponse and the server.ts /health handler.  inference_backend and
# status_reason are optional in the reviewed type; every other field is
# required; ANY other field — top-level or nested — fails closed.
AXON_HEALTH_REQUIRED_KEYS = frozenset(
    {
        "status", "version", "uptime", "services", "inference", "bonsai",
        "execution_available", "bonsai_required", "degraded_reason",
    }
)
AXON_HEALTH_OPTIONAL_KEYS = frozenset({"inference_backend", "status_reason"})
AXON_HEALTH_SERVICE_KEYS = frozenset(
    {"coordinator", "toolRegistry", "governance", "governedExecutor", "bonsai", "ollama", "iar"}
)
AXON_INFERENCE_KEYS = frozenset({"authority", "provider", "model", "healthy"})


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MalformedResponse(f"{label} must be an object", {})
    return value


def _require_exact_keys(value: Mapping[str, Any], keys: set[str] | frozenset[str], label: str) -> None:
    if set(value) != set(keys):
        raise MalformedResponse(
            f"{label} has an invalid field set",
            {"missing": sorted(set(keys) - set(value)), "unexpected": sorted(set(value) - set(keys))},
        )


def _require_sha256(value: Any, label: str) -> None:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ProvenanceInvalid(f"{label} must be a SHA-256", {})


def validate_mother_equivalence_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    value = _require_mapping(receipt, "Mother equivalence receipt")
    required = {"services", "mutation_path", "cockpit_sha", "cycle_id", "timestamp", "seal"}
    if not required.issubset(value):
        raise MalformedResponse("Mother equivalence receipt is unbound", {"missing": sorted(required - set(value))})
    if value["mutation_path"] != "MUTATION_PATH_NOT_RATIFIED":
        raise UnsafeCapability("Mother receipt makes an unauthorized mutation-path claim", {})
    if not isinstance(value["cockpit_sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", value["cockpit_sha"]):
        raise ProvenanceInvalid("Mother receipt lacks a Cockpit commit binding", {})
    services = value["services"]
    if not isinstance(services, list) or not services:
        raise MalformedResponse("Mother receipt requires a non-empty service matrix", {})
    normalized = []
    seen_ports: set[int] = set()
    for service in services:
        item = _require_mapping(service, "Mother service")
        required_service = {"port", "status", "body_sha256", "pid", "command", "cwd", "listener"}
        if not required_service.issubset(item):
            raise MalformedResponse("Mother service lacks process/listener binding", {})
        port = item["port"]
        if isinstance(port, bool) or not isinstance(port, int) or port <= 0 or port in seen_ports:
            raise MalformedResponse("Mother service port is invalid or duplicated", {})
        if item["status"] != 200:
            raise MalformedResponse("Mother dependency is not ready", {"port": port})
        _require_sha256(item["body_sha256"], "Mother response body")
        if isinstance(item["pid"], bool) or not isinstance(item["pid"], int) or item["pid"] <= 0:
            raise ProvenanceInvalid("Mother service PID is invalid", {"port": port})
        if not all(isinstance(item[k], str) and item[k] for k in ("command", "cwd", "listener")):
            raise ProvenanceInvalid("Mother service process binding is incomplete", {"port": port})
        seen_ports.add(port)
        normalized.append(dict(item))
    return {
        "services": normalized,
        "mutation_path": value["mutation_path"],
        "binding": {
            "cockpit_sha": value["cockpit_sha"],
            "cycle_id": value["cycle_id"],
            "timestamp": value["timestamp"],
            "seal": value["seal"],
        },
    }


def validate_qdrant_verification(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = _require_mapping(payload, "Qdrant verification")
    keys = {
        "collection", "endpoint_class", "server_identity", "container", "image",
        "count_before", "count_after", "point_id_hash", "manifest_id", "manifest_hash",
        "request_hashes", "response_hashes", "mutation_attempted",
    }
    _require_exact_keys(value, keys, "Qdrant verification")
    if value["collection"] != QDRANT_COLLECTION:
        raise QdrantCollectionMismatch("Qdrant collection identity mismatch", {})
    if value["count_before"] != QDRANT_POINT_BASELINE:
        raise QdrantCountMismatch("Qdrant point-count baseline mismatch", {})
    if value["count_after"] != value["count_before"]:
        raise QdrantCountChanged("Qdrant count changed during read-only verification", {})
    if value["mutation_attempted"] is not False:
        raise UnsafeCapability("Qdrant verification attempted mutation", {})
    if value["manifest_id"] != QDRANT_MANIFEST_ID:
        raise QdrantPayloadProvenanceMismatch("Qdrant manifest identity mismatch", {})
    for key in ("point_id_hash", "manifest_hash"):
        try:
            _require_sha256(value[key], f"Qdrant {key}")
        except ProvenanceInvalid as exc:
            raise QdrantPayloadProvenanceMismatch("Qdrant point/manifest provenance is invalid", {}) from exc
    if not isinstance(value["request_hashes"], list) or not isinstance(value["response_hashes"], list):
        raise MalformedResponse("Qdrant request/response hashes must be arrays", {})
    for digest in [*value["request_hashes"], *value["response_hashes"]]:
        _require_sha256(digest, "Qdrant request/response")
    return dict(value)


def validate_iar_health(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = _require_mapping(payload, "IAR health")
    _require_exact_keys(value, IAR_HEALTH_KEYS, "IAR health")
    if not isinstance(value["enabled"], bool) or not isinstance(value["backends"], Mapping):
        raise MalformedResponse("IAR health types are invalid", {})
    if value["active_backend"] is not None and not isinstance(value["active_backend"], str):
        raise MalformedResponse("IAR active_backend is invalid", {})
    if value["active_model"] is not None and not isinstance(value["active_model"], str):
        raise MalformedResponse("IAR active_model is invalid", {})
    if value["degraded_reason"] is not None and not isinstance(value["degraded_reason"], str):
        raise MalformedResponse("IAR degraded_reason is invalid", {})
    if any(not isinstance(name, str) or not isinstance(ready, bool) for name, ready in value["backends"].items()):
        raise MalformedResponse("IAR backend health is invalid", {})
    return dict(value)


def validate_iar_provenance(payload: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    value = _require_mapping(payload, "IAR provenance response")
    _require_exact_keys(value, {"provenance_intact"}, "IAR provenance response")
    if value["provenance_intact"] is not True:
        raise ProvenanceInvalid("IAR provenance is not intact", {})
    bound = _require_mapping(binding, "IAR external identity binding")
    required = {
        "pid", "command", "cwd", "repo_sha", "ledger_path", "ledger_line_count",
        "ledger_sha256_prefix", "genesis_continuity",
    }
    _require_exact_keys(bound, required, "IAR external identity binding")
    if bound["ledger_line_count"] != 143 or not str(bound["ledger_sha256_prefix"]).startswith("fae0db89"):
        raise ProvenanceInvalid("IAR ledger baseline mismatch", {})
    if bound["genesis_continuity"] is not True:
        raise ProvenanceInvalid("IAR ledger is not GENESIS-rooted", {})
    if not isinstance(bound["ledger_path"], str) or not bound["ledger_path"].startswith("/"):
        raise ProvenanceInvalid("IAR ledger binding is not absolute", {})
    return {"provenance_intact": True, "binding": dict(bound)}


def validate_aegis_world(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = _require_mapping(payload, "AEGIS world opinion")
    keys = {
        "world", "has_compliance_contract", "compliance_required", "valid_for_composition",
        "verdict", "hard_conflicts", "soft_notes", "advisories", "offending_nodes", "summary",
    }
    _require_exact_keys(value, keys, "AEGIS world opinion")
    if value["verdict"] not in AEGIS_VERDICTS:
        raise MalformedResponse("AEGIS verdict is invalid", {})
    if not isinstance(value["world"], str) or not value["world"] or "/" in value["world"]:
        raise UnsafeCapability("AEGIS world must be an allowlisted identifier, never a path", {})
    for key in ("has_compliance_contract", "compliance_required", "valid_for_composition"):
        if not isinstance(value[key], bool):
            raise MalformedResponse("AEGIS boolean field is invalid", {"field": key})
    for key in ("hard_conflicts", "soft_notes", "advisories", "offending_nodes"):
        if not isinstance(value[key], list):
            raise MalformedResponse("AEGIS list field is invalid", {"field": key})
    return dict(value)


GOVERNANCE_HEALTH_REQUIRED = frozenset({"ok", "service"})


def validate_governance_health(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Required-key SUBSET validation for the promoted UP-2 Governance /health.

    Every required field must be present with the correct type/value; additional
    documented health metadata (e.g. version, uptime) is PERMITTED and does not
    fail validation.  Unknown data never grants authority: only the trusted
    required fields are returned, so downstream code cannot elevate an extra key
    into an authority-bearing claim.  Missing or malformed required fields still
    fail closed.
    """
    value = _require_mapping(payload, "Governance health")
    missing = GOVERNANCE_HEALTH_REQUIRED - set(value)
    if missing:
        raise MalformedResponse("Governance health missing required field(s)", {"missing": sorted(missing)})
    if value["ok"] is not True:
        raise MalformedResponse("Governance health 'ok' must be boolean true", {"ok": value.get("ok")})
    if value["service"] != "autonomous-governance-core":
        raise MalformedResponse("Governance service identity is invalid", {"service": value.get("service")})
    # Return only the trusted required fields — extra metadata is permitted on the
    # wire but is never carried forward as authority.
    return {"ok": True, "service": "autonomous-governance-core"}


def validate_governance_decision(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Exact-key validation for the promoted UP-2 Governance decision.

    The UP-2 authority reference (``policy_id``/``policy_version``) is REQUIRED:
    every promoted Governance decision carries the identity and content hash of
    the ruleset that produced it.  Both are observed evidence only — they bind
    the decision to its policy for receipts and audit, and are never converted
    into execution authority.  Any other additional field still fails closed.
    """
    value = _require_mapping(payload, "Governance decision")
    required = {
        "action_id", "decision", "risk_score", "thresholds", "reasons",
        "required_next_steps", "audit_id", "action", "created_at",
        "policy_id", "policy_version",
    }
    _require_exact_keys(value, required, "Governance decision")
    if value["decision"] not in GOVERNANCE_DECISIONS:
        raise MalformedResponse("Governance decision vocabulary is invalid", {})
    if isinstance(value["risk_score"], bool) or not isinstance(value["risk_score"], (int, float)):
        raise MalformedResponse("Governance risk score is invalid", {})
    if not isinstance(value["policy_id"], str) or not value["policy_id"].strip():
        raise MalformedResponse("Governance policy_id must be a non-empty string", {})
    _require_sha256(value["policy_version"], "Governance policy_version")
    action = _require_mapping(value["action"], "Governance echoed action")
    if "policy" in action:
        raise UnsafeCapability("caller policy override is forbidden", {})
    return dict(value)


def validate_axon_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Endpoint-specific validation of the promoted AXON gateway GET /health.

    The reviewed schema is the v2.4.0-p1.1 health aggregation served by the
    promoted gateway f3a94d1a: nine required fields, two optional aliases,
    exact nested key sets.  This is NOT a superset allowance — an unknown
    field, top-level or nested, still fails closed.

    Explicit normalization boundary: only the canonical identity/health
    evidence is carried forward — status, version, the IAR-authority inference
    health, the governance service flag, and degraded_reason.  The governed-
    execution surface flags (``execution_available``, ``services``, ``bonsai``,
    ``uptime``) are validated on the wire as health evidence but are NOT
    normalized into the receipt: nothing in this response is ever converted
    into execution or mutation authority.
    """
    value = _require_mapping(payload, "AXON identity")
    unknown = set(value) - AXON_HEALTH_REQUIRED_KEYS - AXON_HEALTH_OPTIONAL_KEYS
    if unknown:
        raise MalformedResponse("AXON health has unreviewed field(s)", {"unexpected": sorted(unknown)})
    missing = AXON_HEALTH_REQUIRED_KEYS - set(value)
    if missing:
        raise MalformedResponse("AXON health missing required field(s)", {"missing": sorted(missing)})
    if value["status"] not in {"healthy", "degraded", "down"} or value["version"] != "2.4.0-p1.1":
        raise ProvenanceInvalid("AXON identity/version mismatch", {})
    if isinstance(value["uptime"], bool) or not isinstance(value["uptime"], int) or value["uptime"] < 0:
        raise MalformedResponse("AXON uptime is invalid", {})
    services = _require_mapping(value["services"], "AXON services health")
    _require_exact_keys(services, AXON_HEALTH_SERVICE_KEYS, "AXON services health")
    if any(not isinstance(services[key], bool) for key in AXON_HEALTH_SERVICE_KEYS):
        raise MalformedResponse("AXON service health flags must be booleans", {})
    inference = _require_mapping(value["inference"], "AXON inference identity")
    _require_exact_keys(inference, AXON_INFERENCE_KEYS, "AXON inference identity")
    if inference["authority"] != "iar":
        raise ProvenanceInvalid("AXON inference authority must be IAR", {"authority": inference.get("authority")})
    if not isinstance(inference["healthy"], bool):
        raise MalformedResponse("AXON inference health is invalid", {})
    for key in ("provider", "model"):
        if inference[key] is not None and not isinstance(inference[key], str):
            raise MalformedResponse("AXON inference backend identity is invalid", {"field": key})
    bonsai = _require_mapping(value["bonsai"], "AXON bonsai health")
    _require_exact_keys(bonsai, {"healthy", "required"}, "AXON bonsai health")
    if not isinstance(bonsai["healthy"], bool) or bonsai["required"] is not False:
        raise MalformedResponse("AXON bonsai health is invalid", {})
    if not isinstance(value["execution_available"], bool):
        raise MalformedResponse("AXON execution availability is invalid", {})
    if value["bonsai_required"] is not False:
        raise MalformedResponse("AXON bonsai_required must be false", {})
    if value["status"] == "healthy":
        if value["degraded_reason"] is not None:
            raise MalformedResponse("AXON degraded_reason must be null when healthy", {})
    elif not isinstance(value["degraded_reason"], str) or not value["degraded_reason"]:
        raise MalformedResponse("AXON degraded_reason must explain a non-healthy status", {})
    if "inference_backend" in value and value["inference_backend"] != inference["provider"]:
        raise MalformedResponse("AXON inference_backend alias does not match inference.provider", {})
    if "status_reason" in value and (not isinstance(value["status_reason"], str) or not value["status_reason"]):
        raise MalformedResponse("AXON status_reason is invalid", {})
    return {
        "status": value["status"],
        "version": value["version"],
        "inference": {
            "authority": "iar",
            "provider": inference["provider"],
            "model": inference["model"],
            "healthy": inference["healthy"],
        },
        "governance_reachable": services["governance"],
        "degraded_reason": value["degraded_reason"],
    }


def validate_axon_tools(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = _require_mapping(payload, "AXON tools census")
    _require_exact_keys(value, {"tools"}, "AXON tools census")
    if not isinstance(value["tools"], list) or not all(isinstance(tool, str) for tool in value["tools"]):
        raise MalformedResponse("AXON tools census is invalid", {})
    tools = set(value["tools"])
    if "file_read" not in tools or not tools.issubset(AXON_TOOL_ALLOWLIST):
        raise UnsafeCapability("AXON exposes an unsafe or incomplete tool set", {"tools": sorted(tools)})
    return dict(value)


def validate_axon_governed_read(payload: Mapping[str, Any], *, expected_path: str) -> dict[str, Any]:
    value = _require_mapping(payload, "AXON governed read")
    required = {"taskId", "status", "governance", "subtasks", "path", "output_hash", "mutation"}
    _require_exact_keys(value, required, "AXON governed read")
    if value["status"] != "completed" or value["mutation"] is not False:
        raise UnsafeCapability("AXON governed read did not complete read-only", {})
    governance = _require_mapping(value["governance"], "AXON governance decision")
    if governance.get("decision") != "allow":
        raise UnsafeCapability("AXON governance did not allow the read", {})
    subtasks = value["subtasks"]
    if not isinstance(subtasks, list) or len(subtasks) != 1:
        raise MalformedResponse("AXON governed read must contain exactly one subtask", {})
    subtask = _require_mapping(subtasks[0], "AXON subtask")
    if subtask.get("tool") != "file_read" or subtask.get("status") != "succeeded":
        raise UnsafeCapability("AXON subtask is not a successful file_read", {})
    if value["path"] != expected_path or subtask.get("path") != expected_path:
        raise ProvenanceInvalid("AXON response path does not match the prevalidated path ID", {})
    _require_sha256(value["output_hash"], "AXON governed output")
    return dict(value)


def validate_axon_governed_evidence(result: AdapterResult, *, expected_path: str) -> dict[str, Any]:
    """Validate the governed payload plus its bounded unchained receipt wrapper."""
    if not validate_payload_hash(result):
        raise ProvenanceInvalid("AXON AdapterResult payload hash is invalid", {})
    if result.write_class != WriteClass.OPERATIONAL_METADATA.value:
        raise UnsafeCapability("AXON governed read must disclose its upstream metadata write", {})
    if len(result.upstream_writes) != 1:
        raise ProvenanceInvalid("AXON governed read requires exactly one upstream receipt reference", {})
    ref = OperationalWriteRef.from_mapping(result.upstream_writes[0])
    if ref.kind != "axon_gateway_receipt" or not result.evidence_refs:
        raise ProvenanceInvalid("AXON gateway receipt is absent or unbound", {})
    if not result.mission_id or not result.plan_id or not result.step_id:
        raise ProvenanceInvalid("AXON evidence lacks mission/plan/step binding", {})
    return validate_axon_governed_read(result.payload, expected_path=expected_path)


def validate_axon_traversal(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = _require_mapping(payload, "AXON traversal result")
    if value.get("ok") is not False:
        raise UnsafeCapability("AXON traversal probe unexpectedly succeeded", {})
    codes = value.get("error_codes")
    if not isinstance(codes, Sequence) or isinstance(codes, (str, bytes)):
        raise MalformedResponse("AXON traversal result lacks structured error codes", {})
    approved = [code for code in codes if code in AXON_CONTAINMENT_CODES]
    if len(codes) != 1 or len(approved) != 1:
        raise UnsafeCapability("AXON traversal rejection is unknown or ambiguous", {})
    return {"ok": False, "error_code": approved[0]}
