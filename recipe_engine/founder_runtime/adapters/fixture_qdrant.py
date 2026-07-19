"""Fixture Qdrant — no live retrieval."""

from __future__ import annotations

from typing import Any

from .base import BaseFixtureAdapter


class FixtureQdrant(BaseFixtureAdapter):
    name = "qdrant"

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        # Stable empty/fixture candidates — never live
        return [
            {
                "id": "fixture_vec_1",
                "score": 0.42,
                "payload": {"title": "fixture retrieval candidate", "query_echo": query[:80]},
                "source": "fixture_qdrant",
            }
        ][:limit]
