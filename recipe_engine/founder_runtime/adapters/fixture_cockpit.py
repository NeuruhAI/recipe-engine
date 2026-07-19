"""Fixture Cockpit — no UI mutation."""

from __future__ import annotations

from typing import Any

from .base import BaseFixtureAdapter


class FixtureCockpit(BaseFixtureAdapter):
    name = "cockpit"

    def status(self) -> dict[str, Any]:
        return {
            "surface": "cockpit",
            "mode": "fixture",
            "integration": "not_connected",
            "note": "P1 has no Cockpit integration",
        }
