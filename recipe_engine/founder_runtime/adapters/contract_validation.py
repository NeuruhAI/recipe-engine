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


def validate_governance_health(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = _require_mapping(payload, "Governance health")
    _require_exact_keys(value, {"ok", "service"}, "Governance health")
    if value != {"ok": True, "service": "autonomous-governance-core"}:
        raise MalformedResponse("Governance health identity is invalid", {})
    return dict(value)


def validate_governance_decision(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = _require_mapping(payload, "Governance decision")
    required = {
        "action_id", "decision", "risk_score", "thresholds", "reasons",
        "required_next_steps", "audit_id", "action", "created_at",
    }
    _require_exact_keys(value, required, "Governance decision")
    if value["decision"] not in GOVERNANCE_DECISIONS:
        raise MalformedResponse("Governance decision vocabulary is invalid", {})
    if isinstance(value["risk_score"], bool) or not isinstance(value["risk_score"], (int, float)):
        raise MalformedResponse("Governance risk score is invalid", {})
    action = _require_mapping(value["action"], "Governance echoed action")
    if "policy" in action:
        raise UnsafeCapability("caller policy override is forbidden", {})
    return dict(value)


def validate_axon_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = _require_mapping(payload, "AXON identity")
    keys = {"status", "version", "inference", "execution_available", "degraded_reason"}
    _require_exact_keys(value, keys, "AXON identity")
    if value["status"] not in {"healthy", "degraded", "down"} or value["version"] != "2.4.0-p1.1":
        raise ProvenanceInvalid("AXON identity/version mismatch", {})
    inference = _require_mapping(value["inference"], "AXON inference identity")
    _require_exact_keys(inference, {"authority", "provider", "model", "health"}, "AXON inference identity")
    if not isinstance(value["execution_available"], bool):
        raise MalformedResponse("AXON execution availability is invalid", {})
    return dict(value)


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
