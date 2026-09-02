"""The approval resolver — what happens to a parked mission when nobody is required.

    AWAITING_APPROVAL
      -> consequence classification (trusted kernel class -> Governance facts)
      -> Governance /v2/authorize
      -> allow | founder_required | deny
      -> the kernel's own approval-gated authority transition

Two defects this closes, and the invariants it keeps while closing them:

* A safe, reversible, internal mission (A0/A1, or A2-A4 inside a valid
  FounderGrant) used to park at AWAITING_APPROVAL forever because the only exit
  was a human decision.  It now presents its consequence to Governance and, on
  an ALLOW, records an approval whose decider IS Governance — named by its
  authorization id, receipted on Governance's own hash chain — and spends it on
  the very transition the kernel gates.  Nothing here ever writes
  ``decided_by="jeramie"``, ``caller_approved=True`` or a founder signature.

* An irreversible / external / spend / destructive mission (A5/A6, R4/R5)
  stays parked.  Governance's answer is ``founder_required`` with ONE concise
  ``founder_prompt``; the resolver surfaces that prompt and publishes the exact
  authorization request the founder's later signature must bind to.  It does
  not retry, does not lower the class, and does not invent a grant.

This module is pure arithmetic over data.  It performs no I/O, holds no secret,
and lives inside the AST-confined founder-runtime graph on purpose: the code
that decides how a mission asks for authority must be the code that cannot
manufacture any.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from recipe_engine.founder_runtime.adapters.governance_authorization import (
    FACTS_SCHEMA_VERSION,
    FOUNDER_RUNTIME_COMPONENT,
    OBSERVATION_REPOSITORY,
    OBSERVATION_TARGET,
    scope_key_for,
)
from recipe_engine.founder_runtime.contracts.approval import (
    SCHEMA_APPROVAL,
    Approval,
    ApprovalDecision,
)
from recipe_engine.founder_runtime.contracts.authority import AuthorityClass, AuthorityRequirement
from recipe_engine.founder_runtime.ids import hash_payload, stable_id

RESOLVER_VERSION = "fr.approval_resolver.v1"
RESOLVER_ACTOR = "founder_runtime.approval_resolver"
RESOLVER_LANE = "founder-runtime-approval-resolver"
RESOLVER_STEP = "approval_resolver"

# An approval the resolver records is decided by Governance and says so. The
# prefix is what lets every other reader tell it apart from a human decision.
GOVERNANCE_DECIDER_PREFIX = "governance:"

# Closed outcome vocabulary.
RESOLVED_QUEUED = "resolved_queued"       # Governance allowed; approval spent; mission QUEUED
FOUNDER_REQUIRED = "founder_required"     # Governance asks for the founder; mission stays parked
BLOCKED_POLICY = "blocked_policy"         # Governance denied the consequence; mission BLOCKED_POLICY
UNRESOLVED = "unresolved"                 # authority organ unreachable / identity unqualified; parked
OUTCOMES = (RESOLVED_QUEUED, FOUNDER_REQUIRED, BLOCKED_POLICY, UNRESOLVED)

# Governance denials that are about the RUNTIME or the REQUEST, not about the
# mission's consequence. They park the mission (someone must re-qualify the
# runtime, ratify mutation, fix the clock) rather than block it on policy —
# blocking would require a human approval to undo a configuration problem.
INFRASTRUCTURE_DENIALS = frozenset({
    "IDENTITY_UNKNOWN", "SOURCE_SHA_MISMATCH", "SOURCE_SHA_SUPERSEDED",
    "MUTATION_NOT_RATIFIED", "RISK_TIER_EXCEEDED", "ACTION_CLASS_NOT_ADMITTED",
    "RECEIPT_CHAIN_POISONED", "RECEIPT_PERSISTENCE_FAILED",
    "CANONICALIZATION_FAILED", "MALFORMED_FACTS", "FACTS_TIME_INVALID",
    "FACTS_EXPIRED", "NONCE_REPLAYED",
})


@dataclass(frozen=True)
class Consequence:
    """Governance's consequence coordinates for one trusted authority class."""

    action_class: str
    target_type: str
    target_id: str            # "" => the mission id at build time
    reversibility: str
    blast_radius: str
    production_effect: str
    externality: str = "none"
    repository: str | None = None
    branch_template: str | None = None   # "{mission_id}" substituted at build time
    rollback_plan: str | None = None


# Trusted authority class -> consequence. Closed and monotone: every entry names
# the MOST consequential thing the class admits, never the least, so the tier
# Governance derives can only be too high, never too low. Unknown fails closed.
#
#   A0 read-only allowlisted status   -> status         (R0, auto-allow)
#   A1 local reversible analysis      -> read           (R0, auto-allow)
#   A2 local code/document mutation   -> artifact_write (R1, grant)
#   A3 contained governed execution   -> test_run       (R2, grant + discipline)
#   A4 commit/persistent local change -> git_commit     (R2, grant + feature branch)
#   A5 external/spend/production      -> publish        (R4, founder once)
#   A6 destructive/high-risk          -> broad_delete   (R5, founder interactively)
CONSEQUENCE_BY_CLASS: dict[AuthorityClass, Consequence] = {
    AuthorityClass.A0: Consequence(
        action_class="status", target_type=OBSERVATION_TARGET["type"], target_id=OBSERVATION_TARGET["id"],
        reversibility="reversible", blast_radius="single_target", production_effect="read",
        repository=OBSERVATION_REPOSITORY,
    ),
    AuthorityClass.A1: Consequence(
        action_class="read", target_type="observation", target_id="local_read_analysis",
        reversibility="reversible", blast_radius="single_target", production_effect="read",
        repository=OBSERVATION_REPOSITORY,
    ),
    AuthorityClass.A2: Consequence(
        action_class="artifact_write", target_type="mission", target_id="",
        reversibility="reversible", blast_radius="bounded", production_effect="none",
    ),
    AuthorityClass.A3: Consequence(
        action_class="test_run", target_type="mission", target_id="",
        reversibility="reversible", blast_radius="bounded", production_effect="none",
    ),
    AuthorityClass.A4: Consequence(
        action_class="git_commit", target_type="mission", target_id="",
        reversibility="rollback_known", blast_radius="bounded", production_effect="none",
        repository=OBSERVATION_REPOSITORY, branch_template="mission/{mission_id}",
        rollback_plan="reset the mission branch to its recorded prior head; a protected branch is never written",
    ),
    AuthorityClass.A5: Consequence(
        action_class="publish", target_type="mission", target_id="",
        reversibility="irreversible", blast_radius="bounded", production_effect="none",
        externality="publication",
    ),
    AuthorityClass.A6: Consequence(
        action_class="broad_delete", target_type="mission", target_id="",
        reversibility="irreversible", blast_radius="broad", production_effect="none",
    ),
}


class ConsequenceUnclassified(ValueError):
    """An authority class this resolver has no consequence for. Fails closed."""


def consequence_for(requirement: AuthorityRequirement) -> Consequence:
    try:
        return CONSEQUENCE_BY_CLASS[requirement.authority_class]
    except KeyError as exc:  # pragma: no cover - the enum is closed, but never guess
        raise ConsequenceUnclassified(str(requirement.authority_class)) from exc


def resolver_nonce(mission_id: str, issued_at: str, attempt: str = "") -> str:
    """A fresh, url-safe, 32-char facts nonce. Governance spends it exactly once."""
    return "rn_" + hash_payload({"m": mission_id, "t": issued_at, "a": attempt})[:29]


def resolver_facts(
    mission_id: str,
    requirement: AuthorityRequirement,
    *,
    action: str,
    scope: str,
    source_sha: str,
    nonce: str,
    issued_at: str,
) -> dict[str, Any]:
    """The exact ``gov.action.facts.v1`` this mission presents for its gate.

    ``source_sha`` is the runtime identity this process honestly attests; the
    resolver never derives it and never defaults it. ``action``/``scope`` are the
    kernel's own gate vocabulary so the facts, the published request and the
    approval that may later be signed all describe one transition.
    """
    if not source_sha or len(source_sha) < 7:
        raise ValueError("resolver facts require an attested source SHA")
    consequence = consequence_for(requirement)
    target_id = consequence.target_id or mission_id
    facts: dict[str, Any] = {
        "schema_version": FACTS_SCHEMA_VERSION,
        "action_id": stable_id("govauthz", mission_id, action, scope, RESOLVER_LANE),
        "lane": RESOLVER_LANE,
        "component": FOUNDER_RUNTIME_COMPONENT,
        "source_sha": source_sha,
        "action_class": consequence.action_class,
        "summary": f"{action} [{scope}] {requirement.authority_class.value}:{requirement.reason_code}",
        "target": {"type": consequence.target_type, "id": target_id},
        "externality": consequence.externality,
        "reversibility": consequence.reversibility,
        "blast_radius": consequence.blast_radius,
        "production_effect": consequence.production_effect,
        "spend_usd": 0,
        "nonce": nonce,
        "issued_at": issued_at,
    }
    if consequence.repository:
        facts["repository"] = consequence.repository
    if consequence.branch_template:
        facts["branch"] = consequence.branch_template.format(mission_id=mission_id)
    if consequence.rollback_plan:
        facts["rollback_plan"] = consequence.rollback_plan
    return facts


def resolver_scope_key(facts: Mapping[str, Any]) -> str:
    """Governance's scope key for these facts, computed the way Governance does."""
    return scope_key_for(facts)


@dataclass
class Resolution:
    """What the resolver concluded from one Governance envelope."""

    outcome: str
    reason_code: str
    authorization_id: str | None = None
    risk_tier: str | None = None
    scope_key: str | None = None
    founder_prompt: dict[str, Any] | None = None
    approval: Approval | None = None
    evidence_refs: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolver_version": RESOLVER_VERSION,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "authorization_id": self.authorization_id,
            "risk_tier": self.risk_tier,
            "scope_key": self.scope_key,
            "founder_prompt": dict(self.founder_prompt) if self.founder_prompt else None,
            "approval_id": self.approval.approval_id if self.approval else None,
            "evidence_refs": list(self.evidence_refs),
            "detail": dict(self.detail),
        }


def governance_decided_approval(
    envelope: Mapping[str, Any],
    *,
    mission_id: str,
    requirement: AuthorityRequirement,
    action: str,
    scope: str,
    now_iso: str,
) -> Approval:
    """The approval record a Governance ALLOW backs.

    Its decider is Governance, named by the authorization id, and its evidence
    is Governance's receipt — verifiable by anyone with the chain, and by
    nobody's say-so.  It expires when Governance's decision does, so a record
    that somehow outlives the decision cannot be spent later.
    """
    authorization_id = str(envelope["authorization_id"])
    reason_code = str(envelope.get("reason_code") or "")
    risk_tier = str(envelope.get("risk_tier") or "")
    evidence = [
        f"governance:authorization:{authorization_id}",
        f"governance:receipt:{envelope.get('decision_receipt_hash') or ''}",
        f"governance:facts:{envelope.get('canonical_facts_hash') or ''}",
        f"governance:risk:{risk_tier}",
    ]
    return Approval(
        schema_version=SCHEMA_APPROVAL,
        approval_id=stable_id("appr", mission_id, "resolver", authorization_id),
        mission_id=mission_id,
        action_class=requirement.authority_class.value,
        exact_requested_action=action,
        scope=scope,
        requested_by=RESOLVER_ACTOR,
        decided_by=GOVERNANCE_DECIDER_PREFIX + authorization_id,
        decision=ApprovalDecision.ALLOW,
        conditions=[f"reason_code:{reason_code}", f"risk_tier:{risk_tier}"],
        expiry=str(envelope.get("expires_at") or "") or None,
        evidence_refs=evidence,
        created_at=now_iso,
        decided_at=str(envelope.get("issued_at") or now_iso),
        idempotency_key=f"resolver:{mission_id}:{authorization_id}",
    )


def is_governance_decided(approval: Approval) -> bool:
    return (
        approval.requested_by == RESOLVER_ACTOR
        and isinstance(approval.decided_by, str)
        and approval.decided_by.startswith(GOVERNANCE_DECIDER_PREFIX)
    )


def resolve_envelope(
    envelope: Mapping[str, Any],
    *,
    mission_id: str,
    requirement: AuthorityRequirement,
    action: str,
    scope: str,
    now_iso: str,
    external_approval_id: str | None = None,
) -> Resolution:
    """Map one Governance authorization envelope to a resolver outcome.

    ``external_approval_id`` names a human gate decision that was PRESENTED to
    Governance (as its signed artifact) for this request.  When Governance
    allows on that basis the human's approval is the one to spend, so no
    Governance-decided record is created alongside it.
    """
    decision = str(envelope.get("decision") or "")
    reason_code = str(envelope.get("reason_code") or "UNSPECIFIED")
    authorization_id = envelope.get("authorization_id")
    risk_tier = envelope.get("risk_tier")
    scope_key = envelope.get("scope_key")
    base = dict(
        reason_code=reason_code,
        authorization_id=str(authorization_id) if authorization_id else None,
        risk_tier=str(risk_tier) if risk_tier else None,
        scope_key=str(scope_key) if scope_key else None,
    )
    if not authorization_id:
        return Resolution(outcome=UNRESOLVED, **{**base, "reason_code": "GOVERNANCE_ENVELOPE_UNREFERENCED"},
                          detail={"decision": decision})
    if decision == "allow":
        if str(envelope.get("execution") or "") != "NOT_CLAIMED_BY_GOVERNANCE":
            return Resolution(outcome=UNRESOLVED, **{**base, "reason_code": "GOVERNANCE_CLAIMS_EXECUTION"})
        approval = None
        if external_approval_id is None:
            approval = governance_decided_approval(
                envelope, mission_id=mission_id, requirement=requirement,
                action=action, scope=scope, now_iso=now_iso,
            )
            if approval.expiry and approval.expiry <= now_iso:
                return Resolution(outcome=UNRESOLVED, **{**base, "reason_code": "GOVERNANCE_DECISION_EXPIRED"})
        return Resolution(
            outcome=RESOLVED_QUEUED, approval=approval,
            evidence_refs=[f"governance:authorization:{authorization_id}"],
            detail={"spent_approval": external_approval_id or (approval.approval_id if approval else None),
                    "decided_by": "founder" if external_approval_id else "governance"},
            **base,
        )
    if decision == "founder_required":
        prompt = envelope.get("founder_prompt")
        return Resolution(
            outcome=FOUNDER_REQUIRED,
            founder_prompt=dict(prompt) if isinstance(prompt, Mapping) else None,
            evidence_refs=[f"governance:authorization:{authorization_id}"],
            detail={"reasons": list(envelope.get("reasons") or [])},
            **base,
        )
    if decision == "deny":
        outcome = UNRESOLVED if reason_code in INFRASTRUCTURE_DENIALS else BLOCKED_POLICY
        return Resolution(
            outcome=outcome,
            evidence_refs=[f"governance:authorization:{authorization_id}"],
            detail={"reasons": list(envelope.get("reasons") or []),
                    "infrastructure": reason_code in INFRASTRUCTURE_DENIALS},
            **base,
        )
    return Resolution(outcome=UNRESOLVED, **{**base, "reason_code": "GOVERNANCE_DECISION_UNKNOWN"},
                      detail={"decision": decision})


def unresolved(reason_code: str, **detail: Any) -> Resolution:
    """A parked outcome that names why. Never an allow."""
    return Resolution(outcome=UNRESOLVED, reason_code=reason_code, detail=dict(detail))


__all__ = [
    "BLOCKED_POLICY", "CONSEQUENCE_BY_CLASS", "Consequence", "ConsequenceUnclassified",
    "FOUNDER_REQUIRED", "GOVERNANCE_DECIDER_PREFIX", "INFRASTRUCTURE_DENIALS", "OUTCOMES",
    "RESOLVED_QUEUED", "RESOLVER_ACTOR", "RESOLVER_LANE", "RESOLVER_STEP", "RESOLVER_VERSION",
    "Resolution", "UNRESOLVED", "consequence_for", "governance_decided_approval",
    "is_governance_decided", "resolve_envelope", "resolver_facts", "resolver_nonce",
    "resolver_scope_key", "unresolved",
]
