"""The decision record — what was decided, on what, and under whose authority.

Two disciplines carried over from the governance lanes, restated here because
this is where they are easiest to lose:

* **Authority is not inferred.** A decision carries an explicit authority
  reference. Approval that cannot name its authority is not approval.
* **Rationale is declared, not narrated.** The record stores reason codes and
  the declared feature values that drove them. It does not store model
  reasoning — `events.FORBIDDEN_PAYLOAD_KEYS` makes that hard to do by
  accident, and `DecisionRecord` makes it hard to do on purpose.

Alternatives are recorded only when they exist as declared artifacts. An
invented list of "options considered" would be a story about deliberation
rather than a record of it, so `alternatives` may legitimately be empty and
`alternatives_declared` says which it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.events import FORBIDDEN_PAYLOAD_KEYS
from recipe_engine.closed_loop.identity import canonical_json, digest


SCHEMA_DECISION = "neuruh.closed-loop.decision.v1"
SCHEMA_ALTERNATIVE = "neuruh.closed-loop.alternative.v1"


class DecisionDisposition(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    BLOCKED = "blocked"


class GovernanceVerdict(str, Enum):
    """Mirrors the policy-gate vocabulary the governance organ already speaks."""
    ALLOW = "allow"
    APPROVAL_REQUIRED = "approval_required"
    ESCALATE = "escalate"
    BLOCKED = "blocked"


class DecisionError(ValueError):
    """The decision cannot be recorded as stated."""


@dataclass(frozen=True)
class AuthorityRef:
    """Who was entitled to decide this, and where that entitlement is written."""
    authority_id: str
    authority_kind: str          # operator | governance | delegated | policy
    granted_by: str
    reference: str               # the artifact that records the grant

    def __post_init__(self) -> None:
        for name in ("authority_id", "authority_kind", "granted_by", "reference"):
            if not str(getattr(self, name)).strip():
                raise DecisionError(
                    f"authority.{name} must be non-empty — authority is never inferred")

    def to_dict(self) -> dict[str, Any]:
        return {"authority_id": self.authority_id, "authority_kind": self.authority_kind,
                "granted_by": self.granted_by, "reference": self.reference}


@dataclass(frozen=True)
class Alternative:
    """A declared option that existed as an artifact, with why it lost."""
    alternative_id: str
    summary: str
    artifact_digest: str
    rejected_reason_code: str

    def __post_init__(self) -> None:
        for name in ("alternative_id", "summary", "artifact_digest", "rejected_reason_code"):
            if not str(getattr(self, name)).strip():
                raise DecisionError(f"alternative.{name} must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_ALTERNATIVE,
                "alternative_id": self.alternative_id, "summary": self.summary,
                "artifact_digest": self.artifact_digest,
                "rejected_reason_code": self.rejected_reason_code}


@dataclass(frozen=True)
class DeclaredRationale:
    """Reason codes plus the feature values that triggered them.

    `features` is a flat mapping of declared, named values — the things the
    decision was a function of. Anything that reads like a transcript is
    rejected rather than stored.
    """
    reason_codes: tuple[str, ...]
    features: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.reason_codes:
            raise DecisionError("a decision must state at least one reason code")
        for key in self.features:
            if str(key).strip().lower() in FORBIDDEN_PAYLOAD_KEYS:
                raise DecisionError(
                    f"feature {key!r} is un-declared model reasoning, not a feature")
            if isinstance(self.features[key], str) and len(self.features[key]) > 512:
                raise DecisionError(
                    f"feature {key!r} is prose, not a declared value; record a "
                    "reason code instead")

    def to_dict(self) -> dict[str, Any]:
        return {"reason_codes": list(self.reason_codes), "features": dict(self.features)}


@dataclass(frozen=True)
class DecisionRecord:
    """The join point of the loop: opportunity, evidence, evaluation, verdict."""
    decision_id: str
    subject_id: str
    opportunity_id: str
    recipe_evaluation_digest: str
    evidence_ids: tuple[str, ...]
    disposition: DecisionDisposition
    rationale: DeclaredRationale
    governance_verdict: GovernanceVerdict
    authority: AuthorityRef | None
    alternatives: tuple[Alternative, ...]
    alternatives_declared: bool
    decided_at: str
    world_state_delta_digest: str | None = None

    def __post_init__(self) -> None:
        for name in ("decision_id", "subject_id", "opportunity_id",
                     "recipe_evaluation_digest", "decided_at"):
            if not str(getattr(self, name)).strip():
                raise DecisionError(f"decision.{name} must be non-empty")
        if not isinstance(self.disposition, DecisionDisposition):
            raise DecisionError("disposition must be a DecisionDisposition")
        if not isinstance(self.governance_verdict, GovernanceVerdict):
            raise DecisionError("governance_verdict must be a GovernanceVerdict")
        if self.disposition is DecisionDisposition.APPROVED:
            if self.authority is None:
                raise DecisionError(
                    "an approved decision must name the authority that approved it")
            if self.governance_verdict is GovernanceVerdict.BLOCKED:
                raise DecisionError(
                    "a decision blocked by governance cannot be recorded as approved")
        if self.disposition is DecisionDisposition.BLOCKED and self.governance_verdict is GovernanceVerdict.ALLOW:
            raise DecisionError(
                "a decision blocked while governance allowed it must state a "
                "different governance verdict")
        if self.alternatives and not self.alternatives_declared:
            raise DecisionError(
                "alternatives present but not declared — say which they are")
        if self.alternatives_declared and not self.alternatives:
            raise DecisionError(
                "alternatives_declared=True with no alternatives; if none existed "
                "as artifacts, declare False rather than implying deliberation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_DECISION,
            "decision_id": self.decision_id,
            "subject_id": self.subject_id,
            "opportunity_id": self.opportunity_id,
            "recipe_evaluation_digest": self.recipe_evaluation_digest,
            "evidence_ids": list(self.evidence_ids),
            "disposition": self.disposition.value,
            "rationale": self.rationale.to_dict(),
            "governance_verdict": self.governance_verdict.value,
            "authority": None if self.authority is None else self.authority.to_dict(),
            "alternatives": [a.to_dict() for a in self.alternatives],
            "alternatives_declared": self.alternatives_declared,
            "world_state_delta_digest": self.world_state_delta_digest,
            "decided_at": self.decided_at,
        }

    @property
    def digest(self) -> str:
        """The decision receipt digest the outcome record binds back to."""
        return digest(self.to_dict())
