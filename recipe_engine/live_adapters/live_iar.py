"""Live IAR read-only health & provenance adapter (P2-B).

Read-only IAR health and provenance: active backend, model identity, provider
identity, local vs OpenRouter capability state, configuration provenance,
budget-policy presence, and a bounded health/provenance probe.  Founder Runtime
OpenRouter inference is NOT activated here and NO OpenRouter credits are spent —
``interpret`` is gated DENIED.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from recipe_engine.founder_runtime.adapters.contract_validation import (
    IAR_LEDGER_MAX_BYTES,
    validate_iar_health,
    validate_iar_ledger_continuity,
    validate_iar_provenance,
)
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, AdapterResultStatus
from recipe_engine.founder_runtime.adapters.identity import EvidenceClass
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.errors import ProvenanceInvalid, UnsafeCapability
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.live_adapters.base_live import LiveAdapterBase, reject_mutation_shaped
from recipe_engine.live_adapters.settings import LiveAdapterSettings
from recipe_engine.live_adapters.transport import LoopbackHttpClient, TransportMalformed

_SERVICE = "iar"
_OP_HEALTH = "health"
_OP_PROV = "verify_provenance"


class LiveIARAdapter(LiveAdapterBase):
    name = "iar"
    adapter_version = "p2b.live.iar.1"
    service_name = "iar"
    endpoint_class = "loopback"
    capabilities = ("health", "verify_provenance")

    def __init__(self, settings: LiveAdapterSettings, http: LoopbackHttpClient | None = None):
        super().__init__(settings)
        self._http = http or LoopbackHttpClient(settings.endpoint_map())

    def _get(self, path: str, timeout: int) -> Any:
        resp = self._http.request(_SERVICE, path, timeout_ms=timeout, method="GET")
        if resp.status == 401 or resp.status == 403:
            from recipe_engine.founder_runtime.errors import AuthorityDenied
            raise AuthorityDenied("IAR authentication failed", {"status": resp.status})
        if resp.status != 200:
            raise TransportMalformed(f"iar {path} returned {resp.status}")
        return resp.json()

    def health(self, meta: AdapterRequestMeta) -> AdapterResult:
        t0 = time.monotonic()
        try:
            reject_mutation_shaped(None, context="iar.health")
            timeout = self.op_timeout(_OP_HEALTH, meta)
            body = self._get("/health", timeout)
            if not isinstance(body, Mapping):
                raise TransportMalformed("iar health is not an object")
            # keep only the canonical health keys for the strict validator
            health_view = {k: body.get(k) for k in ("enabled", "active_backend", "active_model", "backends", "degraded_reason")}
            validated = validate_iar_health(health_view)
            provider = self._provider_view(body, validated)
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_HEALTH, meta=meta, exc=exc, duration_ms=_ms(t0))
        payload = {**validated, "provider_view": provider}
        observed_sha = hash_payload(payload)
        backend = validated.get("active_backend")
        dep = self.dependency_identity(
            observed=str(backend or "none"),
            identity_verified=validated["enabled"],
            readiness_verified=bool(backend) and any(validated["backends"].values()),
        ).to_dict()
        prov = self.live_provenance(
            logical="live://iar/health",
            observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
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
            evidence_refs=[f"iar:backend:{backend or 'none'}"],
            duration_ms=_ms(t0),
        )

    def _provider_view(self, body: Mapping[str, Any], validated: Mapping[str, Any]) -> dict[str, Any]:
        backend = validated.get("active_backend") or ""
        is_openrouter = "openrouter" in str(backend).lower() or bool(body.get("openrouter"))
        return {
            "active_backend": backend or None,
            "active_model": validated.get("active_model"),
            "provider": "openrouter" if is_openrouter else ("local" if backend else None),
            "local_capability": bool(backend) and not is_openrouter,
            "openrouter_capability_state": "present_not_activated" if is_openrouter else "absent",
            "budget_policy_present": bool(body.get("budget") or body.get("budget_policy")),
            "config_provenance": str(body.get("config_provenance") or "live_health"),
            "founder_runtime_openrouter_routing": "NOT_AUTHORIZED",
        }

    def verify_provenance(self, meta: AdapterRequestMeta) -> AdapterResult:
        t0 = time.monotonic()
        try:
            reject_mutation_shaped(None, context="iar.verify_provenance")
            anchor_count = self.settings.iar_ledger_baseline_count
            anchor_sha256 = self.settings.iar_ledger_baseline_sha256
            if not anchor_count or not anchor_sha256:
                raise ProvenanceInvalid("IAR ledger baseline anchor is not configured", {})
            timeout = self.op_timeout(_OP_PROV, meta)
            body = self._get("/provenance", timeout)
            if not isinstance(body, Mapping):
                raise TransportMalformed("iar provenance is not an object")
            prov_view = {"provenance_intact": body.get("provenance_intact")}
            binding = body.get("binding")
            validated = validate_iar_provenance(
                prov_view,
                binding if isinstance(binding, Mapping) else {},
                baseline_count=anchor_count,
            )
            continuity = self._verify_ledger_continuity(
                validated["binding"], anchor_count=anchor_count, anchor_sha256=anchor_sha256
            )
            validated = {**validated, "continuity": continuity}
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_PROV, meta=meta, exc=exc, duration_ms=_ms(t0))
        observed_sha = hash_payload(validated)
        dep = self.dependency_identity(
            observed=str(validated["binding"]["repo_sha"]),
            identity_verified=True,
            readiness_verified=True,
        ).to_dict()
        prov = self.live_provenance(
            logical="live://iar/provenance",
            observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=observed_sha,
            evidence_class=EvidenceClass.LIVE_OBSERVED,
        )
        return self.live_result(
            operation=_OP_PROV,
            meta=meta,
            status=AdapterResultStatus.SUCCESS,
            payload=validated,
            dependency_identity=dep,
            provenance=prov,
            observed_sha=observed_sha,
            evidence_refs=[
                f"iar:ledger:{validated['binding']['ledger_line_count']}",
                f"iar:ledger_baseline:{validated['continuity']['baseline_count']}:"
                f"{validated['continuity']['baseline_sha256'][:16]}",
                f"iar:ledger_head:{validated['continuity']['head_entry_hash'][:16]}",
            ],
            duration_ms=_ms(t0),
        )

    def _verify_ledger_continuity(
        self, binding: Mapping[str, Any], *, anchor_count: int, anchor_sha256: str
    ) -> dict[str, Any]:
        """Independent full-chain validation of the ledger the binding reports.

        Read-only bounded file read; the service-reported head receipt
        (line count + hash prefix) must match this adapter's OWN computation
        over the same file, so the service cannot describe one ledger while a
        different one exists on disk.
        """
        path = binding["ledger_path"]
        try:
            with open(path, "rb") as fh:
                ledger_bytes = fh.read(IAR_LEDGER_MAX_BYTES + 1)
        except OSError as exc:
            raise ProvenanceInvalid(
                "IAR ledger is not readable at the bound path", {"path": str(path)}
            ) from exc
        continuity = validate_iar_ledger_continuity(
            ledger_bytes, baseline_count=anchor_count, baseline_sha256=anchor_sha256
        )
        if binding["ledger_line_count"] != continuity["record_count"]:
            raise ProvenanceInvalid(
                "IAR ledger head receipt mismatch (record count)",
                {"reported": binding["ledger_line_count"], "observed": continuity["record_count"]},
            )
        if binding["ledger_sha256_prefix"] != continuity["ledger_sha256"][:16]:
            raise ProvenanceInvalid(
                "IAR ledger head receipt mismatch (ledger hash)",
                {"reported": binding["ledger_sha256_prefix"]},
            )
        return continuity

    # Inference is gated in P2-B — never spends OpenRouter credits.
    def interpret(self, request_text: str, *, principal_id: str = "jeramie", meta: AdapterRequestMeta | None = None) -> dict[str, Any]:
        raise UnsafeCapability(
            "Founder Runtime IAR inference (OpenRouter routing) is NOT authorized in P2-B",
            {"request_hash": hash_payload({"t": str(request_text), "p": principal_id})},
        )

    def interpret_result(self, request_text: str, *, principal_id: str = "jeramie", meta: AdapterRequestMeta | None = None) -> AdapterResult:
        meta = meta or AdapterRequestMeta(correlation_id="iar", observed_at="1970-01-01T00:00:00.000Z")
        return self.failure_result(
            operation=_OP_HEALTH,
            meta=meta,
            exc=UnsafeCapability("IAR inference gated in P2-B", {}),
        )


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
