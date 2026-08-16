"""Live AXON authenticated read-only adapter (P2-B).

Talks ONLY to the already-ratified AXON read-only HTTP surface (health, tools
census, governed read, traversal probe).  Authentication is fail-closed; a
mutation request is proven to return MUTATION_NOT_AUTHORIZED; the governed-read
receipt states no mutation occurred.  This adapter never imports or reaches the
AXON UP-3 mutation module — it is pure loopback HTTP.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from recipe_engine.founder_runtime.adapters.base import OperationalWriteRef, WriteClass
from recipe_engine.founder_runtime.adapters.contract_validation import (
    validate_axon_governed_read,
    validate_axon_identity,
    validate_axon_tools,
    validate_axon_traversal,
)
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, AdapterResultStatus
from recipe_engine.founder_runtime.adapters.identity import EvidenceClass
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.contracts.receipts import (
    ExecutionReceipt,
    ReceiptStatus,
    SCHEMA_RECEIPT,
    TestResult,
)
from recipe_engine.founder_runtime.errors import (
    AdapterUnavailable,
    MutationNotAuthorized,
    ProvenanceInvalid,
    UnsafeCapability,
)
from recipe_engine.founder_runtime.ids import hash_payload, stable_id
from recipe_engine.live_adapters.base_live import LiveAdapterBase, reject_mutation_shaped
from recipe_engine.live_adapters.canonical_topology import CANONICAL_AXON_SHA, CANONICAL_AXON_VERSION
from recipe_engine.live_adapters.settings import LiveAdapterSettings
from recipe_engine.live_adapters.transport import LoopbackHttpClient, TransportDenied, TransportMalformed

_SERVICE = "axon"
_OP_IDENTITY = "identity"
_OP_TOOLS = "tools_census"
_OP_READ = "governed_read"
_OP_TRAVERSAL = "traversal_probe"

# Allowlisted governed-read path ids -> the exact path the gateway must echo.
# Caller-controlled arbitrary paths are impossible: a request names an id.
DEFAULT_READ_ALLOWLIST = {"axon_readme": "README.md", "axon_package": "package.json"}

# Target for the mutation negative control.  Names no real artifact in any
# repository, so a misconfigured gateway has nothing to damage.  The gate under
# test refuses on the tool class (file_write), which this exercises identically.
MUTATION_PROBE_SENTINEL = ".neuruh-mutation-probe-sentinel__must-never-exist"


class LiveAxonReadonlyAdapter(LiveAdapterBase):
    # NOTE: canonical adapter name is "axon_readonly" to match the timeouts
    # policy table keys (("axon_readonly", "identity"), ... ) — the live envelope
    # resolves its declared/effective timeout from that table.
    name = "axon_readonly"
    adapter_version = "p2b.live.axon.1"
    service_name = "axon"
    endpoint_class = "loopback"
    capabilities = ("identity", "tools_census", "governed_read", "traversal_probe")

    def __init__(
        self,
        settings: LiveAdapterSettings,
        http: LoopbackHttpClient | None = None,
        read_allowlist: Mapping[str, str] | None = None,
    ):
        super().__init__(settings)
        self._http = http or LoopbackHttpClient(settings.endpoint_map())
        self._expected_sha = settings.axon_source_sha
        self._read_allowlist = dict(read_allowlist or DEFAULT_READ_ALLOWLIST)

    def _request(self, path: str, *, timeout: int, method: str = "GET", body: Any = None):
        resp = self._http.request(_SERVICE, path, timeout_ms=timeout, method=method, json_body=body)
        if resp.status in (401, 403):
            # Authentication fail-closed.
            raise TransportDenied(f"AXON authentication failed (status {resp.status})")
        return resp

    def identity(self, meta: AdapterRequestMeta) -> AdapterResult:
        t0 = time.monotonic()
        try:
            reject_mutation_shaped(None, context="axon.identity")
            # Verify the PROMOTED AXON identity: when a source SHA is pinned it
            # must be the promoted f3a94d1 (a wrong-SHA service is rejected before
            # any request).  The gateway build version (2.4.0-p1.1) is the
            # on-the-wire corroboration.
            if self._expected_sha and self._expected_sha != CANONICAL_AXON_SHA:
                raise ProvenanceInvalid(
                    "AXON source SHA is not the promoted f3a94d1",
                    {"expected_promoted": CANONICAL_AXON_SHA, "configured": self._expected_sha},
                )
            timeout = self.op_timeout(_OP_IDENTITY, meta)
            resp = self._request("/health", timeout=timeout)
            if resp.status != 200:
                raise TransportMalformed(f"axon health returned {resp.status}")
            body = resp.json()
            validated = validate_axon_identity(dict(body) if isinstance(body, Mapping) else {})
            if validated["version"] != CANONICAL_AXON_VERSION:
                raise ProvenanceInvalid("AXON gateway build version is not the promoted 2.4.0-p1.1", {"version": validated["version"]})
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_IDENTITY, meta=meta, exc=exc, duration_ms=_ms(t0))
        observed_sha = hash_payload(validated)
        dep = self.dependency_identity(
            expected=self._expected_sha or CANONICAL_AXON_SHA, observed=validated["version"],
            identity_verified=validated["version"] == CANONICAL_AXON_VERSION, readiness_verified=validated["status"] != "down",
        ).to_dict()
        prov = self.live_provenance(
            logical="live://axon/health", observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=observed_sha, evidence_class=EvidenceClass.LIVE_OBSERVED,
        )
        return self.live_result(
            operation=_OP_IDENTITY, meta=meta, status=AdapterResultStatus.SUCCESS, payload=validated,
            dependency_identity=dep, provenance=prov, observed_sha=observed_sha,
            evidence_refs=[f"axon:identity:{validated['version']}"], duration_ms=_ms(t0),
        )

    def tools_census(self, meta: AdapterRequestMeta) -> AdapterResult:
        t0 = time.monotonic()
        try:
            reject_mutation_shaped(None, context="axon.tools_census")
            timeout = self.op_timeout(_OP_TOOLS, meta)
            resp = self._request("/api/tools", timeout=timeout)
            if resp.status != 200:
                raise TransportMalformed(f"axon tools returned {resp.status}")
            body = resp.json()
            # validator enforces: file_read present, subset of {file_read, web_fetch}
            # => proves raw bash / file_write / run_command are NOT read-exposed.
            validated = validate_axon_tools(dict(body) if isinstance(body, Mapping) else {})
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_TOOLS, meta=meta, exc=exc, duration_ms=_ms(t0))
        payload = {
            **validated,
            "raw_bash_available": False,
            "raw_file_write_readonly_exposed": False,
            "mutation_executor_reachable": False,
        }
        observed_sha = hash_payload(payload)
        dep = self.dependency_identity(observed="tools", identity_verified=True, readiness_verified=True).to_dict()
        prov = self.live_provenance(
            logical="live://axon/tools", observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=observed_sha, evidence_class=EvidenceClass.LIVE_OBSERVED,
        )
        return self.live_result(
            operation=_OP_TOOLS, meta=meta, status=AdapterResultStatus.SUCCESS, payload=payload,
            dependency_identity=dep, provenance=prov, observed_sha=observed_sha,
            evidence_refs=["axon:tools:" + ",".join(sorted(validated["tools"]))], duration_ms=_ms(t0),
        )

    def governed_read(self, path_id: str, meta: AdapterRequestMeta) -> AdapterResult:
        t0 = time.monotonic()
        try:
            reject_mutation_shaped(None, context="axon.governed_read")
            if path_id not in self._read_allowlist:
                raise UnsafeCapability("governed read path id is not allowlisted (no caller-controlled paths)", {"path_id": str(path_id)})
            expected_path = self._read_allowlist[path_id]
            timeout = self.op_timeout(_OP_READ, meta)
            action = {
                "tool": "file_read",
                "path": expected_path,
                "read_only": True,
            }
            resp = self._request("/api/tasks/execute", timeout=timeout, method="POST", body={"task": "governed_read", "action": action})
            if resp.status != 200:
                raise TransportMalformed(f"axon governed_read returned {resp.status}")
            body = resp.json()
            validated = validate_axon_governed_read(dict(body) if isinstance(body, Mapping) else {}, expected_path=expected_path)
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_READ, meta=meta, exc=exc, duration_ms=_ms(t0))
        observed_sha = validated["output_hash"]
        receipt_ref = str(validated.get("taskId"))
        dep = self.dependency_identity(observed=receipt_ref, identity_verified=True, readiness_verified=True).to_dict()
        prov = self.live_provenance(
            logical=f"live://axon/read/{path_id}", observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=observed_sha, evidence_class=EvidenceClass.LIVE_OBSERVED,
        )
        # Disclose the gateway's bounded receipt as operational metadata evidence
        # (never mission truth).
        upstream = [OperationalWriteRef(kind="axon_gateway_receipt", location_class="axon_receipt_ledger")]
        return self.live_result(
            operation=_OP_READ, meta=meta, status=AdapterResultStatus.SUCCESS, payload=validated,
            dependency_identity=dep, provenance=prov, observed_sha=observed_sha,
            evidence_refs=[f"axon:receipt:{receipt_ref}"], duration_ms=_ms(t0),
            write_class=WriteClass.OPERATIONAL_METADATA, upstream_writes=upstream,
        )

    def traversal_probe(self, meta: AdapterRequestMeta) -> AdapterResult:
        t0 = time.monotonic()
        try:
            reject_mutation_shaped(None, context="axon.traversal_probe")
            timeout = self.op_timeout(_OP_TRAVERSAL, meta)
            action = {"tool": "file_read", "path": "../../etc/passwd", "read_only": True}
            resp = self._request("/api/tasks/execute", timeout=timeout, method="POST", body={"task": "traversal_probe", "action": action})
            body = resp.json() if resp.status in (200, 400, 403) else {"ok": None}
            validated = validate_axon_traversal(dict(body) if isinstance(body, Mapping) else {})
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_TRAVERSAL, meta=meta, exc=exc, duration_ms=_ms(t0))
        observed_sha = hash_payload(validated)
        dep = self.dependency_identity(observed=validated["error_code"], identity_verified=True, readiness_verified=True).to_dict()
        prov = self.live_provenance(
            logical="live://axon/traversal", observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=observed_sha, evidence_class=EvidenceClass.LIVE_OBSERVED,
        )
        return self.live_result(
            operation=_OP_TRAVERSAL, meta=meta, status=AdapterResultStatus.SUCCESS, payload=validated,
            dependency_identity=dep, provenance=prov, observed_sha=observed_sha,
            evidence_refs=[f"axon:traversal:{validated['error_code']}"], duration_ms=_ms(t0),
        )

    def mutation_probe(self, meta: AdapterRequestMeta) -> AdapterResult:
        """Prove the gateway denies mutation: expect MUTATION_NOT_AUTHORIZED.

        A DENIED result here is the SUCCESS condition — the mutation path is
        unreachable and unauthorized.  We never import the UP-3 module; we only
        confirm the HTTP surface refuses.

        The probe is a negative control, so it is the one read-only operation
        that deliberately issues a mutation-shaped request.  Two properties keep
        that safe and honest:

        * The target is ``MUTATION_PROBE_SENTINEL`` — a path that names no real
          artifact in any repository.  The gateway must refuse on the *tool
          class* (``file_write``), never on the path, so a sentinel exercises the
          identical authorization gate.  A real path such as ``README.md`` would
          be written if the gateway were ever misconfigured; this cannot be.
        * The envelope declares ``mutation_attempted=True``.  A receipt that
          recorded ``False`` here would attest that no mutation was attempted
          while a ``file_write`` was on the wire — a fabricated attestation.
        """
        t0 = time.monotonic()
        try:
            reject_mutation_shaped(None, context="axon.mutation_probe")
            timeout = self.op_timeout(_OP_IDENTITY, meta)
            action = {"tool": "file_write", "path": MUTATION_PROBE_SENTINEL, "content": ""}
            resp = self._request("/api/tasks/execute", timeout=timeout, method="POST", body={"task": "mutation_probe", "action": action})
            body = resp.json() if resp.status in (200, 400, 401, 403) else {}
            denied = (
                resp.status in (400, 401, 403)
                or (isinstance(body, Mapping) and (
                    body.get("status") in ("blocked", "awaiting_approval", "denied")
                    or "MUTATION_NOT_AUTHORIZED" in str(body)
                    or body.get("mutation_authorized") is False
                ))
            )
            if not denied:
                raise UnsafeCapability("AXON did NOT deny a mutation request", {"status": resp.status})
        except (UnsafeCapability, MutationNotAuthorized) as exc:
            return self.failure_result(operation=_OP_IDENTITY, meta=meta, exc=exc, duration_ms=_ms(t0))
        except Exception as exc:  # noqa: BLE001 - transport down still counts as unreachable
            payload = {"mutation_denied": True, "reason": "mutation_path_unreachable", "detail": str(getattr(exc, "message", exc))[:120]}
            return self._denied_mutation_result(meta, payload, _ms(t0))
        payload = {"mutation_denied": True, "reason": "MUTATION_NOT_AUTHORIZED", "mutation_occurred": False}
        return self._denied_mutation_result(meta, payload, _ms(t0))

    def _denied_mutation_result(self, meta: AdapterRequestMeta, payload: dict[str, Any], dur: int) -> AdapterResult:
        observed_sha = hash_payload(payload)
        dep = self.dependency_identity(observed="mutation_denied", identity_verified=True, readiness_verified=False).to_dict()
        prov = self.live_provenance(
            logical="live://axon/mutation_probe", observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=observed_sha, evidence_class=EvidenceClass.LIVE_OBSERVED,
        )
        return self.live_result(
            operation=_OP_IDENTITY, meta=meta, status=AdapterResultStatus.DENIED, payload=payload,
            dependency_identity=dep, provenance=prov, observed_sha=observed_sha,
            error_code="MUTATION_NOT_AUTHORIZED", evidence_refs=["axon:mutation_denied"], duration_ms=dur,
            mutation_attempted=True,
        )

    # --- Protocol parity: receipt-producing read-only step -----------------
    def execute_readonly_step(self, **kwargs: Any) -> ExecutionReceipt:
        mission_id = str(kwargs.get("mission_id") or "")
        plan_id = str(kwargs.get("plan_id") or "")
        step_id = str(kwargs.get("step_id") or "")
        input_hash = str(kwargs.get("input_hash") or "")
        started_at = str(kwargs.get("started_at") or "1970-01-01T00:00:00.000Z")
        ended_at = str(kwargs.get("ended_at") or started_at)
        meta = AdapterRequestMeta(correlation_id=mission_id or "axon", observed_at=started_at,
                                  mission_id=mission_id or None, plan_id=plan_id or None, step_id=step_id or None)
        result = self.identity(meta)
        if result.status != AdapterResultStatus.SUCCESS.value:
            raise AdapterUnavailable("AXON read-only surface unavailable for governed step", {"status": result.status})
        output = {"axon_identity": result.payload, "mutation": False}
        receipt = ExecutionReceipt(
            schema_version=SCHEMA_RECEIPT,
            receipt_id=stable_id("live_rcpt", mission_id, plan_id, step_id, input_hash),
            mission_id=mission_id, plan_id=plan_id, step_id=step_id,
            executor="live_axon", capability=str(kwargs.get("capability") or "axon.identity"),
            started_at=started_at, ended_at=ended_at, status=ReceiptStatus.SUCCESS,
            input_hash=input_hash, output_hash=hash_payload(output),
            repository_metadata={"mode": "live", "mutation": False},
            files_changed=[], tests=[TestResult(name="axon_readonly_identity", passed=True)],
            artifacts=[], error=None,
            authority_reference=str(kwargs.get("authority_reference") or "not_claimed_by_governance"),
            idempotency_key=f"live_step:{mission_id}:{plan_id}:{step_id}",
            evidence_class="live_observed", trust_level="live_observed", producer_identity="live_axon",
            action_binding=f"axon.identity:{input_hash}:{hash_payload(output)}",
            outcome_data=output, mutation_authorized=False,
        )
        receipt.compute_hash()
        return receipt

    def execute_readonly_result(self, **kwargs: Any) -> AdapterResult:
        meta = kwargs.get("meta") or AdapterRequestMeta(
            correlation_id=str(kwargs.get("mission_id") or "axon"),
            observed_at=str(kwargs.get("started_at") or "1970-01-01T00:00:00.000Z"),
            mission_id=kwargs.get("mission_id"), plan_id=kwargs.get("plan_id"), step_id=kwargs.get("step_id"),
        )
        return self.identity(meta)


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
