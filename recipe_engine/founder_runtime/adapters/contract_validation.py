"""Pure P2-A.1 response-shape validators for future live adapters.

These functions accept already-observed mappings.  They perform no file,
process, environment, database, or network I/O.
"""

from __future__ import annotations

import hashlib
import json
import math
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

# --- CURRENT RATIFIED CANON (Gate B, 2026-08-16) -----------------------------
# The previous pin (neuruh_vault_canon / 3049 points / manifest
# vault-canon-remediated-20260718T054050Z-cct22) was retired in July: that
# collection no longer exists, and the adapter correctly failed closed rather
# than substituting a fixture.
#
# These constants ARE the ratified attestation, not a hopeful rename. Each was
# re-proved live against the running service by scripts/qdrant_canon_ratify.py,
# which scans every point rather than sampling, and gates relevance on
# ground-truthed self-retrieval rather than an absolute cosine score (a random
# hex string outscores a genuine canon question on this model, so a score
# threshold proves nothing). QDRANT_ATTESTATION_IDENTITY_HASH is the SHA-256 of
# the canonical identity block in that attestation, so a reader can recompute it
# and see whether the live corpus still matches what was ratified.
QDRANT_COLLECTION = "neuruh_canon_v20260816"
QDRANT_POINT_BASELINE = 11756
QDRANT_VECTOR_SIZE = 768
QDRANT_DISTANCE = "Cosine"
QDRANT_EMBEDDING_MODEL = "nomic-embed-text"
QDRANT_SOURCE_TREE = "neuruh-vault"
QDRANT_ATTESTATION_SCHEMA = "fr.qdrant_canon_attestation.v1"
QDRANT_ATTESTATION_IDENTITY_HASH = (
    "438900865717650c801cc3bc0245beaa3df24419d4dbde94673714b5dc8efe9d"
)
QDRANT_SCROLL_REQUEST = {"limit": 1, "with_payload": True, "with_vector": False}
IAR_HEALTH_KEYS = frozenset(
    {"enabled", "active_backend", "active_model", "backends", "degraded_reason"}
)
# --- IAR append-only provenance ledger contract (P2-B re-ratification) ------
# The ledger is the IAR service's single-writer, hash-chained JSONL file.  Its
# ratified baseline (the first IAR_LEDGER_BASELINE_COUNT records) is anchored
# by a FULL sha256; growth beyond the baseline is legitimate if and only if
# every record chains from the literal GENESIS root and every post-baseline
# record satisfies the reviewed local-only provider policy.  The CURRENT total
# and head hash are observed evidence, never a hard-coded ceiling.
IAR_LEDGER_GENESIS = "GENESIS"
IAR_LEDGER_BASELINE_COUNT = 143
IAR_LEDGER_BASELINE_SHA256 = "fae0db894e7af106c265671953134fe76d707710e5be0cd9f8e7479fedaec660"
# One record per service `prov.write` call (iar_v2.py /route): exactly these keys.
IAR_LEDGER_RECORD_KEYS = frozenset(
    {
        "backend", "decision_reason", "entry_hash", "est_cost_usd", "fallback_from",
        "finish_reason", "input_sha", "kind", "latency_ms", "model", "output_sha",
        "prev_hash", "request_id", "truncated", "ts",
    }
)
# Local backend identities from the ratified IAR candidate 0f427703
# (_default_backends, kind == "local").  claude-cloud is kind "cloud" and is
# NEVER a permitted post-baseline ledger writer under the P2-B local-only mandate.
IAR_LEDGER_LOCAL_BACKENDS = frozenset({"ollama-llama3.2", "bonsai-8b", "gemma-4-e4b"})
IAR_LEDGER_MAX_BYTES = 64 * 1024 * 1024  # bounded-read sanity ceiling; larger fails closed
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
        "count_before", "count_after", "point_id_hash",
        "vector_size", "distance", "embedding_model", "embedding_dim", "source_tree",
        "attestation_identity_hash",
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
    # The retired pin bound provenance to a manifest_id the current corpus does
    # not carry. Binding is now to the retrieval identity itself: the geometry
    # the vectors were built in, the model that built them, and the tree they
    # came from. A collection of the right name holding differently-embedded
    # vectors would pass a name check and fail these.
    if value["vector_size"] != QDRANT_VECTOR_SIZE:
        raise QdrantPayloadProvenanceMismatch("Qdrant vector dimension mismatch", {})
    if value["distance"] != QDRANT_DISTANCE:
        raise QdrantPayloadProvenanceMismatch("Qdrant distance metric mismatch", {})
    if value["embedding_model"] != QDRANT_EMBEDDING_MODEL:
        raise QdrantPayloadProvenanceMismatch("Qdrant embedding model mismatch", {})
    if value["embedding_dim"] != QDRANT_VECTOR_SIZE:
        raise QdrantPayloadProvenanceMismatch(
            "Qdrant payload-declared embedding dimension disagrees with the collection", {})
    if value["source_tree"] != QDRANT_SOURCE_TREE:
        raise QdrantPayloadProvenanceMismatch("Qdrant corpus source tree mismatch", {})
    if value["attestation_identity_hash"] != QDRANT_ATTESTATION_IDENTITY_HASH:
        raise QdrantPayloadProvenanceMismatch("Qdrant canon attestation identity mismatch", {})
    try:
        _require_sha256(value["point_id_hash"], "Qdrant point_id_hash")
    except ProvenanceInvalid as exc:
        raise QdrantPayloadProvenanceMismatch("Qdrant point provenance is invalid", {}) from exc
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


def validate_iar_provenance(
    payload: Mapping[str, Any],
    binding: Mapping[str, Any],
    *,
    baseline_count: int = IAR_LEDGER_BASELINE_COUNT,
) -> dict[str, Any]:
    """Wire-shape validation of GET /provenance (append-only re-ratification).

    The ledger baseline is no longer a frozen total: the binding must report AT
    LEAST ``baseline_count`` records and a well-formed 16-hex head-hash prefix.
    The reported count/prefix are the service's head receipt — observed
    evidence that the caller (LiveIARAdapter.verify_provenance) must cross-check
    against its own independent full-chain validation of the ledger file
    (validate_iar_ledger_continuity).  Neither value is ever a hard-coded
    ceiling.
    """
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
    count = bound["ledger_line_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < baseline_count:
        raise ProvenanceInvalid(
            "IAR ledger is below the ratified baseline",
            {"reported": count, "baseline_count": baseline_count},
        )
    prefix = bound["ledger_sha256_prefix"]
    if not isinstance(prefix, str) or not re.fullmatch(r"[0-9a-f]{16}", prefix):
        raise ProvenanceInvalid("IAR ledger head-hash prefix is malformed", {})
    if bound["genesis_continuity"] is not True:
        raise ProvenanceInvalid("IAR ledger is not GENESIS-rooted", {})
    if not isinstance(bound["ledger_path"], str) or not bound["ledger_path"].startswith("/"):
        raise ProvenanceInvalid("IAR ledger binding is not absolute", {})
    return {"provenance_intact": True, "binding": dict(bound)}


def validate_iar_ledger_continuity(
    ledger_bytes: bytes,
    *,
    baseline_count: int,
    baseline_sha256: str,
) -> dict[str, Any]:
    """Continuity-anchored append-only validation of the raw IAR ledger bytes.

    Pure function (no I/O): the caller reads the file.  Fail-closed invariants:

    - the ratified baseline (first ``baseline_count`` records) is byte-for-byte
      anchored by the FULL ``baseline_sha256`` of its lines;
    - every record from the literal GENESIS root through the current head has
      valid deterministic hash linkage (the IAR service's own algorithm:
      entry_hash = sha256(prev_hash + compact-sorted-json(record sans
      entry_hash)));
    - every record carries exactly the reviewed 15-key schema;
    - every POST-baseline record satisfies the local-only provider policy
      (kind local, allowlisted backend, zero cost, no OpenRouter reference);
    - the current total and head hash are computed and RETURNED as observed
      evidence — never compared against a hard-coded current value, so
      legitimate local append-only growth keeps validating without code change.
    """
    if isinstance(baseline_count, bool) or not isinstance(baseline_count, int) or baseline_count < 1:
        raise ProvenanceInvalid("IAR ledger baseline anchor count is not configured", {})
    if not isinstance(baseline_sha256, str) or not SHA256_RE.fullmatch(baseline_sha256):
        raise ProvenanceInvalid("IAR ledger baseline anchor hash is not configured", {})
    if not isinstance(ledger_bytes, bytes) or not ledger_bytes:
        raise ProvenanceInvalid("IAR ledger is empty or unreadable", {})
    if len(ledger_bytes) > IAR_LEDGER_MAX_BYTES:
        raise ProvenanceInvalid("IAR ledger exceeds the bounded-read ceiling", {"bytes": len(ledger_bytes)})
    if b"\r" in ledger_bytes:
        raise ProvenanceInvalid("IAR ledger contains carriage returns", {})
    if not ledger_bytes.endswith(b"\n"):
        raise ProvenanceInvalid("IAR ledger final record is truncated", {})
    raw_lines = ledger_bytes.split(b"\n")
    if raw_lines[-1] != b"":
        raise ProvenanceInvalid("IAR ledger final record is truncated", {})
    lines = raw_lines[:-1]
    if any(not ln.strip() for ln in lines):
        raise ProvenanceInvalid("IAR ledger contains blank lines", {})
    count = len(lines)
    if count < baseline_count:
        raise ProvenanceInvalid(
            "IAR ledger has fewer records than the ratified baseline",
            {"records": count, "baseline_count": baseline_count},
        )
    baseline_blob = b"".join(ln + b"\n" for ln in lines[:baseline_count])
    observed_baseline = hashlib.sha256(baseline_blob).hexdigest()
    if observed_baseline != baseline_sha256:
        raise ProvenanceInvalid(
            "IAR ledger ratified baseline has been altered",
            {"observed": observed_baseline[:16], "anchored": baseline_sha256[:16]},
        )
    prev = IAR_LEDGER_GENESIS
    seen_request_ids: set[str] = set()
    seen_entry_hashes: set[str] = set()
    for index, raw in enumerate(lines, 1):
        try:
            record = json.loads(raw)
        except ValueError as exc:
            raise MalformedResponse("IAR ledger record is not valid JSON", {"record": index}) from exc
        if not isinstance(record, dict):
            raise MalformedResponse("IAR ledger record is not an object", {"record": index})
        if set(record) != IAR_LEDGER_RECORD_KEYS:
            raise ProvenanceInvalid(
                "IAR ledger record has an invalid field set",
                {"record": index, "unexpected": sorted(set(record) ^ IAR_LEDGER_RECORD_KEYS)},
            )
        entry_hash = record["entry_hash"]
        if not isinstance(entry_hash, str) or not SHA256_RE.fullmatch(entry_hash):
            raise ProvenanceInvalid("IAR ledger entry hash is malformed", {"record": index})
        if record["prev_hash"] != prev:
            raise ProvenanceInvalid(
                "IAR ledger chain linkage is broken", {"record": index}
            )
        body = {k: v for k, v in record.items() if k != "entry_hash"}
        preimage = record["prev_hash"] + json.dumps(body, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(preimage.encode()).hexdigest() != entry_hash:
            raise ProvenanceInvalid("IAR ledger entry hash is invalid", {"record": index})
        if entry_hash in seen_entry_hashes:
            raise ProvenanceInvalid("IAR ledger entry hash is replayed", {"record": index})
        seen_entry_hashes.add(entry_hash)
        request_id = record["request_id"]
        if not isinstance(request_id, str) or not request_id:
            raise MalformedResponse("IAR ledger request identity is malformed", {"record": index})
        if request_id in seen_request_ids:
            raise ProvenanceInvalid("IAR ledger request identity is replayed", {"record": index})
        seen_request_ids.add(request_id)
        ts = record["ts"]
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts) or ts <= 0:
            raise MalformedResponse("IAR ledger timestamp is malformed", {"record": index})
        if index > baseline_count:
            _require_iar_local_record(record, index)
        prev = entry_hash
    return {
        "record_count": count,
        "baseline_count": baseline_count,
        "baseline_sha256": baseline_sha256,
        "ledger_sha256": hashlib.sha256(ledger_bytes).hexdigest(),
        "head_entry_hash": prev,
    }


def _require_iar_local_record(record: Mapping[str, Any], index: int) -> None:
    """Reviewed local-only provider policy for every post-baseline record."""
    if record["kind"] != "local":
        raise ProvenanceInvalid(
            "IAR ledger post-baseline record is not local",
            {"record": index, "kind": record.get("kind")},
        )
    backend = record["backend"]
    if not isinstance(backend, str) or backend not in IAR_LEDGER_LOCAL_BACKENDS:
        raise ProvenanceInvalid(
            "IAR ledger post-baseline backend is not an approved local backend",
            {"record": index, "backend": backend if isinstance(backend, str) else type(backend).__name__},
        )
    model = record["model"]
    if not isinstance(model, str) or not model:
        raise MalformedResponse("IAR ledger post-baseline model identity is malformed", {"record": index})
    reason = record["decision_reason"]
    if not isinstance(reason, str) or not reason:
        raise MalformedResponse("IAR ledger post-baseline decision reason is malformed", {"record": index})
    for field in (backend, model, reason):
        if "openrouter" in field.lower():
            raise ProvenanceInvalid(
                "IAR ledger post-baseline record references OpenRouter",
                {"record": index},
            )
    cost = record["est_cost_usd"]
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost != 0:
        raise ProvenanceInvalid(
            "IAR ledger post-baseline record carries non-zero or malformed provider cost",
            {"record": index},
        )
    latency = record["latency_ms"]
    if isinstance(latency, bool) or not isinstance(latency, int) or latency < 0:
        raise MalformedResponse("IAR ledger post-baseline latency evidence is malformed", {"record": index})
    for key in ("input_sha", "output_sha"):
        digest = record[key]
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise MalformedResponse(
                "IAR ledger post-baseline usage evidence is malformed",
                {"record": index, "field": key},
            )
    if not isinstance(record["finish_reason"], str) or not record["finish_reason"]:
        raise MalformedResponse("IAR ledger post-baseline finish reason is malformed", {"record": index})
    if not isinstance(record["truncated"], bool):
        raise MalformedResponse("IAR ledger post-baseline truncation flag is malformed", {"record": index})
    fallback = record["fallback_from"]
    if fallback is not None and (
        not isinstance(fallback, list) or any(not isinstance(item, str) for item in fallback)
    ):
        raise MalformedResponse("IAR ledger post-baseline fallback evidence is malformed", {"record": index})


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


AUTHORIZATION_DECISIONS = frozenset({"allow", "deny", "founder_required"})


def validate_governance_authorization(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Required-key validation for a Governance V2 authorization envelope.

    An authorization is the strongest answer this runtime ever asks for, so the
    shape of the answer is checked before any field of it is believed.  A reply
    that is missing its decision, its reason, its receipt hash or its execution
    marker is not a weak allow — it is an answer we cannot read, and an answer
    we cannot read is a denial.

    ``execution`` must still say NOT_CLAIMED_BY_GOVERNANCE.  Governance decides;
    it never executes, and an envelope claiming otherwise is refused rather than
    quietly accepted as a stronger permission than the contract grants.
    """
    value = _require_mapping(payload, "Governance authorization")
    required = {
        "schema_version", "authorization_id", "decision", "reason_code",
        "reasons", "risk_tier", "canonical_facts_hash", "decision_hash",
        "execution", "issued_at", "expires_at", "approver_ids", "evidence_refs",
    }
    missing = required - set(value)
    if missing:
        raise MalformedResponse(
            "Governance authorization missing required field(s)", {"missing": sorted(missing)}
        )
    if value["schema_version"] != "gov.authorization.v1":
        raise MalformedResponse(
            "Governance authorization schema version is unknown",
            {"schema_version": value.get("schema_version")},
        )
    if value["decision"] not in AUTHORIZATION_DECISIONS:
        raise MalformedResponse(
            "Governance authorization decision vocabulary is invalid",
            {"decision": value.get("decision")},
        )
    if not isinstance(value["reason_code"], str) or not value["reason_code"].strip():
        raise MalformedResponse("Governance authorization reason_code must be a non-empty string", {})
    if value["execution"] != "NOT_CLAIMED_BY_GOVERNANCE":
        raise UnsafeCapability(
            "Governance authorization claims execution", {"execution": value.get("execution")}
        )
    _require_sha256(value["decision_hash"], "Governance authorization decision_hash")
    if value["decision"] == "allow":
        # Only an allow needs a durable receipt: a denial that could not be
        # persisted is already refused on the Governance side.
        _require_sha256(value["canonical_facts_hash"], "Governance authorization canonical_facts_hash")
        if not isinstance(value.get("decision_receipt_hash"), str) or not value["decision_receipt_hash"].strip():
            raise MalformedResponse("Governance allow carries no decision receipt hash", {})
    if not isinstance(value["approver_ids"], list) or not all(isinstance(a, str) for a in value["approver_ids"]):
        raise MalformedResponse("Governance authorization approver_ids must be a list of strings", {})
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
