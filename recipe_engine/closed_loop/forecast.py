"""Forecasts, sealed before the outcome is known.

Calibration is only meaningful against a prediction that could have been wrong.
A "forecast" written after the outcome arrived measures nothing but hindsight,
so this module makes the ordering structural rather than procedural:

* a forecast is `seal()`-ed, which fixes its digest and its `recorded_at`;
* `assert_pre_outcome(observed_at)` refuses any pairing where the seal is not
  strictly earlier than the observation;
* an unsealed forecast has no digest, so nothing downstream can bind to it.

This is the same rule `neuruh_outcome_record.to_017_calibration_entry` enforces
at the calibration boundary. It is enforced here too because by the time the
017 entry is built, the chance to refuse cleanly has already passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.identity import canonical_json, digest


SCHEMA_FORECAST = "neuruh.closed-loop.forecast.v1"


class ForecastError(ValueError):
    """The forecast cannot be sealed or paired as stated."""


@dataclass(frozen=True)
class ForecastedMetric:
    """One predicted quantity, with the horizon it is predicted over."""
    metric_key: str
    predicted_value: Decimal | None
    unit: str
    horizon_days: int

    def __post_init__(self) -> None:
        if not str(self.metric_key).strip():
            raise ForecastError("forecast metric_key must be non-empty")
        if not str(self.unit).strip():
            raise ForecastError("forecast unit must be non-empty")
        if isinstance(self.horizon_days, bool) or not isinstance(self.horizon_days, int):
            raise ForecastError("horizon_days must be an integer")
        if self.horizon_days <= 0:
            raise ForecastError("a forecast horizon must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {"metric_key": self.metric_key,
                "predicted_value": None if self.predicted_value is None else str(self.predicted_value),
                "unit": self.unit, "horizon_days": self.horizon_days}


@dataclass(frozen=True)
class Forecast:
    """A sealed prediction about one subject under one recipe version."""
    forecast_id: str
    subject_id: str
    decision_id: str
    recipe_key: str
    probability: Decimal
    outcome_metric_key: str
    horizon_days: int
    metrics: tuple[ForecastedMetric, ...]
    recorded_at: str
    forecast_version: str
    sealed_digest: str | None = None

    def __post_init__(self) -> None:
        for name in ("forecast_id", "subject_id", "decision_id", "recipe_key",
                     "outcome_metric_key", "recorded_at", "forecast_version"):
            if not str(getattr(self, name)).strip():
                raise ForecastError(f"forecast.{name} must be non-empty")
        if not (Decimal(0) <= self.probability <= Decimal(1)):
            raise ForecastError("probability must be within [0,1]")
        if isinstance(self.horizon_days, bool) or not isinstance(self.horizon_days, int) or self.horizon_days <= 0:
            raise ForecastError("horizon_days must be a positive integer")

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_FORECAST,
            "forecast_id": self.forecast_id,
            "subject_id": self.subject_id,
            "decision_id": self.decision_id,
            "recipe_key": self.recipe_key,
            "probability": str(self.probability),
            "outcome_metric_key": self.outcome_metric_key,
            "horizon_days": self.horizon_days,
            "metrics": [m.to_dict() for m in self.metrics],
            "recorded_at": self.recorded_at,
            "forecast_version": self.forecast_version,
        }

    def seal(self) -> "Forecast":
        """Fix the content. After this, any edit produces a different forecast."""
        return replace(self, sealed_digest=digest(self.body()))

    @property
    def is_sealed(self) -> bool:
        return bool(self.sealed_digest) and self.sealed_digest == digest(self.body())

    @property
    def digest(self) -> str:
        if not self.is_sealed:
            raise ForecastError(
                "an unsealed forecast has no digest — nothing may bind to a "
                "prediction that can still be edited")
        assert self.sealed_digest is not None
        return self.sealed_digest

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "sealed_digest": self.sealed_digest}

    def assert_pre_outcome(self, observed_at: str) -> bool:
        """The whole point. Refuses hindsight dressed as prediction."""
        if not self.is_sealed:
            raise ForecastError("forecast is not sealed; it cannot be scored")
        if not (str(self.recorded_at) < str(observed_at)):
            raise ForecastError(
                f"forecast is not pre-outcome: recorded_at {self.recorded_at} is "
                f"not strictly before observed_at {observed_at}")
        return True
