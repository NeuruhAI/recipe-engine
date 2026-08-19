"""Calibration — how wrong we were, cut by the dimensions that matter.

Deterministic Brier scoring, the same rule the 017 ledger uses, computed per
*cell* so the answer is useful: not "the system is 0.19 Brier" but "this recipe
version is well calibrated on tax-distress subjects in this county and badly
calibrated on heirs-estate ones".

Two invariants:

* **Calibration mutates nothing.** `AUTHORITY = "NONE"` and there is no code
  path from a report to a config. Reports are evidence for a proposal, and a
  proposal goes through governance. That separation is the entire safety
  argument of the learning loop.
* **A cell below its sample floor reports `insufficient_evidence` rather than a
  number.** A Brier score over four samples is noise with a decimal point, and
  once printed it will be quoted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from recipe_engine.closed_loop.identity import canonical_json, digest


SCHEMA_CALIBRATION = "neuruh.closed-loop.calibration.v1"
AUTHORITY = "NONE"

#: Below this a cell is reported but not scored. Matches the BLACK doctrine's
#: NO_FIT_LT_30 claim floor, ported by reference from neuruh-outcome-record's
#: `black_doctrine` rather than re-derived here.
MIN_SAMPLES_FOR_CLAIM = 30

#: Fixed decile buckets, so two reports built a month apart are comparable.
BUCKET_COUNT = 10


class CalibrationDimension(str, Enum):
    """The cuts the loop is required to be able to report on."""
    RECIPE = "recipe"
    SOURCE = "source"
    COUNTY = "county"
    OWNER_CLASS = "owner_class"
    DECISION = "decision"


class CalibrationError(ValueError):
    """The calibration cannot be computed as asked."""


@dataclass(frozen=True)
class ScoredPair:
    """One sealed forecast against one observed actual."""
    subject_id: str
    forecast_digest: str
    outcome_digest: str
    probability: Decimal
    actual: Decimal                    # 1.0 success, 0.5 partial, 0.0 failure
    forecast_recorded_at: str
    observed_at: str
    dimensions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (Decimal(0) <= self.probability <= Decimal(1)):
            raise CalibrationError("probability must be within [0,1]")
        if not (Decimal(0) <= self.actual <= Decimal(1)):
            raise CalibrationError("actual must be within [0,1]")
        if not (str(self.forecast_recorded_at) < str(self.observed_at)):
            raise CalibrationError(
                "pair is not pre-outcome; hindsight cannot be calibrated")
        for name in ("subject_id", "forecast_digest", "outcome_digest"):
            if not str(getattr(self, name)).strip():
                raise CalibrationError(f"pair.{name} must be non-empty")

    @property
    def brier(self) -> Decimal:
        """Deterministic to 10 places — the same rounding the 017 primitive uses."""
        return ((self.probability - self.actual) ** 2).quantize(Decimal("0.0000000001"))

    def bucket(self) -> int:
        return min(int(self.probability * BUCKET_COUNT), BUCKET_COUNT - 1)

    def to_dict(self) -> dict[str, Any]:
        return {"subject_id": self.subject_id, "forecast_digest": self.forecast_digest,
                "outcome_digest": self.outcome_digest, "probability": str(self.probability),
                "actual": str(self.actual), "brier_score": str(self.brier),
                "probability_bucket": self.bucket(),
                "forecast_recorded_at": self.forecast_recorded_at,
                "observed_at": self.observed_at, "dimensions": dict(self.dimensions)}


@dataclass(frozen=True)
class BucketPerformance:
    bucket: int
    count: int
    mean_predicted: Decimal
    observed_rate: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {"bucket": self.bucket, "count": self.count,
                "mean_predicted": str(self.mean_predicted),
                "observed_rate": str(self.observed_rate),
                "gap": str(self.observed_rate - self.mean_predicted)}


@dataclass(frozen=True)
class CalibrationCell:
    """One dimension value, scored — or explicitly not scored."""
    dimension: CalibrationDimension
    key: str
    count: int
    mean_brier: Decimal | None
    mean_predicted: Decimal | None
    observed_rate: Decimal | None
    calibration_gap: Decimal | None
    buckets: tuple[BucketPerformance, ...]
    sufficient: bool
    insufficiency_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        def s(v: Decimal | None) -> str | None:
            return None if v is None else str(v)
        return {"dimension": self.dimension.value, "key": self.key, "count": self.count,
                "mean_brier": s(self.mean_brier), "mean_predicted": s(self.mean_predicted),
                "observed_rate": s(self.observed_rate),
                "calibration_gap": s(self.calibration_gap),
                "buckets": [b.to_dict() for b in self.buckets],
                "sufficient": self.sufficient,
                "insufficiency_reason": self.insufficiency_reason}


@dataclass(frozen=True)
class CalibrationReport:
    """Evidence. Never a change, never an instruction."""
    report_id: str
    window_start: str
    window_end: str
    pair_count: int
    cells: tuple[CalibrationCell, ...]
    pair_digests: tuple[str, ...]
    computed_at: str
    authority: str = AUTHORITY
    mutates_production: bool = False

    def __post_init__(self) -> None:
        if self.authority != AUTHORITY:
            raise CalibrationError("a calibration report grants no authority")
        if self.mutates_production:
            raise CalibrationError(
                "calibration evidence never mutates production; that is what the "
                "learning proposal and the promotion gate are for")

    def cell(self, dimension: CalibrationDimension, key: str) -> CalibrationCell | None:
        for c in self.cells:
            if c.dimension is dimension and c.key == key:
                return c
        return None

    def sufficient_cells(self) -> tuple[CalibrationCell, ...]:
        return tuple(c for c in self.cells if c.sufficient)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_CALIBRATION,
            "report_id": self.report_id,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "pair_count": self.pair_count,
            "cells": [c.to_dict() for c in self.cells],
            "pair_digests": list(self.pair_digests),
            "computed_at": self.computed_at,
            "authority": self.authority,
            "mutates_production": self.mutates_production,
        }

    @property
    def digest(self) -> str:
        return digest(self.to_dict())


def _mean(values: Sequence[Decimal]) -> Decimal:
    return (sum(values) / Decimal(len(values))).quantize(Decimal("0.0000000001"))


def _build_cell(dimension: CalibrationDimension, key: str,
                pairs: Sequence[ScoredPair], min_samples: int) -> CalibrationCell:
    count = len(pairs)
    if count < min_samples:
        return CalibrationCell(
            dimension=dimension, key=key, count=count, mean_brier=None,
            mean_predicted=None, observed_rate=None, calibration_gap=None,
            buckets=(), sufficient=False,
            insufficiency_reason=(f"{count} scored pairs is below the {min_samples} "
                                  "sample floor; no calibration claim is made"))
    predicted = [p.probability for p in pairs]
    actuals = [p.actual for p in pairs]
    mean_predicted = _mean(predicted)
    observed_rate = _mean(actuals)
    buckets: list[BucketPerformance] = []
    for index in range(BUCKET_COUNT):
        members = [p for p in pairs if p.bucket() == index]
        if not members:
            continue
        buckets.append(BucketPerformance(
            bucket=index, count=len(members),
            mean_predicted=_mean([m.probability for m in members]),
            observed_rate=_mean([m.actual for m in members])))
    return CalibrationCell(
        dimension=dimension, key=key, count=count,
        mean_brier=_mean([p.brier for p in pairs]),
        mean_predicted=mean_predicted, observed_rate=observed_rate,
        calibration_gap=(observed_rate - mean_predicted).quantize(Decimal("0.0000000001")),
        buckets=tuple(buckets), sufficient=True, insufficiency_reason=None)


def calibrate(
    pairs: Sequence[ScoredPair],
    *,
    report_id: str,
    window_start: str,
    window_end: str,
    computed_at: str,
    dimensions: Sequence[CalibrationDimension] = tuple(CalibrationDimension),
    min_samples: int = MIN_SAMPLES_FOR_CLAIM,
) -> CalibrationReport:
    """Score every requested cut. Cells below the floor are reported unscored."""
    if not pairs:
        raise CalibrationError("no scored pairs; there is nothing to calibrate")
    cells: list[CalibrationCell] = []
    for dimension in dimensions:
        grouped: dict[str, list[ScoredPair]] = {}
        for pair in pairs:
            key = pair.dimensions.get(dimension.value)
            if key is None:
                continue
            grouped.setdefault(str(key), []).append(pair)
        for key in sorted(grouped):
            cells.append(_build_cell(dimension, key, grouped[key], min_samples))
    return CalibrationReport(
        report_id=report_id, window_start=window_start, window_end=window_end,
        pair_count=len(pairs), cells=tuple(cells),
        pair_digests=tuple(sorted(digest(p.to_dict()) for p in pairs)),
        computed_at=computed_at)
