"""Deterministic context pack selection."""

from __future__ import annotations

from recipe_engine.founder_runtime.contracts.context import (
    ContextPack, ContextSource, ConflictRecord, SCHEMA_CONTEXT_PACK, FreshnessClass,
)
from .registry import fixture_registry
from .precedence import sort_by_precedence
from .freshness import is_usable


def select_packs(
    pack_ids: list[str],
    *,
    token_budget: int = 400,
    include_history: bool = False,
) -> ContextPack:
    reg = fixture_registry()
    candidates = [s for s in reg.values() if s.pack_id in pack_ids]
    excluded: list[str] = []
    stale: list[str] = []
    selected: list[ContextSource] = []
    # exclude superseded and expired
    for s in sort_by_precedence(candidates):
        if s.superseded_by:
            excluded.append(s.source_id)
            continue
        if s.freshness_class == FreshnessClass.EXPIRED:
            stale.append(s.source_id)
            excluded.append(s.source_id)
            continue
        if s.freshness_class == FreshnessClass.STALE and not include_history:
            stale.append(s.source_id)
            excluded.append(s.source_id)
            continue
        if not is_usable(s, allow_stale=include_history):
            excluded.append(s.source_id)
            continue
        selected.append(s)

    # budget
    total = 0
    kept: list[ContextSource] = []
    for s in selected:
        if total + s.token_estimate > token_budget:
            excluded.append(s.source_id)
            continue
        kept.append(s)
        total += s.token_estimate

    conflicts = []
    ids = {s.source_id for s in kept}
    if "src_conflict_a" in ids and "src_conflict_b" in ids:
        conflicts.append(ConflictRecord(
            conflict_id="conf_gov_ownership",
            source_a="src_conflict_a",
            source_b="src_conflict_b",
            description="AEGIS vs Governance-core ownership; operator correction prefers governance-core",
            resolved=False,
        ))
    # Also record conflict if both packs present even if one excluded
    all_ids = {s.source_id for s in candidates}
    if "src_conflict_a" in all_ids and "src_conflict_b" in all_ids and not conflicts:
        conflicts.append(ConflictRecord(
            conflict_id="conf_gov_ownership",
            source_a="src_conflict_a",
            source_b="src_conflict_b",
            description="AEGIS vs Governance-core ownership; operator correction prefers governance-core",
            resolved=False,
        ))

    pack_id = "+".join(sorted(set(pack_ids)))
    return ContextPack(
        schema_version=SCHEMA_CONTEXT_PACK,
        pack_id=pack_id,
        sources=kept,
        token_budget=token_budget,
        conflicts=conflicts,
        selected_source_ids=[s.source_id for s in kept],
        total_tokens=total,
        stale_sources=stale,
        excluded_sources=excluded,
    )
