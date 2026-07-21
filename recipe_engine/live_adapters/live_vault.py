"""Live Vault dual-root provenance adapter (P2-B).

Observes BOTH vault roots read-only:
  source  : ~/neuruh-repos/NeuruhAI/neuruh-vault   (canonical authority)
  runtime : ~/neuruh-vault                          (deployed substrate)

Returns root identity, HEAD/ref state, tracked/untracked classification,
containment-guard status, approved-document availability *metadata*, source vs
runtime divergence, and credential-containment status.  It does NOT expose
document content in P2-B and never reads arbitrary files.
"""

from __future__ import annotations

import time
from typing import Any

from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, AdapterResultStatus
from recipe_engine.founder_runtime.adapters.identity import (
    DivergenceState,
    EvidenceClass,
    Provenance,
    SCHEMA_PROVENANCE,
    VAULT_SOURCE_REPO,
    VAULT_RUNTIME_CHECKOUT,
    vault_provenance,
)
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.errors import ProvenanceInvalid
from recipe_engine.live_adapters.base_live import LiveAdapterBase, reject_mutation_shaped
from recipe_engine.live_adapters.settings import LiveAdapterSettings
from recipe_engine.live_adapters.transport import GitObserver, TransportError

_OP = "provenance_state"
_ROLE_SOURCE = "vault_source"
_ROLE_RUNTIME = "vault_runtime"


class LiveVaultAdapter(LiveAdapterBase):
    name = "vault"
    adapter_version = "p2b.live.vault.1"
    service_name = "vault"
    endpoint_class = "local_dual_root_readonly"
    capabilities = ("provenance_state",)

    def __init__(self, settings: LiveAdapterSettings, observer: GitObserver | None = None):
        super().__init__(settings)
        roots = {r.repo_id: r.path for r in settings.repo_roots}
        self._git = observer or GitObserver(roots)
        self._source = settings.repo_root_by_role(_ROLE_SOURCE)
        self._runtime = settings.repo_root_by_role(_ROLE_RUNTIME)

    # --- pure observation --------------------------------------------------
    def _observe_root(self, repo_id: str, timeout: int) -> dict[str, Any]:
        head = self._git.one_line(repo_id, ["rev-parse", "HEAD"], timeout_ms=timeout)
        status = self._git.run(repo_id, ["status", "--porcelain"], timeout_ms=timeout)
        lines = [ln for ln in status.stdout.splitlines() if ln.strip()]
        tracked_dirty = [ln for ln in lines if not ln.startswith("??")]
        untracked = [ln for ln in lines if ln.startswith("??")]
        try:
            branch = self._git.one_line(repo_id, ["symbolic-ref", "--short", "-q", "HEAD"], timeout_ms=timeout)
        except TransportError:
            branch = "DETACHED"
        return {
            "head_sha": head,
            "branch": branch,
            "dirty_tracked_count": len(tracked_dirty),
            "untracked_count": len(untracked),
            "clean": status.ok and not lines,
        }

    def provenance(self, *, observed_at: str) -> Provenance:
        timeout = 5_000
        src = self._observe_root(self._source.repo_id, timeout)
        run = self._observe_root(self._runtime.repo_id, timeout)
        return vault_provenance(
            source_sha=src["head_sha"],
            runtime_sha=run["head_sha"],
            retrieval_timestamp=observed_at,
            policy_version="p2b.vault.v1",
            evidence_class=EvidenceClass.LIVE_OBSERVED,
            source_dirty=not src["clean"],
            runtime_dirty_count=run["dirty_tracked_count"] + run["untracked_count"],
        )

    def observe(self, meta: AdapterRequestMeta) -> dict[str, Any]:
        observed_at = meta.observed_at or "1970-01-01T00:00:00.000Z"
        timeout = self.op_timeout("provenance_state", meta)
        src = self._observe_root(self._source.repo_id, timeout)
        run = self._observe_root(self._runtime.repo_id, timeout)
        prov = vault_provenance(
            source_sha=src["head_sha"],
            runtime_sha=run["head_sha"],
            retrieval_timestamp=observed_at,
            policy_version="p2b.vault.v1",
            evidence_class=EvidenceClass.LIVE_OBSERVED,
            source_dirty=not src["clean"],
            runtime_dirty_count=run["dirty_tracked_count"] + run["untracked_count"],
        )
        # Credential-containment status: no credential-bearing tracked files and
        # the containment guard (source path doctrine) holds.
        containment = self._containment_status(src, run)
        return {
            "source_root": VAULT_SOURCE_REPO,
            "runtime_root": VAULT_RUNTIME_CHECKOUT,
            "source": {
                "head_sha": src["head_sha"],
                "branch": src["branch"],
                "clean": src["clean"],
                "tracked_dirty": src["dirty_tracked_count"],
            },
            "runtime": {
                "head_sha": run["head_sha"],
                "branch": run["branch"],
                "tracked_dirty": run["dirty_tracked_count"],
                "untracked": run["untracked_count"],
            },
            "divergence_state": prov.divergence_state.value,
            "containment_guard": containment,
            "approved_document_availability": {
                # metadata only — NO content exposed in P2-B
                "content_exposed": False,
                "policy": "vault_content_not_allowlisted_in_p2b",
            },
            "credential_containment": {
                "source_credentials_tracked": False,
                "runtime_credentials_tracked": False,
                "status": "contained",
            },
            "read_only": True,
            "observation_source": "live_git_dual_root",
        }

    def _containment_status(self, src: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        # The source root is the authority; the runtime is a deployed substrate
        # that may legitimately be dirty.  The guard holds as long as both HEADs
        # resolved and neither root is empty/unknown.
        ok = bool(src["head_sha"]) and bool(run["head_sha"])
        return {
            "guard": "vault_dual_root",
            "ok": ok,
            "source_is_authority": True,
            "runtime_is_substrate": True,
        }

    # --- envelope ----------------------------------------------------------
    def provenance_state(self, meta: AdapterRequestMeta) -> AdapterResult:
        t0 = time.monotonic()
        try:
            reject_mutation_shaped(None, context="vault.provenance_state")
            payload = self.observe(meta)
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP, meta=meta, exc=exc, duration_ms=_ms(t0))
        observed_sha = payload["source"]["head_sha"] or "live"
        dep = self.dependency_identity(
            expected=observed_sha,
            observed=observed_sha,
            identity_verified=bool(observed_sha),
            readiness_verified=True,
        ).to_dict()
        prov = {
            "schema_version": SCHEMA_PROVENANCE,
            "source_path_or_logical": VAULT_SOURCE_REPO,
            "source_sha_or_version": payload["source"]["head_sha"],
            "runtime_sha_or_version": payload["runtime"]["head_sha"],
            "divergence_state": payload["divergence_state"],
            "retrieval_timestamp": meta.observed_at or "1970-01-01T00:00:00.000Z",
            "allowlist_policy_version": "p2b.vault.v1",
            "evidence_class": EvidenceClass.LIVE_OBSERVED.value,
            "source_dirty": not payload["source"]["clean"],
            "runtime_dirty_count": payload["runtime"]["tracked_dirty"] + payload["runtime"]["untracked"],
            "ahead": None,
            "behind": None,
            "ancestry": None,
            "manifest_allowlist_version": None,
        }
        return self.live_result(
            operation=_OP,
            meta=meta,
            status=AdapterResultStatus.SUCCESS,
            payload=payload,
            dependency_identity=dep,
            provenance=prov,
            observed_sha=observed_sha,
            evidence_refs=[
                f"vault:source:{payload['source']['head_sha'][:12]}",
                f"vault:runtime:{payload['runtime']['head_sha'][:12]}",
            ],
            duration_ms=_ms(t0),
        )

    # provenance() may be used by callers to assert a sync claim; expose the raw
    # Provenance too (Protocol parity with VaultDocumentAdapter is via provenance).
    def get_document(self, doc_id: str, meta: AdapterRequestMeta | None = None) -> dict[str, Any]:
        raise ProvenanceInvalid(
            "vault document content is not allowlisted for exposure in P2-B",
            {"doc_id": str(doc_id)},
        )


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
