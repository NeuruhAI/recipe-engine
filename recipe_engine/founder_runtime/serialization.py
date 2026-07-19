"""Canonical serialization re-export + mission helpers."""

from __future__ import annotations

from typing import Any

from recipe_engine.autonomy.serialization import canonical_json, loads_canonical


def to_canonical(obj: Any) -> str:
    return canonical_json(obj)


def from_canonical(text: str) -> Any:
    return loads_canonical(text)


__all__ = ["to_canonical", "from_canonical", "canonical_json", "loads_canonical"]
