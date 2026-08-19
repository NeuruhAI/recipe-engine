"""Recipe learning — four questions that must not be collapsed into one number.

A recipe can find genuinely excellent opportunities whose owners are almost
impossible to reach. Averaged into a single "recipe score" that reads as a bad
recipe, and the system learns to stop looking where the value is. The four
tracks are therefore measured independently and reported independently:

* **OPPORTUNITY_QUALITY** — were these the right subjects at all?
* **CONTACTABILITY** — could an identity and a contact point be resolved?
* **CALLABILITY** — was contact permitted and attemptable (consent, DNC, hours)?
* **TRANSACTION_SUCCESS** — did the pursued ones convert?

`diagnose()` names which track is the constraint, because "the recipe is at
0.31" is not an instruction and "this recipe's opportunities are good but only
18% are contactable" is.

Each track carries its own denominator. Conflating them is the specific error
this module exists to prevent: contactability is measured over opportunities
produced, callability over contactable ones, transaction success over pursued
ones — so a small callable population cannot flatter or damn the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.identity import canonical_json, digest


SCHEMA_RECIPE_SCORECARD = "neuruh.closed-loop.recipe-scorecard.v1"

MIN_SAMPLES_FOR_TRACK = 30


class Track(str, Enum):
    OPPORTUNITY_QUALITY = "opportunity_quality"
    CONTACTABILITY = "contactability"
    CALLABILITY = "callability"
    TRANSACTION_SUCCESS = "transaction_success"


#: Each track's denominator, stated as data so the nesting is reviewable.
TRACK_DENOMINATOR: dict[Track, str] = {
    Track.OPPORTUNITY_QUALITY: "opportunities_produced",
    Track.CONTACTABILITY: "opportunities_produced",
    Track.CALLABILITY: "contactable",
    Track.TRANSACTION_SUCCESS: "pursued",
}


class RecipeLearningError(ValueError):
    """The scorecard cannot be built as stated."""


@dataclass(frozen=True)
class RecipeObservationCounts:
    """Raw counts for one recipe version over one window.

    Ordering constraints are enforced because a pipeline that reports more
    contactable subjects than it produced opportunities has a counting bug, and
    a counting bug that survives into learning becomes a policy.
    """
    opportunities_produced: int
    opportunities_accepted: int
    contactable: int
    callable_permitted: int
    pursued: int
    converted: int

    def __post_init__(self) -> None:
        for name in ("opportunities_produced", "opportunities_accepted", "contactable",
                     "callable_permitted", "pursued", "converted"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RecipeLearningError(f"{name} must be a non-negative integer")
        if self.opportunities_accepted > self.opportunities_produced:
            raise RecipeLearningError("accepted cannot exceed produced")
        if self.contactable > self.opportunities_produced:
            raise RecipeLearningError("contactable cannot exceed produced")
        if self.callable_permitted > self.contactable:
            raise RecipeLearningError("callable cannot exceed contactable")
        if self.pursued > self.callable_permitted:
            raise RecipeLearningError("pursued cannot exceed callable")
        if self.converted > self.pursued:
            raise RecipeLearningError("converted cannot exceed pursued")

    def to_dict(self) -> dict[str, Any]:
        return {"opportunities_produced": self.opportunities_produced,
                "opportunities_accepted": self.opportunities_accepted,
                "contactable": self.contactable,
                "callable_permitted": self.callable_permitted,
                "pursued": self.pursued, "converted": self.converted}


@dataclass(frozen=True)
class TrackResult:
    track: Track
    numerator: int
    denominator: int
    rate: Decimal | None
    sufficient: bool
    insufficiency_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"track": self.track.value, "numerator": self.numerator,
                "denominator": self.denominator,
                "denominator_name": TRACK_DENOMINATOR[self.track],
                "rate": None if self.rate is None else str(self.rate),
                "sufficient": self.sufficient,
                "insufficiency_reason": self.insufficiency_reason}


@dataclass(frozen=True)
class RecipeScorecard:
    """One recipe version, four independent tracks, never one number."""
    recipe_key: str
    window_start: str
    window_end: str
    counts: RecipeObservationCounts
    tracks: tuple[TrackResult, ...]
    computed_at: str

    def track(self, track: Track) -> TrackResult:
        for result in self.tracks:
            if result.track is track:
                return result
        raise RecipeLearningError(f"track not computed: {track.value}")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_RECIPE_SCORECARD, "recipe_key": self.recipe_key,
                "window_start": self.window_start, "window_end": self.window_end,
                "counts": self.counts.to_dict(),
                "tracks": [t.to_dict() for t in self.tracks],
                "computed_at": self.computed_at}

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    def composite(self) -> None:
        """Deliberately absent.

        Returning `None` rather than omitting the method is the point: someone
        will look for a single score, and this is where they are told why there
        isn't one. Use `diagnose()`.
        """
        return None


@dataclass(frozen=True)
class Diagnosis:
    """Which track is the constraint, and what that means for the recipe."""
    recipe_key: str
    constraint: Track | None
    verdict: str
    detail: str
    unmeasured_tracks: tuple[Track, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"recipe_key": self.recipe_key,
                "constraint": None if self.constraint is None else self.constraint.value,
                "verdict": self.verdict, "detail": self.detail,
                "unmeasured_tracks": [t.value for t in self.unmeasured_tracks]}


def _track_result(track: Track, numerator: int, denominator: int,
                  min_samples: int) -> TrackResult:
    if denominator < min_samples:
        return TrackResult(
            track=track, numerator=numerator, denominator=denominator, rate=None,
            sufficient=False,
            insufficiency_reason=(
                f"{denominator} in the {TRACK_DENOMINATOR[track]} denominator is "
                f"below the {min_samples} floor; no rate is claimed"))
    return TrackResult(
        track=track, numerator=numerator, denominator=denominator,
        rate=(Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001")),
        sufficient=True, insufficiency_reason=None)


def score_recipe(counts: RecipeObservationCounts, *, recipe_key: str,
                 window_start: str, window_end: str, computed_at: str,
                 min_samples: int = MIN_SAMPLES_FOR_TRACK) -> RecipeScorecard:
    """Compute all four tracks over their own denominators."""
    tracks = (
        _track_result(Track.OPPORTUNITY_QUALITY, counts.opportunities_accepted,
                      counts.opportunities_produced, min_samples),
        _track_result(Track.CONTACTABILITY, counts.contactable,
                      counts.opportunities_produced, min_samples),
        _track_result(Track.CALLABILITY, counts.callable_permitted,
                      counts.contactable, min_samples),
        _track_result(Track.TRANSACTION_SUCCESS, counts.converted,
                      counts.pursued, min_samples),
    )
    return RecipeScorecard(recipe_key=recipe_key, window_start=window_start,
                           window_end=window_end, counts=counts, tracks=tracks,
                           computed_at=computed_at)


def diagnose(scorecard: RecipeScorecard, *,
             quality_floor: Decimal = Decimal("0.50"),
             rate_floor: Decimal = Decimal("0.40")) -> Diagnosis:
    """Name the binding constraint, in the order the pipeline runs.

    Upstream first: if opportunity quality is the problem, a contactability
    number computed over bad opportunities is not worth acting on. The first
    measured track below its floor is the constraint; everything downstream is
    reported but not blamed.
    """
    unmeasured = tuple(t.track for t in scorecard.tracks if not t.sufficient)
    order = (Track.OPPORTUNITY_QUALITY, Track.CONTACTABILITY,
             Track.CALLABILITY, Track.TRANSACTION_SUCCESS)
    floors = {Track.OPPORTUNITY_QUALITY: quality_floor}

    for track in order:
        result = scorecard.track(track)
        if not result.sufficient or result.rate is None:
            continue
        floor = floors.get(track, rate_floor)
        if result.rate < floor:
            if track is Track.OPPORTUNITY_QUALITY:
                verdict = "recipe produces poor opportunities"
                detail = ("the subjects themselves are the problem; downstream "
                          "rates describe a population not worth pursuing")
            elif track is Track.CONTACTABILITY:
                verdict = "good opportunities, hard-to-resolve owners"
                detail = ("this is a resolution problem, not a recipe problem; "
                          "source strategy is the lever, not recipe weights")
            elif track is Track.CALLABILITY:
                verdict = "contactable but not permitted to contact"
                detail = ("consent, suppression or hours are the constraint; no "
                          "amount of recipe tuning changes this")
            else:
                verdict = "reachable and pursued, but not converting"
                detail = ("the constraint is downstream of targeting — offer, "
                          "script or operator execution")
            return Diagnosis(scorecard.recipe_key, track, verdict, detail, unmeasured)

    if unmeasured and len(unmeasured) == len(scorecard.tracks):
        return Diagnosis(scorecard.recipe_key, None, "not yet measurable",
                         "no track has enough samples to claim a rate", unmeasured)
    return Diagnosis(scorecard.recipe_key, None, "no track below its floor",
                     "every measured track clears its floor", unmeasured)
