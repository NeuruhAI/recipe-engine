"""Adapter modes and fixture base — no silent live fallback."""

from __future__ import annotations

from enum import Enum
from typing import Any

from recipe_engine.founder_runtime.errors import AdapterModeInvalid, LiveFallbackForbidden


class AdapterMode(str, Enum):
    """Canonical adapter modes (P2-A)."""

    FIXTURE = "fixture"
    LIVE = "live"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


ALL_ADAPTER_MODES: frozenset[str] = frozenset(m.value for m in AdapterMode)

# P1.2 fixture adapters still require explicit fixture mode only
ALLOWED_FIXTURE_MODES = {AdapterMode.FIXTURE, "fixture"}
# Deprecated alias
ALLOWED_P1_MODES = ALLOWED_FIXTURE_MODES


def normalize_mode(mode: str | AdapterMode) -> AdapterMode:
    if isinstance(mode, AdapterMode):
        return mode
    m = str(mode)
    try:
        return AdapterMode(m)
    except ValueError as e:
        raise AdapterModeInvalid(
            f"unknown adapter mode {m!r}",
            {"mode": m, "allowed": sorted(ALL_ADAPTER_MODES)},
        ) from e


def require_fixture_mode(mode: str | AdapterMode, adapter_name: str) -> None:
    """Fixture adapters accept only mode='fixture'. No live/disabled/unavailable."""
    m = normalize_mode(mode)
    if m != AdapterMode.FIXTURE:
        raise AdapterModeInvalid(
            f"adapter {adapter_name} rejects mode={m.value!r}; fixture adapters require mode='fixture'",
            {"adapter": adapter_name, "mode": m.value},
        )


def reject_fallback(fallback_used: bool, *, context: str = "") -> None:
    """P2-A: fallback_used must always be false."""
    if fallback_used:
        raise LiveFallbackForbidden(
            "live-to-fixture fallback is forbidden",
            {"context": context, "fallback_used": True},
        )


class MutationClass(str, Enum):
    NONE = "none"
    READ_ONLY = "read_only"
    LOCAL_REVERSIBLE = "local_reversible"
    MUTATING = "mutating"
    DESTRUCTIVE = "destructive"


class BaseFixtureAdapter:
    """Backward-compatible fixture adapter base (P1.2 semantics preserved)."""

    name: str = "base"
    adapter_version: str = "p2a.fixture.1"

    def __init__(self, mode: str = "fixture"):
        require_fixture_mode(mode, self.name)
        self.adapter_mode = AdapterMode.FIXTURE
        # Keep string for P1.2 callers that read .mode as value
        self._mode_value = AdapterMode.FIXTURE.value

    @property
    def mode_value(self) -> str:
        return self._mode_value

    def mode(self) -> AdapterMode:
        return AdapterMode.FIXTURE

    def mutation_class(self) -> MutationClass:
        return MutationClass.READ_ONLY

    def identity_dict(self) -> dict[str, Any]:
        return {
            "canonical_name": self.name,
            "owner_repository": "NeuruhAI/recipe-engine",
            "implementation_version": self.adapter_version,
            "source_sha": "fixture",
            "mode": self._mode_value,
            "mutation_capability": False,
            "evidence_class": "fixture",
        }
