"""Source strategy, made learnable.

The operational question is narrow and valuable: given this kind of subject,
which source should we try first? Today that ordering is a habit. This module
turns it into a measured cell — (owner class × source) — carrying identity
yield, high-confidence yield, manual minutes, false-positive rate and cost.

Ranking is honest about what it does not know. A cell under its sample floor is
returned as `insufficient_evidence` and is *not* interleaved with measured
cells, because an untried source with two lucky hits will otherwise sort to the
top and stay there — the classic bandit failure where exploration is punished
by its own early noise.

The output is a *recommendation with numbers attached*, and it becomes a change
only by way of a SOURCE_ORDERING learning proposal through the promotion gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from recipe_engine.closed_loop.identity import canonical_json, digest


SCHEMA_SOURCE_YIELD = "neuruh.closed-loop.source-yield.v1"

#: A source cell needs this many attempts before its rate means anything.
MIN_ATTEMPTS_FOR_RANKING = 30


class SourceLearningError(ValueError):
    """The source statistics cannot be computed or ranked as asked."""


@dataclass(frozen=True)
class SourceAttempt:
    """One use of one source against one subject, and what it produced."""
    subject_id: str
    owner_class: str
    source_type: str
    identity_resolved: bool
    high_confidence: bool
    false_positive: bool
    manual_minutes: Decimal
    cost: Decimal
    attempted_at: str

    def __post_init__(self) -> None:
        for name in ("subject_id", "owner_class", "source_type", "attempted_at"):
            if not str(getattr(self, name)).strip():
                raise SourceLearningError(f"attempt.{name} must be non-empty")
        if self.manual_minutes < 0 or self.cost < 0:
            raise SourceLearningError("manual_minutes and cost must be non-negative")
        if self.high_confidence and not self.identity_resolved:
            raise SourceLearningError(
                "a high-confidence result that resolved no identity is a "
                "contradiction, not a strong result")
        if self.false_positive and not self.identity_resolved:
            raise SourceLearningError(
                "a false positive is a resolution that turned out wrong; an "
                "unresolved attempt is simply a miss")

    def to_dict(self) -> dict[str, Any]:
        return {"subject_id": self.subject_id, "owner_class": self.owner_class,
                "source_type": self.source_type,
                "identity_resolved": self.identity_resolved,
                "high_confidence": self.high_confidence,
                "false_positive": self.false_positive,
                "manual_minutes": str(self.manual_minutes), "cost": str(self.cost),
                "attempted_at": self.attempted_at}


@dataclass(frozen=True)
class SourceYieldCell:
    """One (owner_class, source) pair, measured — or explicitly not."""
    owner_class: str
    source_type: str
    attempts: int
    identity_yield: Decimal | None
    high_confidence_yield: Decimal | None
    false_positive_rate: Decimal | None
    mean_manual_minutes: Decimal | None
    mean_cost: Decimal | None
    cost_per_resolved_identity: Decimal | None
    sufficient: bool
    insufficiency_reason: str | None

    @property
    def key(self) -> str:
        return f"{self.owner_class}::{self.source_type}"

    def to_dict(self) -> dict[str, Any]:
        def s(v: Decimal | None) -> str | None:
            return None if v is None else str(v)
        return {"owner_class": self.owner_class, "source_type": self.source_type,
                "attempts": self.attempts, "identity_yield": s(self.identity_yield),
                "high_confidence_yield": s(self.high_confidence_yield),
                "false_positive_rate": s(self.false_positive_rate),
                "mean_manual_minutes": s(self.mean_manual_minutes),
                "mean_cost": s(self.mean_cost),
                "cost_per_resolved_identity": s(self.cost_per_resolved_identity),
                "sufficient": self.sufficient,
                "insufficiency_reason": self.insufficiency_reason}


@dataclass(frozen=True)
class SourceRanking:
    """"Try this first", with the evidence and the caveats attached."""
    owner_class: str
    ranked: tuple[SourceYieldCell, ...]
    unranked: tuple[SourceYieldCell, ...]
    ranking_basis: str
    computed_at: str

    def recommended_first(self) -> SourceYieldCell | None:
        return self.ranked[0] if self.ranked else None

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_SOURCE_YIELD, "owner_class": self.owner_class,
                "ranked": [c.to_dict() for c in self.ranked],
                "unranked": [c.to_dict() for c in self.unranked],
                "ranking_basis": self.ranking_basis, "computed_at": self.computed_at}

    @property
    def digest(self) -> str:
        return digest(self.to_dict())


def _rate(numerator: int, denominator: int) -> Decimal:
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


def _mean(values: Sequence[Decimal]) -> Decimal:
    return (sum(values) / Decimal(len(values))).quantize(Decimal("0.0001"))


def build_cells(attempts: Sequence[SourceAttempt], *,
                min_attempts: int = MIN_ATTEMPTS_FOR_RANKING) -> tuple[SourceYieldCell, ...]:
    """Fold attempts into cells. Nothing is dropped; thin cells are labelled."""
    grouped: dict[tuple[str, str], list[SourceAttempt]] = {}
    for attempt in attempts:
        grouped.setdefault((attempt.owner_class, attempt.source_type), []).append(attempt)

    cells: list[SourceYieldCell] = []
    for (owner_class, source_type) in sorted(grouped):
        members = grouped[(owner_class, source_type)]
        count = len(members)
        resolved = sum(1 for m in members if m.identity_resolved)
        if count < min_attempts:
            cells.append(SourceYieldCell(
                owner_class=owner_class, source_type=source_type, attempts=count,
                identity_yield=None, high_confidence_yield=None,
                false_positive_rate=None, mean_manual_minutes=None, mean_cost=None,
                cost_per_resolved_identity=None, sufficient=False,
                insufficiency_reason=(
                    f"{count} attempts is below the {min_attempts} floor; this "
                    "source is unranked, not bad")))
            continue
        total_cost = sum((m.cost for m in members), Decimal(0))
        cells.append(SourceYieldCell(
            owner_class=owner_class, source_type=source_type, attempts=count,
            identity_yield=_rate(resolved, count),
            high_confidence_yield=_rate(sum(1 for m in members if m.high_confidence), count),
            false_positive_rate=(_rate(sum(1 for m in members if m.false_positive), resolved)
                                 if resolved else Decimal("0.0000")),
            mean_manual_minutes=_mean([m.manual_minutes for m in members]),
            mean_cost=_mean([m.cost for m in members]),
            cost_per_resolved_identity=((total_cost / Decimal(resolved)).quantize(Decimal("0.0001"))
                                        if resolved else None),
            sufficient=True, insufficiency_reason=None))
    return tuple(cells)


def rank_sources(cells: Sequence[SourceYieldCell], *, owner_class: str,
                 computed_at: str) -> SourceRanking:
    """Order the measured cells; keep the unmeasured ones visible and separate.

    The order is: high-confidence yield descending, then false-positive rate
    ascending, then cost per resolved identity ascending, then manual minutes
    ascending, then source name — so the ordering is total and reproducible
    rather than dependent on dict order.

    High-confidence yield leads because a resolution nobody trusts still costs
    an operator the same minutes as one they do.
    """
    scoped = [c for c in cells if c.owner_class == owner_class]
    measured = [c for c in scoped if c.sufficient]
    unmeasured = [c for c in scoped if not c.sufficient]

    def sort_key(cell: SourceYieldCell) -> tuple[Any, ...]:
        return (
            -(cell.high_confidence_yield or Decimal(0)),
            cell.false_positive_rate if cell.false_positive_rate is not None else Decimal(1),
            (cell.cost_per_resolved_identity
             if cell.cost_per_resolved_identity is not None else Decimal(10 ** 9)),
            cell.mean_manual_minutes or Decimal(0),
            cell.source_type,
        )

    return SourceRanking(
        owner_class=owner_class,
        ranked=tuple(sorted(measured, key=sort_key)),
        unranked=tuple(sorted(unmeasured, key=lambda c: c.source_type)),
        ranking_basis=("high_confidence_yield desc, false_positive_rate asc, "
                       "cost_per_resolved_identity asc, mean_manual_minutes asc, "
                       "source_type asc"),
        computed_at=computed_at)
