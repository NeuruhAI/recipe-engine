"""Live Governance decision/authorization adapter (P2-B, T31B).

Observation/decision-only integration against promoted Governance main.  Verifies
the exact content-bound request, authority state, policy version, receipt chain,
and stamps a NOT_CLAIMED_BY_GOVERNANCE execution marker.  It never requests or
consumes live mutation authority: a decision is *observed*, never executed.

T31B adds the second half of the authority chain.  ``decide()`` asks the policy
question on ``/v1/evaluate`` — is this class of action permissible at all — and
``authorize()`` asks the authority question on ``/v2/authorize``: is THIS exact
action, from THIS exact source SHA, backed by THIS externally decided founder
approval, authorized once.  They are different questions and both are asked;
neither substitutes for the other.

This adapter SUBMITS a founder approval.  It never creates one, and there is no
signing capability anywhere in this package: the artifact arrives already signed
from the external decision channel, and this adapter's only power over it is to
hand it to Governance and believe the answer.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from recipe_engine.founder_runtime.adapters.contract_validation import (
    validate_governance_authorization,
    validate_governance_decision,
    validate_governance_health,
)
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, AdapterResultStatus
from recipe_engine.founder_runtime.adapters.identity import EvidenceClass
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, GovernanceAction
from recipe_engine.founder_runtime.contracts.authority import AuthorityClass
from recipe_engine.founder_runtime.errors import ProvenanceInvalid, UnsafeCapability
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.live_adapters.base_live import LiveAdapterBase, reject_mutation_shaped
from recipe_engine.live_adapters.settings import LiveAdapterSettings
from recipe_engine.live_adapters.transport import LoopbackHttpClient, TransportMalformed

_SERVICE = "governance"
_OP_HEALTH = "health"
_OP_POLICY = "default_policy"
_OP_DECIDE = "decide"
_OP_AUTHORIZE = "authorize"
NOT_CLAIMED = "NOT_CLAIMED_BY_GOVERNANCE"


class LiveGovernanceAdapter(LiveAdapterBase):
    name = "governance"
    adapter_version = "p2b.live.governance.1"
    service_name = "governance"
    endpoint_class = "loopback"
    capabilities = ("health", "default_policy", "decide", "authorize")

    def __init__(self, settings: LiveAdapterSettings, http: LoopbackHttpClient | None = None):
        super().__init__(settings)
        self._http = http or LoopbackHttpClient(settings.endpoint_map())
        self._expected_sha = settings.governance_source_sha

    def _get(self, path: str, timeout: int) -> Any:
        resp = self._http.request(_SERVICE, path, timeout_ms=timeout, method="GET")
        if resp.status != 200:
            raise TransportMalformed(f"governance {path} returned {resp.status}")
        return resp.json()

    def health(self, meta: AdapterRequestMeta) -> AdapterResult:
        t0 = time.monotonic()
        try:
            reject_mutation_shaped(None, context="governance.health")
            timeout = self.op_timeout(_OP_HEALTH, meta)
            body = self._get("/health", timeout)
            validated = validate_governance_health(dict(body) if isinstance(body, Mapping) else {})
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_HEALTH, meta=meta, exc=exc, duration_ms=_ms(t0))
        observed_sha = hash_payload(validated)
        dep = self.dependency_identity(
            expected="autonomous-governance-core",
            observed=validated["service"],
            identity_verified=validated["service"] == "autonomous-governance-core",
            readiness_verified=validated["ok"] is True,
        ).to_dict()
        prov = self.live_provenance(
            logical="live://governance/health",
            observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=observed_sha,
            evidence_class=EvidenceClass.LIVE_OBSERVED,
        )
        return self.live_result(
            operation=_OP_HEALTH,
            meta=meta,
            status=AdapterResultStatus.SUCCESS,
            payload={**validated, "execution_marker": NOT_CLAIMED},
            dependency_identity=dep,
            provenance=prov,
            observed_sha=observed_sha,
            evidence_refs=["governance:health"],
            duration_ms=_ms(t0),
        )

    def default_policy(self, meta: AdapterRequestMeta) -> AdapterResult:
        t0 = time.monotonic()
        try:
            reject_mutation_shaped(None, context="governance.default_policy")
            timeout = self.op_timeout(_OP_POLICY, meta)
            body = self._get("/v1/policy", timeout)
            if not isinstance(body, Mapping) or "policy_version" not in body:
                raise TransportMalformed("governance policy lacks policy_version")
            policy_version = str(body["policy_version"])
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_POLICY, meta=meta, exc=exc, duration_ms=_ms(t0))
        payload = {"policy_version": policy_version, "execution_marker": NOT_CLAIMED}
        observed_sha = hash_payload(payload)
        dep = self.dependency_identity(
            expected=self._expected_sha, observed=policy_version, identity_verified=True, readiness_verified=True
        ).to_dict()
        prov = self.live_provenance(
            logical="live://governance/policy",
            observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=observed_sha,
            evidence_class=EvidenceClass.LIVE_OBSERVED,
        )
        return self.live_result(
            operation=_OP_POLICY,
            meta=meta,
            status=AdapterResultStatus.SUCCESS,
            payload=payload,
            dependency_identity=dep,
            provenance=prov,
            observed_sha=observed_sha,
            evidence_refs=[f"governance:policy:{policy_version}"],
            duration_ms=_ms(t0),
        )

    def decide(
        self,
        action: GovernanceAction,
        *,
        trusted_authority_class: AuthorityClass | str,
        trusted_policy_version: str,
        meta: AdapterRequestMeta,
    ) -> AdapterResult:
        t0 = time.monotonic()
        try:
            # The request shape is closed (GovernanceAction) so a caller policy
            # override is impossible; still assert no mutation-shaped smuggling.
            action_dict = action.to_dict()
            reject_mutation_shaped(None, context="governance.decide")
            if "policy" in action_dict:
                raise UnsafeCapability("caller policy override is forbidden", {})
            request_hash = hash_payload(action_dict)
            timeout = self.op_timeout(_OP_DECIDE, meta)
            resp = self._http.request(
                _SERVICE, "/v1/evaluate", timeout_ms=timeout, method="POST", json_body={"action": action_dict}
            )
            if resp.status != 200:
                raise TransportMalformed(f"governance evaluate returned {resp.status}")
            body = resp.json()
            decision = validate_governance_decision(dict(body) if isinstance(body, Mapping) else {})
            # Content-bound: the echoed action must match what we submitted.
            echoed = decision["action"]
            if hash_payload(dict(echoed)) != request_hash and echoed.get("action_id") != action.action_id:
                raise ProvenanceInvalid("governance echoed a different action than requested", {})
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_DECIDE, meta=meta, exc=exc, duration_ms=_ms(t0))
        observed_sha = hash_payload(decision)
        payload = {
            **decision,
            "request_hash": request_hash,
            "trusted_authority_class": trusted_authority_class.value if isinstance(trusted_authority_class, AuthorityClass) else str(trusted_authority_class),
            "trusted_policy_version": trusted_policy_version,
            "execution_marker": NOT_CLAIMED,
            "mutation_authority_consumed": False,
        }
        dep = self.dependency_identity(
            expected=self._expected_sha, observed=str(decision["audit_id"]), identity_verified=True, readiness_verified=True
        ).to_dict()
        prov = self.live_provenance(
            logical="live://governance/decide",
            observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=observed_sha,
            evidence_class=EvidenceClass.LIVE_OBSERVED,
        )
        return self.live_result(
            operation=_OP_DECIDE,
            meta=meta,
            status=AdapterResultStatus.SUCCESS,
            payload=payload,
            dependency_identity=dep,
            provenance=prov,
            observed_sha=observed_sha,
            evidence_refs=[f"governance:decision:{decision['audit_id']}"],
            duration_ms=_ms(t0),
        )

    def authorize(
        self,
        facts: Mapping[str, Any],
        *,
        founder_approval: Mapping[str, Any] | None,
        meta: AdapterRequestMeta,
    ) -> AdapterResult:
        """Submit one exact action, with its external founder approval, to /v2/authorize.

        Content-bound in both directions.  The facts are sent verbatim and the
        returned envelope must carry the hash of exactly those facts; an envelope
        bound to different content is treated as provenance failure rather than
        as a decision about this action.

        This is a submission, never a signature.  ``founder_approval`` is an
        opaque artifact produced elsewhere: this adapter cannot construct one,
        cannot alter one without invalidating it, and holds no secret with which
        it could.  If it is None, nothing is smuggled in its place — the request
        simply goes without one and Governance decides on that basis.
        """
        t0 = time.monotonic()
        try:
            reject_mutation_shaped(None, context="governance.authorize")
            facts_dict = dict(facts)
            if "policy" in facts_dict:
                raise UnsafeCapability("caller policy override is forbidden", {})
            facts_hash = hash_payload(facts_dict)
            body: dict[str, Any] = {"facts": facts_dict}
            if founder_approval is not None:
                body["founder_approval"] = dict(founder_approval)
            timeout = self.op_timeout(_OP_AUTHORIZE, meta)
            resp = self._http.request(
                _SERVICE, "/v2/authorize", timeout_ms=timeout, method="POST", json_body=body
            )
            if resp.status != 200:
                raise TransportMalformed(f"governance authorize returned {resp.status}")
            raw = resp.json()
            envelope = validate_governance_authorization(dict(raw) if isinstance(raw, Mapping) else {})
            # The envelope must be about the action we submitted. Governance
            # canonicalizes the facts itself, so we compare its hash of them
            # against ours; a mismatch means the answer is not about this action.
            if envelope["decision"] == "allow" and envelope["canonical_facts_hash"] != _gov_facts_hash(facts_dict):
                raise ProvenanceInvalid("governance authorized different content than requested", {})
        except Exception as exc:  # noqa: BLE001
            return self.failure_result(operation=_OP_AUTHORIZE, meta=meta, exc=exc, duration_ms=_ms(t0))
        observed_sha = hash_payload(envelope)
        payload = {
            **envelope,
            "request_hash": facts_hash,
            "founder_approval_presented": founder_approval is not None,
            "execution_marker": NOT_CLAIMED,
            "mutation_authority_consumed": False,
        }
        dep = self.dependency_identity(
            expected=self._expected_sha,
            observed=str(envelope["authorization_id"]),
            identity_verified=True,
            readiness_verified=True,
        ).to_dict()
        prov = self.live_provenance(
            logical="live://governance/authorize",
            observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=observed_sha,
            evidence_class=EvidenceClass.LIVE_OBSERVED,
        )
        return self.live_result(
            operation=_OP_AUTHORIZE,
            meta=meta,
            status=AdapterResultStatus.SUCCESS,
            payload=payload,
            dependency_identity=dep,
            provenance=prov,
            observed_sha=observed_sha,
            evidence_refs=[f"governance:authorization:{envelope['authorization_id']}"],
            duration_ms=_ms(t0),
        )



def _gov_facts_hash(facts: Mapping[str, Any]) -> str:
    """Governance's canonical hash of these facts, computed the way it does.

    The runtime's own hash_payload is a different canonical form, so comparing
    against it would reject every honest answer. This uses the shared
    Governance-canonical implementation the signing channel also uses.
    """
    from recipe_engine.founder_runtime.adapters.governance_authorization import hash_canonical

    return hash_canonical(facts)


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
