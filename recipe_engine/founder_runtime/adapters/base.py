"""Adapter base — no silent live fallback."""

from __future__ import annotations

from enum import Enum
from typing import Any

from recipe_engine.founder_runtime.errors import AdapterModeInvalid


class AdapterMode(str, Enum):
    FIXTURE = "fixture"
    # Live modes intentionally not implemented in P1


ALLOWED_P1_MODES = {AdapterMode.FIXTURE, "fixture"}


def require_fixture_mode(mode: str | AdapterMode, adapter_name: str) -> None:
    m = mode.value if isinstance(mode, AdapterMode) else str(mode)
    if m != AdapterMode.FIXTURE.value:
        raise AdapterModeInvalid(
            f"adapter {adapter_name} rejects mode={m!r}; P1 allows only mode='fixture'",
            {"adapter": adapter_name, "mode": m},
        )


class BaseFixtureAdapter:
    name: str = "base"

    def __init__(self, mode: str = "fixture"):
        require_fixture_mode(mode, self.name)
        self.mode = "fixture"
