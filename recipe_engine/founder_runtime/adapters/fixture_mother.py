"""Fixture Mother — health/provenance fixtures. Does not plan."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.contracts.health import DependencyHealth, HealthSnapshot
from recipe_engine.founder_runtime.adapters.base import BaseFixtureAdapter, AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.identity import fixture_identity, fixture_dependency
from recipe_engine.founder_runtime.adapters.fixture_mixin import envelope_for_fixture
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, RetryPolicyDeclaration
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult


class FixtureMother(BaseFixtureAdapter):
    name = "mother"
    adapter_version = "p2a.fixture.mother.1"

    def mode(self) -> AdapterMode:
        return AdapterMode.FIXTURE

    def mutation_class(self) -> MutationClass:
        return MutationClass.READ_ONLY

    def retry_policy(self) -> RetryPolicyDeclaration:
        return RetryPolicyDeclaration(max_attempts=1)

    def adapter_identity(self):
        return fixture_identity(self.name, capabilities=("health_snapshot",), version=self.adapter_version)

    def dependency_identity(self):
        return fixture_dependency("mother", endpoint_class="fixture_health")

    def health_snapshot(self, *, observed_at: str, meta: AdapterRequestMeta | None = None) -> HealthSnapshot:
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

    def health_result(self, *, observed_at: str, meta: AdapterRequestMeta) -> AdapterResult:
        snap = self.health_snapshot(observed_at=observed_at, meta=meta)
        return envelope_for_fixture(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            operation="health_snapshot",
            meta=meta,
            payload=snap.to_dict(),
            source_identity=self.adapter_identity().to_dict(),
            dependency_identity=self.dependency_identity().to_dict(),
            evidence_refs=[snap.snapshot_id],
            logical_source="fixture://mother/health",
        )
