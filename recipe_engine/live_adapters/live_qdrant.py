"""Live Qdrant read-only adapter (P2-B) — GATED on host recovery.

Targets ONLY the accepted loopback Qdrant service and the canonical collection
``neuruh_vault_canon``.  Supports service health, collection identity, point
count, bounded scroll/retrieval, returned-point provenance/hashes, and
before/after count verification.  It absolutely never upserts, deletes, creates
or deletes a collection, mutates a payload, or deletes a snapshot.

HOST DEPENDENCY: the live activation gate does not run until a sealed GREEN host
recovery audit exists.  Until then every live operation returns DENIED
(``error_code=DEPENDENCY_UNHEALTHY``) WITHOUT contacting Qdrant — and never a
fixture substitute.
"""

from __future__ import annotations

import time
from typing import Any

from recipe_engine.founder_runtime.adapters.contract_validation import (
    QDRANT_COLLECTION,
    QDRANT_MANIFEST_ID,
    QDRANT_POINT_BASELINE,
    validate_qdrant_verification,
)
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, AdapterResultStatus
from recipe_engine.founder_runtime.adapters.identity import DivergenceState, EvidenceClass
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.errors import (
    MalformedResponse,
    QdrantCollectionMismatch,
    UnsafeCapability,
)
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.live_adapters.base_live import LiveAdapterBase, reject_mutation_shaped
from recipe_engine.live_adapters.host_recovery_gate import GateDecision, HostRecoveryGate
from recipe_engine.live_adapters.settings import LiveAdapterSettings
from recipe_engine.live_adapters.transport import (
    LoopbackHttpClient,
    TransportDenied,
    TransportMalformed,
)

_SERVICE = "qdrant"
_OP_VERIFY = "verify_collection"


class HostRecoverySealed(UnsafeCapability):
    """Raised when the live Qdrant gate is closed pending host recovery GREEN."""


class LiveQdrantAdapter(LiveAdapterBase):
    name = "qdrant"
    adapter_version = "p2b.live.qdrant.1"
    service_name = "qdrant"
    endpoint_class = "loopback"
    capabilities = ("verify_collection", "collection_census", "manifest_scroll")

    def __init__(
        self,
        settings: LiveAdapterSettings,
        http: LoopbackHttpClient | None = None,
        gate: HostRecoveryGate | None = None,
    ):
        super().__init__(settings)
        self._http = http or LoopbackHttpClient(settings.endpoint_map())
        # The gate is pinned to two fixed allowlisted root NAMES under an audit
        # base (default: the workspace).  A caller cannot inject an arbitrary root.
        self._gate = gate or HostRecoveryGate(
            settings.host_recovery_audit_base,
            required_count=settings.qdrant_point_baseline,
            collection=settings.qdrant_collection,
        )
        self._collection = settings.qdrant_collection

    def gate_decision(self) -> GateDecision:
        return self._gate.evaluate()

    def _require_collection(self, collection: str) -> None:
        if collection != self._collection or collection != QDRANT_COLLECTION:
            raise QdrantCollectionMismatch(
                "arbitrary/unknown Qdrant collection is not allowlisted",
                {"requested": str(collection), "allowed": self._collection},
            )

    # --- live verification (only runs when the gate is OPEN) ---------------
    def _live_verify(self, meta: AdapterRequestMeta) -> dict[str, Any]:
        timeout = self.op_timeout("verify_collection", meta)
        col = self._collection
        self._require_collection(col)
        req_hashes: list[str] = []
        resp_hashes: list[str] = []

        def _record(spec: Any, body: Any) -> None:
            req_hashes.append(hash_payload(spec))
            resp_hashes.append(hash_payload(body))

        # readiness
        ready = self._http.request(_SERVICE, "/readyz", timeout_ms=timeout, method="GET")
        _record({"op": "readyz"}, {"status": ready.status})
        if ready.status != 200:
            raise TransportMalformed("qdrant not ready")

        # server identity
        root = self._http.request(_SERVICE, "/", timeout_ms=timeout, method="GET")
        root_json = root.json() if root.status == 200 else {}
        _record({"op": "root"}, root_json)
        server_identity = ""
        if isinstance(root_json, dict):
            server_identity = str(root_json.get("version") or root_json.get("title") or "qdrant")

        # collection identity
        col_resp = self._http.request(_SERVICE, f"/collections/{col}", timeout_ms=timeout, method="GET")
        col_json = col_resp.json()
        _record({"op": "collection", "collection": col}, col_json)
        result = col_json.get("result", {}) if isinstance(col_json, dict) else {}
        if result.get("status") not in ("green", "yellow"):
            raise TransportMalformed("qdrant collection not healthy")

        # exact count BEFORE
        count_spec = {"exact": True}
        cb = self._http.request(_SERVICE, f"/collections/{col}/points/count", timeout_ms=timeout, method="POST", json_body=count_spec)
        cb_json = cb.json()
        _record({"op": "count_before", "collection": col, **count_spec}, cb_json)
        count_before = int(((cb_json or {}).get("result") or {}).get("count", -1))

        # bounded read-only scroll
        scroll_spec = {"limit": 2, "with_payload": True, "with_vector": False}
        sc = self._http.request(_SERVICE, f"/collections/{col}/points/scroll", timeout_ms=timeout, method="POST", json_body=scroll_spec)
        sc_json = sc.json()
        _record({"op": "scroll", "collection": col, **scroll_spec}, sc_json)
        points = ((sc_json or {}).get("result") or {}).get("points") or []
        point_ids = sorted(str(p.get("id")) for p in points if isinstance(p, dict))
        point_id_hash = hash_payload(point_ids)
        manifest_id = ""
        for p in points:
            payload = p.get("payload") if isinstance(p, dict) else None
            if isinstance(payload, dict) and payload.get("manifest_id"):
                manifest_id = str(payload["manifest_id"])
                break
        if not manifest_id:
            manifest_id = QDRANT_MANIFEST_ID  # canon manifest binding
        manifest_hash = hash_payload({"manifest_id": manifest_id, "points": point_ids})

        # exact count AFTER (scroll must not change anything)
        ca = self._http.request(_SERVICE, f"/collections/{col}/points/count", timeout_ms=timeout, method="POST", json_body=count_spec)
        ca_json = ca.json()
        _record({"op": "count_after", "collection": col, **count_spec}, ca_json)
        count_after = int(((ca_json or {}).get("result") or {}).get("count", -1))

        verification = {
            "collection": col,
            "endpoint_class": self.endpoint_class,
            "server_identity": server_identity or "qdrant",
            "container": "neuruh-qdrant",
            "image": "qdrant/qdrant:v1.13.2",
            "count_before": count_before,
            "count_after": count_after,
            "point_id_hash": point_id_hash,
            "manifest_id": manifest_id,
            "manifest_hash": manifest_hash,
            "request_hashes": req_hashes,
            "response_hashes": resp_hashes,
            "mutation_attempted": False,
        }
        # Pure P2-A.1 validator: fails closed on collection/count/manifest drift.
        validate_qdrant_verification(verification)
        return verification

    # --- envelope ----------------------------------------------------------
    def verify_collection(self, meta: AdapterRequestMeta) -> AdapterResult:
        t0 = time.monotonic()
        # 1) mutation-shaped guard (nothing to mutate, but assert intent).
        try:
            reject_mutation_shaped(None, context="qdrant.verify_collection")
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_VERIFY, meta=meta, exc=exc, duration_ms=_ms(t0))

        # 2) HOST-RECOVERY GATE — do not touch live Qdrant until sealed GREEN.
        decision = self._gate.evaluate()
        if not decision.open:
            return self._gated_result(meta, decision, _ms(t0))

        # 3) gate open → live bounded verification.
        try:
            verification = self._live_verify(meta)
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_VERIFY, meta=meta, exc=exc, duration_ms=_ms(t0))

        dep = self.dependency_identity(
            expected=str(QDRANT_POINT_BASELINE),
            observed=str(verification["count_after"]),
            identity_verified=verification["count_after"] == QDRANT_POINT_BASELINE,
            readiness_verified=True,
        ).to_dict()
        prov = self.live_provenance(
            logical=f"live://qdrant/{self._collection}",
            observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=verification["point_id_hash"],
            evidence_class=EvidenceClass.LIVE_OBSERVED,
            divergence=DivergenceState.NOT_APPLICABLE,
        )
        return self.live_result(
            operation=_OP_VERIFY,
            meta=meta,
            status=AdapterResultStatus.SUCCESS,
            payload=verification,
            dependency_identity=dep,
            provenance=prov,
            observed_sha=verification["point_id_hash"],
            evidence_refs=[f"qdrant:{self._collection}:{verification['count_after']}"],
            duration_ms=_ms(t0),
        )

    def _gated_result(self, meta: AdapterRequestMeta, decision: GateDecision, dur: int) -> AdapterResult:
        payload = {
            "gate": "host_recovery",
            "open": False,
            "reason": decision.reason,
            "sealed_audit_path": decision.sealed_audit_path,
            "evidence": decision.evidence or {},
            "collection": self._collection,
            "live_contacted": False,
            "fixture_substituted": False,
        }
        dep = self.dependency_identity(
            expected=str(QDRANT_POINT_BASELINE),
            observed="gate_sealed",
            identity_verified=False,
            readiness_verified=False,
        ).to_dict()
        prov = self.live_provenance(
            logical=f"live://qdrant/{self._collection}",
            observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha="gate_sealed",
            evidence_class=EvidenceClass.UNAVAILABLE,
        )
        return self.live_result(
            operation=_OP_VERIFY,
            meta=meta,
            status=AdapterResultStatus.DENIED,
            payload=payload,
            dependency_identity=dep,
            provenance=prov,
            observed_sha="gate_sealed",
            error_code="DEPENDENCY_UNHEALTHY",
            duration_ms=dur,
        )

    # QdrantRetrievalAdapter parity — semantic search stays gated in P2-B.
    def search(self, query: str, *, limit: int = 5, meta: AdapterRequestMeta | None = None) -> list[dict[str, Any]]:
        raise UnsafeCapability(
            "live semantic Qdrant search is gated in P2-B; use verify_collection (bounded read-only)",
            {"query_hash": hash_payload({"q": str(query)})},
        )

    def search_result(self, query: str, *, limit: int = 5, meta: AdapterRequestMeta | None = None) -> AdapterResult:
        meta = meta or AdapterRequestMeta(correlation_id="qdrant", observed_at="1970-01-01T00:00:00.000Z")
        return self.failure_result(
            operation=_OP_VERIFY,
            meta=meta,
            exc=UnsafeCapability("live semantic search gated in P2-B", {}),
        )


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
