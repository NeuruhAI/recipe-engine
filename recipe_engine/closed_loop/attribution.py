"""Attribution — what an outcome is allowed to be credited to.

"Recipe X caused the sale" is almost never true and always tempting. A single
sale usually involves a source that found the parcel, a signal that flagged the
distress, a recipe that scored it, a decision that pursued it, an action that
made contact, and an operator who closed it. Crediting one of those and
retiring the rest is how a learning system convinces itself of a story.

So attribution is graded, multi-contributor, and refuses to invent shares:

* **DIRECT** — a bound chain exists: this contributor's artifact digest is on
  the path from decision to outcome, and the outcome could not have occurred
  without it as recorded.
* **SUPPORTED** — bound in the chain, but the outcome had other sufficient
  contributors; it helped and can be shown to have.
* **ASSOCIATED** — present in the lineage with no demonstrated dependency.
  Correlation, honestly labelled.
* **UNKNOWN** — a contributor we can name but cannot place.

A `share` may only be stated when an `allocation_basis` is declared. Absent
that, shares are `None` — because an evenly-split guess looks exactly like a
measurement and will be read as one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.identity import canonical_json, digest


SCHEMA_ATTRIBUTION = "neuruh.closed-loop.attribution.v1"


class AttributionStrength(str, Enum):
    DIRECT = "direct"
    SUPPORTED = "supported"
    ASSOCIATED = "associated"
    UNKNOWN = "unknown"


#: Strengths that may be used as evidence for a learning proposal. ASSOCIATED
#: and UNKNOWN are recorded, reportable, and explicitly not learnable — that is
#: the whole reason for grading them separately.
LEARNABLE_STRENGTHS: frozenset[AttributionStrength] = frozenset({
    AttributionStrength.DIRECT, AttributionStrength.SUPPORTED,
})


class ContributorKind(str, Enum):
    SOURCE = "source"
    SIGNAL = "signal"
    RECIPE_VERSION = "recipe_version"
    DECISION = "decision"
    ACTION = "action"
    OPERATOR = "operator"
    EXTERNAL = "external"


class AttributionError(ValueError):
    """The attribution cannot be stated as claimed."""


@dataclass(frozen=True)
class Contribution:
    """One contributor, one strength, one stated basis."""
    contributor_kind: ContributorKind
    contributor_key: str
    strength: AttributionStrength
    basis: str
    bound_digest: str | None = None
    share: Decimal | None = None
    allocation_basis: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.contributor_kind, ContributorKind):
            raise AttributionError("contributor_kind must be a ContributorKind")
        if not str(self.contributor_key).strip():
            raise AttributionError("contributor_key must be non-empty")
        if not isinstance(self.strength, AttributionStrength):
            raise AttributionError("strength must be an AttributionStrength")
        if not str(self.basis).strip():
            raise AttributionError(
                "every contribution states its basis — attribution without a "
                "stated basis is folklore")
        if self.strength in (AttributionStrength.DIRECT, AttributionStrength.SUPPORTED):
            if not self.bound_digest:
                raise AttributionError(
                    f"{self.strength.value} attribution requires a bound_digest on "
                    "the recorded path; without one the strongest honest claim is "
                    "'associated'")
        if self.share is not None:
            if not self.allocation_basis or not str(self.allocation_basis).strip():
                raise AttributionError(
                    "a share requires a declared allocation_basis; an assumed split "
                    "reads as a measurement")
            if not (Decimal(0) <= self.share <= Decimal(1)):
                raise AttributionError("share must be within [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contributor_kind": self.contributor_kind.value,
            "contributor_key": self.contributor_key,
            "strength": self.strength.value,
            "basis": self.basis,
            "bound_digest": self.bound_digest,
            "share": None if self.share is None else str(self.share),
            "allocation_basis": self.allocation_basis,
        }


@dataclass(frozen=True)
class AttributionSet:
    """Everything credited for one outcome, and what is left unexplained."""
    attribution_id: str
    outcome_id: str
    outcome_digest: str
    contributions: tuple[Contribution, ...]
    unexplained: tuple[str, ...]
    attributed_at: str

    def __post_init__(self) -> None:
        for name in ("attribution_id", "outcome_id", "outcome_digest", "attributed_at"):
            if not str(getattr(self, name)).strip():
                raise AttributionError(f"attribution.{name} must be non-empty")
        if not self.contributions:
            raise AttributionError(
                "an attribution with no contributions explains nothing; record an "
                "UNKNOWN contribution instead of an empty set")
        shares = [c.share for c in self.contributions if c.share is not None]
        if shares and sum(shares) > Decimal("1.0000001"):
            raise AttributionError("declared shares exceed 1.0")

    def by_strength(self, strength: AttributionStrength) -> tuple[Contribution, ...]:
        return tuple(c for c in self.contributions if c.strength is strength)

    def learnable(self) -> tuple[Contribution, ...]:
        """The subset a learning proposal may cite as evidence."""
        return tuple(c for c in self.contributions if c.strength in LEARNABLE_STRENGTHS)

    def contributors_of(self, kind: ContributorKind) -> tuple[Contribution, ...]:
        return tuple(c for c in self.contributions if c.contributor_kind is kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_ATTRIBUTION,
            "attribution_id": self.attribution_id,
            "outcome_id": self.outcome_id,
            "outcome_digest": self.outcome_digest,
            "contributions": [c.to_dict() for c in self.contributions],
            "unexplained": list(self.unexplained),
            "attributed_at": self.attributed_at,
        }

    @property
    def digest(self) -> str:
        return digest(self.to_dict())


def strength_for(*, bound_digest: str | None, other_sufficient_contributors: bool,
                 dependency_demonstrated: bool) -> AttributionStrength:
    """The grading rule, in one place so it is argued with once.

    * Not on the recorded path at all → ASSOCIATED at best; UNKNOWN if we
      cannot even say where it sits.
    * On the path, dependency demonstrated, nothing else sufficient → DIRECT.
    * On the path, dependency demonstrated, others also sufficient → SUPPORTED.
    * On the path with no demonstrated dependency → ASSOCIATED.
    """
    if not bound_digest:
        return AttributionStrength.ASSOCIATED if dependency_demonstrated else AttributionStrength.UNKNOWN
    if not dependency_demonstrated:
        return AttributionStrength.ASSOCIATED
    return (AttributionStrength.SUPPORTED if other_sufficient_contributors
            else AttributionStrength.DIRECT)
