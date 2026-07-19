"""Versioned engine configuration. Pure; no env/I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .errors import ConfigError
from .hashing import content_hash

SCHEMA_VERSION = "autonomy.config.v1"
ENGINE_VERSION = "recipe-engine/autonomy@0.1.0"

# Dimension keys used by scoring/collapse
DIMENSIONS: tuple[str, ...] = (
    "evidence_strength",
    "evidence_completeness",
    "feasibility",
    "economic_attractiveness",
    "time_efficiency",
    "strategic_fit",
    "reversibility",
    "operational_complexity",
    "uncertainty",
    "downside_risk",
    "regulatory_policy_risk",
    "dependency_risk",
    "confidence",
)

# higher_better vs lower_better
DIMENSION_DIRECTION: dict[str, str] = {
    "evidence_strength": "higher_better",
    "evidence_completeness": "higher_better",
    "feasibility": "higher_better",
    "economic_attractiveness": "higher_better",
    "time_efficiency": "higher_better",
    "strategic_fit": "higher_better",
    "reversibility": "higher_better",
    "operational_complexity": "lower_better",
    "uncertainty": "lower_better",
    "downside_risk": "lower_better",
    "regulatory_policy_risk": "lower_better",
    "dependency_risk": "lower_better",
    "confidence": "higher_better",
}

DEFAULT_WEIGHTS: dict[str, Decimal] = {
    "evidence_strength": Decimal("1.0"),
    "evidence_completeness": Decimal("1.2"),
    "feasibility": Decimal("1.1"),
    "economic_attractiveness": Decimal("1.0"),
    "time_efficiency": Decimal("0.8"),
    "strategic_fit": Decimal("0.9"),
    "reversibility": Decimal("0.7"),
    "operational_complexity": Decimal("0.6"),
    "uncertainty": Decimal("1.0"),
    "downside_risk": Decimal("1.3"),
    "regulatory_policy_risk": Decimal("1.0"),
    "dependency_risk": Decimal("0.8"),
    "confidence": Decimal("1.0"),
}

MAX_CANDIDATES = 64
MAX_CLAIMS = 256
MAX_EVIDENCE = 512
MAX_SELECTED = 4
MAX_EXPLANATION_CHARS = 4000
DUPLICATE_THRESHOLD = Decimal("0.92")
SCORE_QUANTUM = Decimal("0.0001")


def _as_decimal(value: Any, name: str) -> Decimal:
    try:
        if isinstance(value, Decimal):
            d = value
        elif isinstance(value, (int, str)):
            d = Decimal(str(value))
        elif isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise ConfigError(f"{name} is NaN/Infinity")
            d = Decimal(str(value))
        else:
            raise ConfigError(f"{name} must be a number")
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError(f"{name} is not a valid decimal: {value!r}") from exc
    if d != d:  # pragma: no cover
        raise ConfigError(f"{name} is NaN")
    if d.is_infinite():
        raise ConfigError(f"{name} is infinite")
    return d


@dataclass(frozen=True)
class EngineConfig:
    schema_version: str = SCHEMA_VERSION
    engine_version: str = ENGINE_VERSION
    weights: Mapping[str, Decimal] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    max_selected: int = MAX_SELECTED
    max_candidates: int = MAX_CANDIDATES
    max_claims: int = MAX_CLAIMS
    max_evidence: int = MAX_EVIDENCE
    max_explanation_chars: int = MAX_EXPLANATION_CHARS
    duplicate_threshold: Decimal = DUPLICATE_THRESHOLD
    critical_evidence_completeness_min: Decimal = Decimal("0.35")
    critical_conflict_blocks: bool = True
    score_quantum: Decimal = SCORE_QUANTUM
    config_version: str = "default-v1"
    # injected clock: ISO-8601 string or None → fixed epoch for determinism in tests
    fixed_timestamp: str = "1970-01-01T00:00:00Z"
    # injected id seed (optional extra salt for packet ids)
    id_salt: str = "autonomy-p0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "engine_version": self.engine_version,
            "weights": {k: str(self.weights[k]) for k in sorted(self.weights.keys())},
            "max_selected": self.max_selected,
            "max_candidates": self.max_candidates,
            "max_claims": self.max_claims,
            "max_evidence": self.max_evidence,
            "max_explanation_chars": self.max_explanation_chars,
            "duplicate_threshold": str(self.duplicate_threshold),
            "critical_evidence_completeness_min": str(self.critical_evidence_completeness_min),
            "critical_conflict_blocks": self.critical_conflict_blocks,
            "score_quantum": str(self.score_quantum),
            "config_version": self.config_version,
            "fixed_timestamp": self.fixed_timestamp,
            "id_salt": self.id_salt,
        }

    def config_hash(self) -> str:
        return content_hash(self.to_dict())


def validate_config(config: EngineConfig) -> EngineConfig:
    if config.schema_version != SCHEMA_VERSION:
        raise ConfigError(
            f"unsupported config schema_version: {config.schema_version}",
            {"expected": SCHEMA_VERSION},
        )
    if config.max_selected < 0 or config.max_selected > MAX_SELECTED:
        raise ConfigError(f"max_selected must be 0..{MAX_SELECTED}")
    if config.max_candidates < 1 or config.max_candidates > 1000:
        raise ConfigError("max_candidates out of bounds")
    weights = dict(config.weights)
    for dim in DIMENSIONS:
        if dim not in weights:
            raise ConfigError(f"missing weight for dimension: {dim}")
        w = _as_decimal(weights[dim], f"weight[{dim}]")
        if w < 0:
            raise ConfigError(f"negative weight rejected: {dim}")
        weights[dim] = w
    thr = _as_decimal(config.duplicate_threshold, "duplicate_threshold")
    if thr < 0 or thr > 1:
        raise ConfigError("duplicate_threshold must be in [0,1]")
    crit = _as_decimal(
        config.critical_evidence_completeness_min, "critical_evidence_completeness_min"
    )
    quantum = _as_decimal(config.score_quantum, "score_quantum")
    if quantum <= 0:
        raise ConfigError("score_quantum must be > 0")
    # rebuild frozen config with normalized decimals
    return EngineConfig(
        schema_version=config.schema_version,
        engine_version=config.engine_version,
        weights=weights,
        max_selected=config.max_selected,
        max_candidates=config.max_candidates,
        max_claims=config.max_claims,
        max_evidence=config.max_evidence,
        max_explanation_chars=config.max_explanation_chars,
        duplicate_threshold=thr,
        critical_evidence_completeness_min=crit,
        critical_conflict_blocks=config.critical_conflict_blocks,
        score_quantum=quantum,
        config_version=config.config_version,
        fixed_timestamp=config.fixed_timestamp,
        id_salt=config.id_salt,
    )


def default_config(**overrides: Any) -> EngineConfig:
    base = EngineConfig()
    if not overrides:
        return validate_config(base)
    data = base.to_dict()
    data.update(overrides)
    if "weights" in overrides and isinstance(overrides["weights"], Mapping):
        data["weights"] = {
            k: _as_decimal(v, f"weight[{k}]") for k, v in overrides["weights"].items()
        }
    else:
        data["weights"] = dict(DEFAULT_WEIGHTS)
    return validate_config(
        EngineConfig(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            engine_version=data.get("engine_version", ENGINE_VERSION),
            weights=data["weights"],
            max_selected=int(data.get("max_selected", MAX_SELECTED)),
            max_candidates=int(data.get("max_candidates", MAX_CANDIDATES)),
            max_claims=int(data.get("max_claims", MAX_CLAIMS)),
            max_evidence=int(data.get("max_evidence", MAX_EVIDENCE)),
            max_explanation_chars=int(data.get("max_explanation_chars", MAX_EXPLANATION_CHARS)),
            duplicate_threshold=_as_decimal(
                data.get("duplicate_threshold", DUPLICATE_THRESHOLD), "duplicate_threshold"
            ),
            critical_evidence_completeness_min=_as_decimal(
                data.get(
                    "critical_evidence_completeness_min",
                    Decimal("0.35"),
                ),
                "critical_evidence_completeness_min",
            ),
            critical_conflict_blocks=bool(data.get("critical_conflict_blocks", True)),
            score_quantum=_as_decimal(data.get("score_quantum", SCORE_QUANTUM), "score_quantum"),
            config_version=str(data.get("config_version", "default-v1")),
            fixed_timestamp=str(data.get("fixed_timestamp", "1970-01-01T00:00:00Z")),
            id_salt=str(data.get("id_salt", "autonomy-p0")),
        )
    )
