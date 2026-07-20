"""Fixture Cockpit — no UI mutation."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.adapters.base import BaseFixtureAdapter, AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.identity import fixture_identity, fixture_dependency
from recipe_engine.founder_runtime.adapters.fixture_mixin import envelope_for_fixture
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, RetryPolicyDeclaration
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult
from typing import Any


class FixtureCockpit(BaseFixtureAdapter):
    name = "cockpit"
    adapter_version = "p2a.fixture.cockpit.1"

    def mode(self) -> AdapterMode:
        return AdapterMode.FIXTURE

    def mutation_class(self) -> MutationClass:
        return MutationClass.NONE

    def retry_policy(self) -> RetryPolicyDeclaration:
        return RetryPolicyDeclaration(max_attempts=1)

    def adapter_identity(self):
        return fixture_identity(self.name, capabilities=("status",), version=self.adapter_version)

    def dependency_identity(self):
        return fixture_dependency("cockpit", endpoint_class="fixture_status")


    def status(self) -> dict[str, Any]:
        return {
            "surface": "cockpit",
            "mode": "fixture",
            "integration": "not_connected",
            "note": "P1 has no Cockpit integration",
        }

    def status_result(self, meta: AdapterRequestMeta | None = None) -> AdapterResult:
        meta = meta or AdapterRequestMeta(correlation_id="fixture_cockpit", observed_at="1970-01-01T00:00:00.000Z")
        payload = self.status()
        return envelope_for_fixture(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            operation="status",
            meta=meta,
            payload=payload,
            source_identity=self.adapter_identity().to_dict(),
            dependency_identity=self.dependency_identity().to_dict(),
            logical_source="fixture://cockpit",
        )
