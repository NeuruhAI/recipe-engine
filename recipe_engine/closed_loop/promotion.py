"""The governed promotion loop — where learning is told "not yet" on purpose.

A learning proposal reaches a candidate version. It does not reach production.
Between the two sits this gate, whose only outputs are ELIGIBLE, HELD and
BLOCKED, and which never grants deployment authority under any input. That last
clause matters: eligibility is a statement about prerequisites, not a licence,
and the 020 Promotion Gate primitive this mirrors makes the same distinction.

The rule that is *added* here, and that the public primitive has no way to
know, is the **world-mode rule**: a proposal whose evidence lineage includes any
non-LIVE mode can traverse the entire loop and must stop at HELD. A synthetic
court can therefore be run end to end — which is how the mechanism gets proven
— without any path by which a fixture becomes a production change.

Canary is a stage, not an escape: `VersionPromotion` requires a canary
evaluation reference and an external approval reference, neither of which this
module can mint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.identity import PROMOTABLE_MODES, WorldMode, digest
from recipe_engine.closed_loop.learning import LearningProposal


SCHEMA_PROMOTION = "neuruh.closed-loop.promotion.v1"


class PromotionOutcome(str, Enum):
    ELIGIBLE = "eligible"
    HELD = "held"
    BLOCKED = "blocked"


class Stage(str, Enum):
    SANDBOX = "sandbox"
    CANARY = "canary"
    PILOT = "pilot"
    PRODUCTION = "production"


class PromotionError(ValueError):
    """The promotion cannot be evaluated or recorded as asked."""


@dataclass(frozen=True)
class PromotionPrerequisites:
    """What the proposal has actually got, stated as facts rather than hopes."""
    tests_passed: bool
    test_report_digest: str | None
    regression_count: int
    critical_regression_count: int
    human_approval_digest: str | None
    reversibility_contract_digest: str | None
    canary_evaluation_digest: str | None
    evidence_modes: tuple[WorldMode, ...]

    def __post_init__(self) -> None:
        if not self.evidence_modes:
            raise PromotionError(
                "evidence_modes must be stated; a proposal that cannot say whether "
                "its evidence is live is not evaluable")
        for mode in self.evidence_modes:
            if not isinstance(mode, WorldMode):
                raise PromotionError("evidence_modes must all be WorldMode values")
        for name in ("regression_count", "critical_regression_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PromotionError(f"{name} must be a non-negative integer")
        if self.critical_regression_count > self.regression_count:
            raise PromotionError("critical regressions cannot exceed known regressions")

    @property
    def non_live_modes(self) -> tuple[WorldMode, ...]:
        return tuple(sorted({m for m in self.evidence_modes if m not in PROMOTABLE_MODES},
                            key=lambda m: m.value))

    def to_dict(self) -> dict[str, Any]:
        return {"tests_passed": self.tests_passed,
                "test_report_digest": self.test_report_digest,
                "regression_count": self.regression_count,
                "critical_regression_count": self.critical_regression_count,
                "human_approval_digest": self.human_approval_digest,
                "reversibility_contract_digest": self.reversibility_contract_digest,
                "canary_evaluation_digest": self.canary_evaluation_digest,
                "evidence_modes": [m.value for m in self.evidence_modes]}


@dataclass(frozen=True)
class PromotionPolicy:
    """The declared bar. Mirrors 020's shape so a request projects cleanly."""
    policy_id: str
    min_sample_count: int = 30
    max_regressions: int = 0
    require_tests: bool = True
    require_human_approval: bool = True
    require_reversibility: bool = True
    require_canary_for_stages: tuple[Stage, ...] = (Stage.PILOT, Stage.PRODUCTION)
    allowed_stages: tuple[Stage, ...] = tuple(Stage)

    def to_dict(self) -> dict[str, Any]:
        return {"policy_id": self.policy_id, "min_sample_count": self.min_sample_count,
                "max_regressions": self.max_regressions, "require_tests": self.require_tests,
                "require_human_approval": self.require_human_approval,
                "require_reversibility": self.require_reversibility,
                "require_canary_for_stages": [s.value for s in self.require_canary_for_stages],
                "allowed_stages": [s.value for s in self.allowed_stages]}

    @property
    def version(self) -> str:
        return digest(self.to_dict())


@dataclass(frozen=True)
class PromotionDecision:
    """A verdict about prerequisites. Never a deployment."""
    decision_id: str
    proposal_digest: str
    policy_id: str
    policy_version: str
    requested_stage: Stage
    outcome: PromotionOutcome
    reasons: tuple[str, ...]
    decided_at: str
    deployment_authority: bool = False

    def __post_init__(self) -> None:
        if self.deployment_authority is not False:
            raise PromotionError(
                "a promotion decision never grants deployment authority")
        if not self.reasons:
            raise PromotionError("a promotion decision must state its reasons")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_PROMOTION, "decision_id": self.decision_id,
                "proposal_digest": self.proposal_digest, "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "requested_stage": self.requested_stage.value,
                "outcome": self.outcome.value, "reasons": list(self.reasons),
                "decided_at": self.decided_at,
                "deployment_authority": self.deployment_authority}

    @property
    def digest(self) -> str:
        return digest(self.to_dict())


def evaluate_promotion(
    proposal: LearningProposal,
    prerequisites: PromotionPrerequisites,
    policy: PromotionPolicy,
    *,
    decision_id: str,
    requested_stage: Stage,
    decided_at: str,
) -> PromotionDecision:
    """Structural violations BLOCK; missing evidence HOLDS; otherwise ELIGIBLE.

    The split matters operationally: a HOLD is a to-do list, a BLOCK is a
    statement that this change must not be made in this form at all.
    """
    blocking: list[str] = []
    if requested_stage not in policy.allowed_stages:
        blocking.append(f"stage not allowed by policy: {requested_stage.value}")
    if prerequisites.critical_regression_count > 0:
        blocking.append(
            f"critical regressions present: {prerequisites.critical_regression_count}")
    if proposal.auto_apply:
        blocking.append("proposal requests auto-apply, which is forbidden")
    if blocking:
        return PromotionDecision(decision_id, proposal.digest, policy.policy_id,
                                 policy.version, requested_stage,
                                 PromotionOutcome.BLOCKED, tuple(blocking), decided_at)

    holds: list[str] = []
    non_live = prerequisites.non_live_modes
    if non_live:
        holds.append(
            "evidence lineage includes non-live modes "
            f"({', '.join(m.value for m in non_live)}); a fixture may prove the "
            "mechanism and may never become a production change")
    if policy.require_tests and not prerequisites.tests_passed:
        holds.append("required tests did not pass")
    if policy.require_tests and prerequisites.test_report_digest is None:
        holds.append("required test report missing")
    if proposal.evidence_window.sample_count < policy.min_sample_count:
        holds.append(
            f"sample count {proposal.evidence_window.sample_count} below policy "
            f"minimum {policy.min_sample_count}")
    if proposal.evidence_window.sample_count < proposal.required_samples:
        holds.append(
            f"sample count {proposal.evidence_window.sample_count} below the "
            f"{proposal.required_samples} required for this change class")
    if prerequisites.regression_count > policy.max_regressions:
        holds.append(
            f"regressions {prerequisites.regression_count} exceed maximum "
            f"{policy.max_regressions}")
    if policy.require_human_approval and prerequisites.human_approval_digest is None:
        holds.append("required human approval missing")
    if policy.require_reversibility and prerequisites.reversibility_contract_digest is None:
        holds.append("required reversibility contract missing")
    if (requested_stage in policy.require_canary_for_stages
            and prerequisites.canary_evaluation_digest is None):
        holds.append(f"stage {requested_stage.value} requires a canary evaluation")

    if holds:
        return PromotionDecision(decision_id, proposal.digest, policy.policy_id,
                                 policy.version, requested_stage,
                                 PromotionOutcome.HELD, tuple(holds), decided_at)
    return PromotionDecision(
        decision_id, proposal.digest, policy.policy_id, policy.version, requested_stage,
        PromotionOutcome.ELIGIBLE,
        ("all declared promotion prerequisites satisfied; lifecycle eligibility "
         "only, no deployment authority",), decided_at)


@dataclass(frozen=True)
class VersionPromotion:
    """The record that a candidate became the version in force.

    It cannot be constructed from an ELIGIBLE decision alone. Eligibility says
    the prerequisites hold; promotion additionally requires an external
    approval that this module has no means of producing.
    """
    promotion_id: str
    proposal_digest: str
    promotion_decision_digest: str
    target_id: str
    from_version: str
    to_version: str
    external_approval_ref: str
    canary_evaluation_digest: str | None
    promoted_at: str
    rollback_ref: str

    def __post_init__(self) -> None:
        for name in ("promotion_id", "proposal_digest", "promotion_decision_digest",
                     "target_id", "from_version", "to_version", "external_approval_ref",
                     "promoted_at", "rollback_ref"):
            if not str(getattr(self, name)).strip():
                raise PromotionError(f"promotion.{name} must be non-empty")
        if self.from_version == self.to_version:
            raise PromotionError("a promotion must change the version in force")

    def to_dict(self) -> dict[str, Any]:
        return {"promotion_id": self.promotion_id, "proposal_digest": self.proposal_digest,
                "promotion_decision_digest": self.promotion_decision_digest,
                "target_id": self.target_id, "from_version": self.from_version,
                "to_version": self.to_version,
                "external_approval_ref": self.external_approval_ref,
                "canary_evaluation_digest": self.canary_evaluation_digest,
                "promoted_at": self.promoted_at, "rollback_ref": self.rollback_ref}

    @property
    def digest(self) -> str:
        return digest(self.to_dict())


def promote(decision: PromotionDecision, *, promotion_id: str, target_id: str,
            from_version: str, to_version: str, external_approval_ref: str,
            canary_evaluation_digest: str | None, promoted_at: str,
            rollback_ref: str) -> VersionPromotion:
    """Record a promotion, or refuse. Refuses on anything short of ELIGIBLE."""
    if decision.outcome is not PromotionOutcome.ELIGIBLE:
        raise PromotionError(
            f"cannot promote on a {decision.outcome.value} decision: "
            f"{'; '.join(decision.reasons)}")
    if not str(external_approval_ref).strip():
        raise PromotionError(
            "promotion requires an external approval reference; eligibility is "
            "not approval")
    return VersionPromotion(
        promotion_id=promotion_id, proposal_digest=decision.proposal_digest,
        promotion_decision_digest=decision.digest, target_id=target_id,
        from_version=from_version, to_version=to_version,
        external_approval_ref=external_approval_ref,
        canary_evaluation_digest=canary_evaluation_digest,
        promoted_at=promoted_at, rollback_ref=rollback_ref)
