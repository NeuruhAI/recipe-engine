"""End-to-end Founder Runtime read-only LIVE canary (P2-B).

Executes the ordered read-only path:

  Founder request
    -> Recipe Engine compile (deterministic id from the request)
    -> Git observation
    -> Vault provenance
    -> Qdrant bounded retrieval (gated on host recovery)
    -> Mother health
    -> IAR provenance
    -> AEGIS opinion
    -> Governance decision (observation-only)
    -> AXON authenticated read-only action
    -> completion verification
    -> deterministic receipt chain

It NEVER substitutes a fixture for a live adapter, never mutates, never persists
to production, and never communicates externally.  When any organ is UNAVAILABLE
/ DENIED — or the Qdrant host-recovery gate is sealed — the canary HOLDs at that
organ, records the exact reason, and refuses to claim completion.  The receipt
chain is deterministic: re-running with the same inputs reconstructs an
identical chain (adapter observations are hashed; wall-clock is excluded).

This module is a composition root; the caller supplies a fully-built
:class:`LiveAdapterSettings`.  It uses only the live adapters (LIVE mode) via the
existing registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from recipe_engine.founder_runtime.adapters.base import AdapterMode
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, AdapterResultStatus, validate_payload_hash
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, GovernanceAction
from recipe_engine.founder_runtime.adapters.registry import AdapterRegistry
from recipe_engine.founder_runtime.contracts.authority import AuthorityClass
from recipe_engine.founder_runtime.ids import hash_payload, stable_id
from recipe_engine.live_adapters import LIVE_POLICY_VERSION
from recipe_engine.live_adapters.canonical_topology import resolve_aegis_world
from recipe_engine.live_adapters.registration import build_live_registry
from recipe_engine.live_adapters.settings import LiveAdapterSettings

CANARY_REQUEST = (
    "Observe the Neuruh organism read-only across every organ and prove the "
    "live read-only adapter path end to end without mutation."
)

# The required organ order (P2-B section J).
ORGAN_ORDER = ("github", "vault", "qdrant", "mother", "iar", "aegis", "governance", "axon_readonly")

_FIXED_TS = "1970-01-01T00:00:00.000Z"  # deterministic observed_at for replayable receipts


@dataclass
class OrganObservation:
    organ: str
    operation: str
    status: str
    adapter_mode: str
    payload_hash: str
    source_version_or_sha: str
    error_code: str | None
    evidence_refs: list[str]
    fallback_used: bool
    mutation_attempted: bool
    receipt_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "organ": self.organ,
            "operation": self.operation,
            "status": self.status,
            "adapter_mode": self.adapter_mode,
            "payload_hash": self.payload_hash,
            "source_version_or_sha": self.source_version_or_sha,
            "error_code": self.error_code,
            "evidence_refs": list(self.evidence_refs),
            "fallback_used": self.fallback_used,
            "mutation_attempted": self.mutation_attempted,
            "receipt_hash": self.receipt_hash,
        }


@dataclass
class CanaryResult:
    verdict: str                      # "GREEN" | "HOLD"
    request_hash: str
    mission_id: str
    organ_order: list[str]
    observations: list[OrganObservation] = field(default_factory=list)
    receipt_chain: list[dict[str, Any]] = field(default_factory=list)
    chain_root_hash: str = ""
    hold_reason: str | None = None
    hold_organ: str | None = None
    fixture_fallback_used: bool = False
    mutation_observed: bool = False
    production_persistence: bool = False
    external_communication: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "request_hash": self.request_hash,
            "mission_id": self.mission_id,
            "organ_order": list(self.organ_order),
            "observations": [o.to_dict() for o in self.observations],
            "receipt_chain": list(self.receipt_chain),
            "chain_root_hash": self.chain_root_hash,
            "hold_reason": self.hold_reason,
            "hold_organ": self.hold_organ,
            "fixture_fallback_used": self.fixture_fallback_used,
            "mutation_observed": self.mutation_observed,
            "production_persistence": self.production_persistence,
            "external_communication": self.external_communication,
            "policy_version": LIVE_POLICY_VERSION,
        }


def _meta(mission_id: str, step: str) -> AdapterRequestMeta:
    return AdapterRequestMeta(
        correlation_id=mission_id, causation_id=step, mission_id=mission_id,
        plan_id=f"{mission_id}:plan", step_id=step, observed_at=_FIXED_TS,
        authority_class=AuthorityClass.A0,
    )


def _observe(organ: str, result: AdapterResult) -> OrganObservation:
    return OrganObservation(
        organ=organ,
        operation=result.operation,
        status=result.status,
        adapter_mode=result.adapter_mode,
        payload_hash=result.payload_hash,
        source_version_or_sha=result.source_version_or_sha,
        error_code=result.error_code,
        evidence_refs=list(result.evidence_refs),
        fallback_used=result.fallback_used,
        mutation_attempted=result.mutation_attempted,
    )


def run_live_readonly_canary(
    settings: LiveAdapterSettings,
    *,
    registry: AdapterRegistry | None = None,
    request_text: str = CANARY_REQUEST,
    github_repo: str = "NeuruhAI/recipe-engine",
    aegis_world: str | None = None,
    axon_path_id: str | None = None,
) -> CanaryResult:
    """Run the ordered read-only canary once.  Deterministic and side-effect-safe.

    ``aegis_world`` selects the AEGIS world from the fixed live allowlist; ``None``
    uses the canonical default (``defensenet``).  A mock-only world
    (``neuruh_status``) or any non-allowlisted name is rejected fail-closed
    (``AegisWorldError``) BEFORE the canary starts — never sent to AEGIS, never a
    fixture fallback.
    """
    # Fail closed on the AEGIS world before any organ runs.  The mock default that
    # 404s against real AEGIS is gone; the canonical real world is used instead.
    aegis_world = resolve_aegis_world(aegis_world)
    registry = registry or build_live_registry(settings)

    # 1) Recipe Engine compile: a deterministic mission id bound to the request.
    request_hash = hash_payload({"request": request_text, "policy": LIVE_POLICY_VERSION})
    mission_id = stable_id("canary", request_hash)

    out = CanaryResult(
        verdict="GREEN", request_hash=request_hash, mission_id=mission_id,
        organ_order=list(ORGAN_ORDER),
    )

    def resolve(kind: str):
        # LIVE only — the registry forbids a fixture downgrade.
        return registry.resolve(kind, mode=AdapterMode.LIVE)

    # 2) ordered organ observations.  Each organ is invoked live; the first
    #    non-SUCCESS is the HOLD point (fail closed, no fixture fallback).
    def step(organ: str, call) -> AdapterResult:
        result = call()
        obs = _observe(organ, result)
        # bind a per-organ receipt into the chain (see finalize below)
        out.observations.append(obs)
        if result.fallback_used:
            out.fixture_fallback_used = True
        if result.mutation_attempted:
            out.mutation_observed = True
        return result

    prev_hash = "0" * 64
    hold = False

    def chain(obs: OrganObservation) -> None:
        nonlocal prev_hash
        material = {
            "seq": len(out.receipt_chain) + 1,
            "prev_hash": prev_hash,
            "mission_id": mission_id,
            "request_hash": request_hash,
            "organ": obs.organ,
            "operation": obs.operation,
            "status": obs.status,
            "adapter_mode": obs.adapter_mode,
            "payload_hash": obs.payload_hash,
            "source_version_or_sha": obs.source_version_or_sha,
            "evidence_refs": list(obs.evidence_refs),
            "mutation_attempted": obs.mutation_attempted,
            "fallback_used": obs.fallback_used,
        }
        entry_hash = hash_payload(material)
        obs.receipt_hash = entry_hash
        out.receipt_chain.append({**material, "entry_hash": entry_hash})
        prev_hash = entry_hash

    calls = [
        ("github", lambda: resolve("github").repo_state_result(github_repo, _meta(mission_id, "git_observation"))),
        ("vault", lambda: resolve("vault").provenance_state(_meta(mission_id, "vault_provenance"))),
        ("qdrant", lambda: resolve("qdrant").verify_collection(_meta(mission_id, "qdrant_bounded_retrieval"))),
        ("mother", lambda: resolve("mother").health_result(observed_at=_FIXED_TS, meta=_meta(mission_id, "mother_health"))),
        ("iar", lambda: resolve("iar").verify_provenance(_meta(mission_id, "iar_provenance"))),
        ("aegis", lambda: resolve("aegis").validate_world(aegis_world, _meta(mission_id, "aegis_opinion"))),
        ("governance", lambda: resolve("governance").decide(
            GovernanceAction(action_id=stable_id("act", mission_id), actor="founder_runtime",
                             tool="observe", domain="organism_status", summary="read-only observation"),
            trusted_authority_class=AuthorityClass.A0, trusted_policy_version=LIVE_POLICY_VERSION,
            meta=_meta(mission_id, "governance_decision"))),
        ("axon_readonly", lambda: resolve("axon_readonly").identity(_meta(mission_id, "axon_readonly_action"))
         if axon_path_id is None else
         resolve("axon_readonly").governed_read(axon_path_id, _meta(mission_id, "axon_readonly_action"))),
    ]

    for organ, call in calls:
        if hold:
            break
        try:
            result = step(organ, call)
        except Exception as exc:  # noqa: BLE001 - any organ failure is a HOLD, never a fixture
            out.verdict = "HOLD"
            out.hold_organ = organ
            out.hold_reason = f"{organ} raised {type(exc).__name__}: {str(exc)[:160]}"
            hold = True
            break
        chain(out.observations[-1])
        # integrity: every chained observation's payload hash must self-verify.
        if not _payload_hash_ok(result):
            out.verdict = "HOLD"
            out.hold_organ = organ
            out.hold_reason = f"{organ} payload hash failed self-verification"
            hold = True
            break
        if result.status != AdapterResultStatus.SUCCESS.value:
            out.verdict = "HOLD"
            out.hold_organ = organ
            out.hold_reason = f"{organ} returned status={result.status} error={result.error_code} (no fixture fallback)"
            hold = True
            break

    # 3) completion verification — GREEN only if every organ was live SUCCESS.
    out.chain_root_hash = hash_payload(out.receipt_chain)
    all_success = (
        not hold
        and len(out.observations) == len(ORGAN_ORDER)
        and all(o.status == AdapterResultStatus.SUCCESS.value for o in out.observations)
        and all(o.adapter_mode == AdapterMode.LIVE.value for o in out.observations)
        and not out.fixture_fallback_used
        and not out.mutation_observed
    )
    out.verdict = "GREEN" if all_success else "HOLD"
    if out.verdict == "HOLD" and out.hold_reason is None:
        out.hold_reason = "not all organs returned live SUCCESS"
    return out


def _payload_hash_ok(result: AdapterResult) -> bool:
    try:
        return validate_payload_hash(result)
    except Exception:  # noqa: BLE001
        return False


def canary_signature(result: CanaryResult) -> str:
    """A replay-stable signature: excludes wall-clock, includes the full chain."""
    return hash_payload({
        "verdict": result.verdict,
        "request_hash": result.request_hash,
        "mission_id": result.mission_id,
        "organ_order": result.organ_order,
        "chain_root_hash": result.chain_root_hash,
        "hold_organ": result.hold_organ,
        "observations": [
            {k: v for k, v in o.to_dict().items() if k != "receipt_hash"} for o in result.observations
        ],
    })
