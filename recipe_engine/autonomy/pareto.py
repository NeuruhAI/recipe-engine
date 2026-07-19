"""Pareto frontier and dominance. Pure; no I/O."""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Sequence

from .config import DIMENSIONS, EngineConfig
from .models import ScenarioScore, ScoreDirection


def _oriented(score: ScenarioScore, dim: str) -> Decimal | None:
    ds = score.dimensions.get(dim)
    if ds is None or ds.value is None:
        return None
    if ds.direction == ScoreDirection.HIGHER_BETTER:
        return ds.value
    return Decimal("1") - ds.value


def dominates(a: ScenarioScore, b: ScenarioScore, dims: Sequence[str] | None = None) -> bool:
    """True if a Pareto-dominates b (a >= b all dims, > at least one). Missing treated as incomparable."""
    use = list(dims) if dims is not None else list(DIMENSIONS)
    any_better = False
    for dim in use:
        va = _oriented(a, dim)
        vb = _oriented(b, dim)
        if va is None or vb is None:
            # incomplete dimensions: cannot claim strict dominance on that dim
            if va is None and vb is None:
                continue
            return False
        if va < vb:
            return False
        if va > vb:
            any_better = True
    return any_better


def pareto_frontier(
    scores: Iterable[ScenarioScore],
    *,
    dims: Sequence[str] | None = None,
) -> list[str]:
    """Return strategy_ids on the non-dominated frontier, sorted."""
    items = list(scores)
    frontier: list[str] = []
    for s in items:
        if any(dominates(other, s, dims) for other in items if other.strategy_id != s.strategy_id):
            continue
        frontier.append(s.strategy_id)
    return sorted(frontier)


def domination_map(
    scores: Sequence[ScenarioScore],
    *,
    dims: Sequence[str] | None = None,
) -> dict[str, list[str]]:
    """strategy_id -> list of dominator strategy_ids."""
    out: dict[str, list[str]] = {}
    for s in scores:
        doms = sorted(
            o.strategy_id
            for o in scores
            if o.strategy_id != s.strategy_id and dominates(o, s, dims)
        )
        if doms:
            out[s.strategy_id] = doms
    return out


def composite_tie_key(score: ScenarioScore, config: EngineConfig) -> tuple:
    """Stable ranking key: composite desc, confidence desc, strategy_id asc."""
    conf = score.dimensions.get("confidence")
    conf_v = conf.value if conf and conf.value is not None else Decimal("-1")
    comp = score.composite if score.composite is not None else Decimal("-1")
    return (-comp, -conf_v, score.strategy_id)
