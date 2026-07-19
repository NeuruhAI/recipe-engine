"""Context precedence rules."""

from __future__ import annotations

from recipe_engine.founder_runtime.contracts.context import PRECEDENCE_ORDER, ContextSource


def precedence_rank(source: ContextSource) -> int:
    try:
        return PRECEDENCE_ORDER.index(source.precedence_class)
    except ValueError:
        return len(PRECEDENCE_ORDER) + 10


def sort_by_precedence(sources: list[ContextSource]) -> list[ContextSource]:
    return sorted(sources, key=lambda s: (precedence_rank(s), s.source_id))


def governance_binding_blocks_override(sources: list[ContextSource], action_prohibited: bool) -> bool:
    """Current founder instruction cannot override prohibited Governance."""
    if not action_prohibited:
        return False
    has_gov = any(s.precedence_class == "governance_decision" for s in sources)
    has_instruction = any(s.precedence_class == "explicit_founder_instruction" for s in sources)
    return has_gov and has_instruction
