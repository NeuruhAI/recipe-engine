"""Near-duplicate strategy fingerprinting. Pure; no I/O."""

from __future__ import annotations

from decimal import Decimal

from .hashing import content_hash
from .models import CandidateStrategy


def strategy_fingerprint(strategy: CandidateStrategy) -> str:
    """Fingerprint for near-duplicate detection."""
    payload = {
        "family": strategy.strategy_family,
        "thesis_norm": " ".join(strategy.thesis.lower().split()),
        "next_safe_action": strategy.next_safe_action,
        "reversibility": strategy.reversibility,
        "capital_intensity": strategy.capital_intensity,
        "time_profile": strategy.time_profile,
        "action_classes": sorted(strategy.action_classes),
        "dependencies": sorted(strategy.dependencies),
    }
    return content_hash(payload)


def diversity_key(strategy: CandidateStrategy) -> dict[str, str]:
    return {
        "strategy_family": strategy.strategy_family,
        "risk_profile": strategy.capital_intensity + ":" + strategy.reversibility,
        "time_horizon": strategy.time_profile,
        "capital_intensity": strategy.capital_intensity,
        "reversibility": strategy.reversibility,
        "dependency_profile": ",".join(sorted(strategy.dependencies)) or "none",
    }


def jaccard(a: set[str], b: set[str]) -> Decimal:
    if not a and not b:
        return Decimal("1")
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return Decimal("0")
    return Decimal(inter) / Decimal(union)


def similarity(a: CandidateStrategy, b: CandidateStrategy) -> Decimal:
    """Deterministic similarity in [0,1]."""
    tokens_a = set(" ".join(a.thesis.lower().split()).split())
    tokens_b = set(" ".join(b.thesis.lower().split()).split())
    text_sim = jaccard(tokens_a, tokens_b)
    fam = Decimal("1") if a.strategy_family == b.strategy_family else Decimal("0")
    next_a = a.next_safe_action.strip().lower()
    next_b = b.next_safe_action.strip().lower()
    next_sim = Decimal("1") if next_a == next_b else Decimal("0")
    dep_sim = jaccard(set(a.dependencies), set(b.dependencies))
    # weighted blend
    return (
        Decimal("0.35") * text_sim
        + Decimal("0.25") * fam
        + Decimal("0.25") * next_sim
        + Decimal("0.15") * dep_sim
    )


def collapse_duplicates(
    strategies: list[CandidateStrategy],
    threshold: Decimal,
) -> tuple[list[CandidateStrategy], dict[str, str]]:
    """
    Keep first by strategy_id order among near-duplicates.
    Returns kept list and map of duplicate_id -> kept_parent_id.
    """
    ordered = sorted(strategies, key=lambda s: s.strategy_id)
    kept: list[CandidateStrategy] = []
    parent: dict[str, str] = {}
    for s in ordered:
        dup_of = None
        for k in kept:
            if similarity(s, k) >= threshold:
                dup_of = k.strategy_id
                break
            if strategy_fingerprint(s) == strategy_fingerprint(k):
                dup_of = k.strategy_id
                break
        if dup_of is not None:
            parent[s.strategy_id] = dup_of
        else:
            kept.append(s)
    return kept, parent
