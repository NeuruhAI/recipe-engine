"""Compile selected context into mission-facing pack."""

from __future__ import annotations

from .selector import select_packs


def compile_context(pack_ids: list[str], *, token_budget: int = 400) -> dict:
    pack = select_packs(pack_ids, token_budget=token_budget)
    return pack.to_dict()
