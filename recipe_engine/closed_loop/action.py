"""Action intent, and the receipt that says what actually happened.

An action is declared *before* it is taken, in enough detail that the execution
can be judged against it rather than described after the fact. The intent
carries what, why, target, capability, expected effect, risk, cost,
reversibility, authority and an idempotency key; the receipt binds to the
intent digest and records the observed result.

`ACTION_EXECUTED` without a bound intent is the exact shape of an action nobody
authorised, so `bind_execution` refuses it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.decision import AuthorityRef, DecisionError
from recipe_engine.closed_loop.identity import canonical_json, digest


SCHEMA_INTENT = "neuruh.closed-loop.action-intent.v1"
SCHEMA_RECEIPT = "neuruh.closed-loop.action-receipt.v1"


class Reversibility(str, Enum):
    REVERSIBLE = "reversible"
    PARTIALLY_REVERSIBLE = "partially_reversible"
    IRREVERSIBLE = "irreversible"


class RiskClass(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExecutionStatus(str, Enum):
    EXECUTED = "executed"
    FAILED = "failed"
    REFUSED = "refused"


class ActionError(ValueError):
    """The action cannot be declared or bound as stated."""


@dataclass(frozen=True)
class ExpectedEffect:
    """What the world should look like afterwards, stated measurably.

    Free-text hopes are not effects. Each expected effect names a fact key and
    the value the action is meant to produce, so the observation can disagree.
    """
    fact_key: str
    expected_value: Any
    confidence: Decimal | None = None

    def __post_init__(self) -> None:
        if not str(self.fact_key).strip():
            raise ActionError("expected_effect.fact_key must be non-empty")
        if self.confidence is not None and not (Decimal(0) <= self.confidence <= Decimal(1)):
            raise ActionError("expected_effect.confidence must be within [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return {"fact_key": self.fact_key, "expected_value": self.expected_value,
                "confidence": None if self.confidence is None else str(self.confidence)}


@dataclass(frozen=True)
class ActionIntent:
    """Declared before execution. The thing the receipt is judged against."""
    action_id: str
    decision_id: str
    subject_id: str
    what: str
    why_reason_codes: tuple[str, ...]
    target: str
    capability: str
    expected_effects: tuple[ExpectedEffect, ...]
    risk: RiskClass
    cost_estimate: Mapping[str, Any]
    reversibility: Reversibility
    authority: AuthorityRef
    idempotency_key: str
    declared_at: str

    def __post_init__(self) -> None:
        for name in ("action_id", "decision_id", "subject_id", "what", "target",
                     "capability", "idempotency_key", "declared_at"):
            if not str(getattr(self, name)).strip():
                raise ActionError(f"intent.{name} must be non-empty")
        if not self.why_reason_codes:
            raise ActionError("an action must state why, as reason codes")
        if not self.expected_effects:
            raise ActionError(
                "an action with no expected effect cannot be judged; state one")
        if not isinstance(self.risk, RiskClass):
            raise ActionError("intent.risk must be a RiskClass")
        if not isinstance(self.reversibility, Reversibility):
            raise ActionError("intent.reversibility must be a Reversibility")
        if not isinstance(self.authority, AuthorityRef):
            raise ActionError("an action intent must carry an AuthorityRef")
        if self.reversibility is Reversibility.IRREVERSIBLE and self.risk is not RiskClass.HIGH:
            raise ActionError(
                "an irreversible action is high risk by construction; say so")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_INTENT,
            "action_id": self.action_id,
            "decision_id": self.decision_id,
            "subject_id": self.subject_id,
            "what": self.what,
            "why_reason_codes": list(self.why_reason_codes),
            "target": self.target,
            "capability": self.capability,
            "expected_effects": [e.to_dict() for e in self.expected_effects],
            "risk": self.risk.value,
            "cost_estimate": dict(self.cost_estimate),
            "reversibility": self.reversibility.value,
            "authority": self.authority.to_dict(),
            "idempotency_key": self.idempotency_key,
            "declared_at": self.declared_at,
        }

    @property
    def digest(self) -> str:
        return digest(self.to_dict())


@dataclass(frozen=True)
class ActionExecutionReceipt:
    """What happened, bound to what was declared."""
    receipt_id: str
    intent_digest: str
    action_id: str
    status: ExecutionStatus
    started_at: str
    finished_at: str
    observed_effects: Mapping[str, Any]
    actual_cost: Mapping[str, Any]
    error_codes: tuple[str, ...] = ()
    external_reference: str | None = None

    def __post_init__(self) -> None:
        for name in ("receipt_id", "intent_digest", "action_id", "started_at", "finished_at"):
            if not str(getattr(self, name)).strip():
                raise ActionError(f"receipt.{name} must be non-empty")
        if not isinstance(self.status, ExecutionStatus):
            raise ActionError("receipt.status must be an ExecutionStatus")
        if str(self.finished_at) < str(self.started_at):
            raise ActionError("receipt.finished_at precedes started_at")
        if self.status is not ExecutionStatus.EXECUTED and not self.error_codes:
            raise ActionError(
                f"a {self.status.value} action must state error codes")
        if self.status is ExecutionStatus.EXECUTED and self.error_codes:
            raise ActionError("an executed action may not also carry error codes")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_RECEIPT,
            "receipt_id": self.receipt_id,
            "intent_digest": self.intent_digest,
            "action_id": self.action_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "observed_effects": dict(self.observed_effects),
            "actual_cost": dict(self.actual_cost),
            "error_codes": list(self.error_codes),
            "external_reference": self.external_reference,
        }

    @property
    def digest(self) -> str:
        """The run receipt digest the outcome record binds back to."""
        return digest(self.to_dict())


def bind_execution(intent: ActionIntent, receipt: ActionExecutionReceipt) -> dict[str, Any]:
    """Prove a receipt belongs to its intent, and report effect-by-effect.

    Returns the comparison rather than a verdict: whether an unmet expectation
    is a failure is a judgement for outcome and attribution, not for the
    binding step.
    """
    if receipt.intent_digest != intent.digest:
        raise ActionError(
            "receipt does not bind to this intent — an execution with no declared "
            "intent is an action nobody authorised")
    if receipt.action_id != intent.action_id:
        raise ActionError("receipt.action_id does not match the intent")
    comparison = []
    for effect in intent.expected_effects:
        observed = receipt.observed_effects.get(effect.fact_key, None)
        comparison.append({
            "fact_key": effect.fact_key,
            "expected": effect.expected_value,
            "observed": observed,
            "observed_present": effect.fact_key in receipt.observed_effects,
            "matched": (effect.fact_key in receipt.observed_effects
                        and observed == effect.expected_value),
        })
    return {
        "intent_digest": intent.digest,
        "receipt_digest": receipt.digest,
        "status": receipt.status.value,
        "effects": comparison,
        "unobserved_effects": [c["fact_key"] for c in comparison if not c["observed_present"]],
    }
