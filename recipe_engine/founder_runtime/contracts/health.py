"""Health snapshot contracts from fixture Mother."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class DependencyHealth:
    dependency_id: str
    name: str
    status: str  # healthy | degraded | down | unknown
    provenance: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dependency_id": self.dependency_id,
            "name": self.name,
            "status": self.status,
            "provenance": self.provenance,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class HealthSnapshot:
    schema_version: str
    snapshot_id: str
    observed_at: str
    overall: str
    dependencies: tuple[DependencyHealth, ...]
    blocked: tuple[str, ...]
    source: str = "fixture_mother"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "observed_at": self.observed_at,
            "overall": self.overall,
            "dependencies": [d.to_dict() for d in self.dependencies],
            "blocked": list(self.blocked),
            "source": self.source,
        }
