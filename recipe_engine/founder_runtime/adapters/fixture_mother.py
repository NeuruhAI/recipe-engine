"""Fixture Mother — health/provenance fixtures. Does not plan."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.contracts.health import DependencyHealth, HealthSnapshot
from .base import BaseFixtureAdapter


class FixtureMother(BaseFixtureAdapter):
    name = "mother"

    def health_snapshot(self, *, observed_at: str) -> HealthSnapshot:
        deps = (
            DependencyHealth("dep_axon", "AXON", "healthy", "fixture", "fixture identity only"),
            DependencyHealth("dep_iar", "IAR", "healthy", "fixture", "fixture identity only"),
            DependencyHealth("dep_gov", "Governance", "healthy", "fixture", "fixture identity only"),
            DependencyHealth("dep_vault", "Vault", "degraded", "fixture", "not queried live"),
            DependencyHealth("dep_qdrant", "Qdrant", "unknown", "fixture", "not queried live"),
            DependencyHealth("dep_cockpit", "Cockpit", "healthy", "fixture", "read-only fixture"),
        )
        blocked = tuple(d.name for d in deps if d.status in ("down",))
        overall = "degraded" if any(d.status == "degraded" for d in deps) else "healthy"
        return HealthSnapshot(
            schema_version="fr.health.v1",
            snapshot_id=f"health_{observed_at}",
            observed_at=observed_at,
            overall=overall,
            dependencies=deps,
            blocked=blocked,
            source="fixture_mother",
        )
