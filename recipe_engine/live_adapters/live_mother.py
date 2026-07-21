"""Live Mother read-only observation adapter (P2-B).

Bounded read-only observation of Mother: health, identity, version/SHA, expected
contract, equivalence receipt, dependency state, and stale/unavailable
classification.  No commands, no writes.  Mother is reached only via its
allowlisted loopback endpoint; when it is down the adapter reports UNAVAILABLE
and never substitutes a fixture.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from recipe_engine.founder_runtime.adapters.contract_validation import validate_mother_equivalence_receipt
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, AdapterResultStatus
from recipe_engine.founder_runtime.adapters.identity import EvidenceClass
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.contracts.health import DependencyHealth, HealthSnapshot
from recipe_engine.founder_runtime.errors import MalformedResponse
from recipe_engine.founder_runtime.ids import hash_payload, stable_id
from recipe_engine.live_adapters.base_live import LiveAdapterBase, reject_mutation_shaped
from recipe_engine.live_adapters.settings import LiveAdapterSettings
from recipe_engine.live_adapters.transport import LoopbackHttpClient, TransportMalformed

_SERVICE = "mother"
_OP_HEALTH = "health_snapshot"
_OP_RECEIPT = "read_equivalence_receipt"
_HEALTH_SCHEMA = "fr.health_snapshot.v1"


class LiveMotherAdapter(LiveAdapterBase):
    name = "mother"
    adapter_version = "p2b.live.mother.1"
    service_name = "mother"
    endpoint_class = "loopback"
    capabilities = ("health_snapshot", "read_equivalence_receipt")

    def __init__(self, settings: LiveAdapterSettings, http: LoopbackHttpClient | None = None):
        super().__init__(settings)
        self._http = http or LoopbackHttpClient(settings.endpoint_map())

    def _get(self, path: str, timeout: int) -> Any:
        resp = self._http.request(_SERVICE, path, timeout_ms=timeout, method="GET")
        if resp.status != 200:
            raise TransportMalformed(f"mother {path} returned {resp.status}")
        return resp.json()

    def health_snapshot(self, *, observed_at: str, meta: AdapterRequestMeta | None = None) -> HealthSnapshot:
        timeout = (meta.timeout_ms if meta and meta.timeout_ms else 5_000)
        body = self._get("/health", timeout)
        if not isinstance(body, Mapping):
            raise MalformedResponse("mother health is not an object", {})
        deps = []
        for d in body.get("dependencies", []) or []:
            if isinstance(d, Mapping):
                deps.append(
                    DependencyHealth(
                        dependency_id=str(d.get("id") or d.get("name") or "dep"),
                        name=str(d.get("name") or "dep"),
                        status=str(d.get("status") or "unknown"),
                        provenance="live_mother",
                        detail=str(d.get("detail") or ""),
                    )
                )
        return HealthSnapshot(
            schema_version=_HEALTH_SCHEMA,
            snapshot_id=stable_id("mother_health", observed_at, hash_payload(dict(body))),
            observed_at=observed_at,
            overall=str(body.get("overall") or body.get("status") or "unknown"),
            dependencies=tuple(deps),
            blocked=tuple(str(b) for b in (body.get("blocked") or [])),
            source="live_mother",
        )

    def health_result(self, *, observed_at: str, meta: AdapterRequestMeta) -> AdapterResult:
        t0 = time.monotonic()
        try:
            reject_mutation_shaped(None, context="mother.health")
            snap = self.health_snapshot(observed_at=observed_at, meta=meta)
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_HEALTH, meta=meta, exc=exc, duration_ms=_ms(t0))
        payload = snap.to_dict()
        observed_sha = hash_payload(payload)
        dep = self.dependency_identity(
            observed=snap.overall, identity_verified=True, readiness_verified=snap.overall in ("healthy", "green")
        ).to_dict()
        prov = self.live_provenance(
            logical="live://mother/health",
            observed_at=observed_at,
            observed_sha=observed_sha,
            evidence_class=EvidenceClass.LIVE_OBSERVED,
        )
        return self.live_result(
            operation=_OP_HEALTH,
            meta=meta,
            status=AdapterResultStatus.SUCCESS,
            payload=payload,
            dependency_identity=dep,
            provenance=prov,
            observed_sha=observed_sha,
            evidence_refs=[snap.snapshot_id],
            duration_ms=_ms(t0),
        )

    def read_equivalence_receipt(
        self,
        receipt_ref: str | Mapping[str, Any],
        meta: AdapterRequestMeta,
    ) -> AdapterResult:
        t0 = time.monotonic()
        try:
            reject_mutation_shaped(receipt_ref if isinstance(receipt_ref, Mapping) else None, context="mother.equivalence")
            timeout = (meta.timeout_ms if meta and meta.timeout_ms else 5_000)
            if isinstance(receipt_ref, Mapping):
                body = dict(receipt_ref)  # caller supplied a pre-observed receipt
            else:
                body = self._get(f"/equivalence/{receipt_ref}", timeout)
            normalized = validate_mother_equivalence_receipt(body)
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_RECEIPT, meta=meta, exc=exc, duration_ms=_ms(t0))
        observed_sha = hash_payload(normalized)
        dep = self.dependency_identity(
            observed=normalized["binding"]["cockpit_sha"], identity_verified=True, readiness_verified=True
        ).to_dict()
        prov = self.live_provenance(
            logical="live://mother/equivalence",
            observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=observed_sha,
            evidence_class=EvidenceClass.LIVE_OBSERVED,
        )
        return self.live_result(
            operation=_OP_RECEIPT,
            meta=meta,
            status=AdapterResultStatus.SUCCESS,
            payload=normalized,
            dependency_identity=dep,
            provenance=prov,
            observed_sha=observed_sha,
            evidence_refs=[f"mother:equivalence:{normalized['binding']['cycle_id']}"],
            duration_ms=_ms(t0),
        )


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
