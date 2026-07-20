"""Adapter modes and fixture base — no silent live fallback."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

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


class WriteClass(str, Enum):
    """Canonical P2-A.1 classification for writes observed at an adapter boundary."""

    MISSION_TRUTH = "mission_truth"
    OPERATIONAL_METADATA = "operational_metadata"
    EXTERNAL_MUTATION = "external_mutation"
    PROHIBITED = "prohibited"


@dataclass(frozen=True)
class OperationalWriteRef:
    """Bounded upstream write recorded as evidence, never as a trust root."""

    kind: str
    location_class: str
    bounded: bool = True
    recorded_as: str = "evidence_reference"

    def __post_init__(self) -> None:
        if not self.kind or not self.location_class:
            raise ValueError("operational write references require kind and location_class")
        if self.bounded is not True:
            raise ValueError("operational write references must be bounded")
        if self.recorded_as != "evidence_reference":
            raise ValueError("operational writes may only be recorded as evidence_reference")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "location_class": self.location_class,
            "bounded": True,
            "recorded_as": "evidence_reference",
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OperationalWriteRef":
        required = {"kind", "location_class", "bounded", "recorded_as"}
        if set(value) != required:
            raise ValueError("operational write reference has an invalid shape")
        return cls(
            kind=str(value["kind"]),
            location_class=str(value["location_class"]),
            bounded=value["bounded"] is True,
            recorded_as=str(value["recorded_as"]),
        )


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
