"""Live AEGIS read-only local opinion/evidence adapter (P2-B).

Bounded local opinion/evidence analysis via the accepted local AEGIS endpoint.
Returns evidence references, opinion classification, uncertainty, risk flags,
model/backend provenance, and a receipt hash.  AEGIS is advisory only: this
adapter never lets AEGIS approve execution or claim Governance authority.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from recipe_engine.founder_runtime.adapters.contract_validation import validate_aegis_world
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, AdapterResultStatus
from recipe_engine.founder_runtime.adapters.identity import EvidenceClass
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.errors import UnsafeCapability
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.live_adapters.base_live import LiveAdapterBase, reject_mutation_shaped
from recipe_engine.live_adapters.settings import LiveAdapterSettings
from recipe_engine.live_adapters.transport import LoopbackHttpClient, TransportMalformed

_SERVICE = "aegis"
_OP = "validate_world"


class LiveAegisAdapter(LiveAdapterBase):
    name = "aegis"
    adapter_version = "p2b.live.aegis.1"
    service_name = "aegis"
    endpoint_class = "loopback"
    capabilities = ("validate_world",)

    def __init__(self, settings: LiveAdapterSettings, http: LoopbackHttpClient | None = None):
        super().__init__(settings)
        self._http = http or LoopbackHttpClient(settings.endpoint_map())

    def validate_world(self, world_id: str, meta: AdapterRequestMeta) -> AdapterResult:
        t0 = time.monotonic()
        try:
            # world id must be an allowlisted identifier, never a path.
            if not isinstance(world_id, str) or not world_id or "/" in world_id or ".." in world_id:
                raise UnsafeCapability("AEGIS world must be an allowlisted identifier, never a path", {"world": str(world_id)})
            reject_mutation_shaped(None, context="aegis.validate_world")
            timeout = self.op_timeout("validate_world", meta)
            resp = self._http.request(_SERVICE, f"/world/{world_id}/opinion", timeout_ms=timeout, method="GET")
            if resp.status != 200:
                raise TransportMalformed(f"aegis returned {resp.status}")
            body = resp.json()
            if not isinstance(body, Mapping):
                raise TransportMalformed("aegis opinion is not an object")
            opinion = validate_aegis_world(dict(body))
            # AEGIS is advisory: strip/deny any field that would claim authority.
            if body.get("authorizes_execution") or body.get("governance_authority"):
                raise UnsafeCapability("AEGIS opinion attempted to claim execution/governance authority", {})
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP, meta=meta, exc=exc, duration_ms=_ms(t0))
        receipt_hash = hash_payload(opinion)
        payload = {
            **opinion,
            "opinion_classification": opinion["verdict"],
            "uncertainty": "advisory_only",
            "risk_flags": list(opinion["hard_conflicts"]),
            "model_backend_provenance": str(body.get("model") or body.get("backend") or "live_aegis"),
            "receipt_hash": receipt_hash,
            "authorizes_execution": False,
            "claims_governance_authority": False,
        }
        dep = self.dependency_identity(
            observed=opinion["verdict"], identity_verified=True, readiness_verified=True
        ).to_dict()
        prov = self.live_provenance(
            logical=f"live://aegis/{world_id}",
            observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=receipt_hash,
            evidence_class=EvidenceClass.LIVE_OBSERVED,
        )
        return self.live_result(
            operation=_OP,
            meta=meta,
            status=AdapterResultStatus.SUCCESS,
            payload=payload,
            dependency_identity=dep,
            provenance=prov,
            observed_sha=receipt_hash,
            evidence_refs=[f"aegis:{world_id}:{opinion['verdict']}"],
            duration_ms=_ms(t0),
        )


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
