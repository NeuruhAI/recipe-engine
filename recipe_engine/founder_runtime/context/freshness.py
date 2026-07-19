"""Freshness classification helpers."""

from __future__ import annotations

from recipe_engine.founder_runtime.contracts.context import ContextSource, FreshnessClass


def is_usable(source: ContextSource, *, allow_stale: bool = False) -> bool:
    if source.superseded_by:
        return False
    if source.freshness_class == FreshnessClass.EXPIRED:
        return False
    if source.freshness_class == FreshnessClass.STALE and not allow_stale:
        return False
    return True
