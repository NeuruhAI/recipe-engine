"""Governance requirement proposals only. Not authority. Pure; no I/O."""

from __future__ import annotations

from .models import (
    SV_GOVERNANCE,
    CandidateStrategy,
    GovernanceProposalState,
    GovernanceRequirement,
    OpportunityEnvelope,
)

# Action classes that require elevated authority
IRREVERSIBLE_HINTS = frozenset({"irreversible_write", "irreversible", "deed", "title_transfer"})
COMM_HINTS = frozenset({"external_message", "email", "sms", "seller_contact", "public_publish"})
FINANCIAL_HINTS = frozenset({"financial_commit", "payment", "wire", "spend", "offer_bind"})
PROHIBITED_HINTS = frozenset({"prohibited", "illegal", "credential_exposure"})


def propose_governance(
    opportunity: OpportunityEnvelope,
    strategy: CandidateStrategy,
    *,
    policy_version: str = "proposal-v1",
) -> GovernanceRequirement:
    """
    Produce a GovernanceRequirement proposal.
    Real Governance remains authoritative and may override.
    Fail-closed on missing/unknown policy.
    """
    reasons: list[str] = []
    required_authority: list[str] = []
    required_evidence: list[str] = []
    prohibited: list[str] = []
    notes: list[str] = []

    actions = set(a.lower() for a in strategy.action_classes)
    gov_class = (strategy.governance_class or "").lower()

    if strategy.prohibited or any(h in actions for h in PROHIBITED_HINTS) or "prohibited" in gov_class:
        prohibited.extend(sorted(actions & PROHIBITED_HINTS) or ["prohibited"])
        return GovernanceRequirement(
            schema_version=SV_GOVERNANCE,
            state=GovernanceProposalState.BLOCKED,
            reason_codes=("prohibited_action",),
            required_authority=("governance",),
            required_evidence=tuple(strategy.evidence_gaps),
            prohibited_actions=tuple(prohibited or ("prohibited",)),
            expires_at=None,
            policy_version=policy_version,
            notes=("Recipe Engine proposal only; Governance authoritative.",),
        )

    if not strategy.policy_known or gov_class in ("", "unknown", "missing"):
        return GovernanceRequirement(
            schema_version=SV_GOVERNANCE,
            state=GovernanceProposalState.ESCALATE,
            reason_codes=("unknown_policy",),
            required_authority=("governance", "operator"),
            required_evidence=("policy_reference",),
            prohibited_actions=(),
            expires_at=None,
            policy_version=policy_version,
            notes=(
                "Missing/unknown policy fails closed: escalate, never allow.",
                "Recipe Engine proposal only; Governance authoritative.",
            ),
        )

    needs_approval = False
    escalate = False

    if strategy.reversibility == "irreversible" or actions & IRREVERSIBLE_HINTS:
        needs_approval = True
        reasons.append("irreversible_action")
        required_authority.append("operator_approval")

    if actions & COMM_HINTS:
        needs_approval = True
        reasons.append("external_communication")
        required_authority.append("comms_authority")

    if actions & FINANCIAL_HINTS or strategy.capital_intensity == "high":
        needs_approval = True
        reasons.append("financial_commitment")
        required_authority.append("financial_authority")

    sensitivity = str(opportunity.authority_context.get("sensitivity", "internal")).lower()
    if sensitivity in ("confidential", "restricted", "pii-local-only"):
        escalate = True
        reasons.append("sensitive_data")
        required_authority.append("data_steward")

    if strategy.evidence_gaps:
        required_evidence.extend(strategy.evidence_gaps)

    if "escalate" in gov_class:
        escalate = True
        reasons.append("governance_class_escalate")

    if "block" in gov_class:
        return GovernanceRequirement(
            schema_version=SV_GOVERNANCE,
            state=GovernanceProposalState.BLOCKED,
            reason_codes=tuple(sorted(set(reasons + ["governance_class_block"]))),
            required_authority=tuple(sorted(set(required_authority + ["governance"]))),
            required_evidence=tuple(sorted(set(required_evidence))),
            prohibited_actions=(),
            expires_at=None,
            policy_version=policy_version,
            notes=("Recipe Engine proposal only; Governance authoritative.",),
        )

    if escalate and not needs_approval:
        state = GovernanceProposalState.ESCALATE
    elif needs_approval:
        state = GovernanceProposalState.APPROVAL_REQUIRED
    else:
        state = GovernanceProposalState.ALLOW
        if not reasons:
            reasons.append("standard_path")

    notes.append("Recipe Engine proposal only; real Governance response overrides.")
    return GovernanceRequirement(
        schema_version=SV_GOVERNANCE,
        state=state,
        reason_codes=tuple(sorted(set(reasons))) if reasons else ("standard_path",),
        required_authority=tuple(sorted(set(required_authority))),
        required_evidence=tuple(sorted(set(required_evidence))),
        prohibited_actions=(),
        expires_at=None,
        policy_version=policy_version,
        notes=tuple(notes),
    )
