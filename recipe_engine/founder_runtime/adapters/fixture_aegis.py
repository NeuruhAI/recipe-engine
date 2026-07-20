"""Fixture AEGIS — composition-validity opinion only. Not authority."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.adapters.base import BaseFixtureAdapter, AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, AdapterResultStatus
from recipe_engine.founder_runtime.adapters.identity import fixture_identity, fixture_dependency
from recipe_engine.founder_runtime.adapters.fixture_mixin import envelope_for_fixture
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, RetryPolicyDeclaration
from recipe_engine.founder_runtime.errors import ErrorCode


class FixtureAegis(BaseFixtureAdapter):
    name = "aegis"
    adapter_version = "p2a.fixture.aegis.1"

    def mode(self) -> AdapterMode:
        return AdapterMode.FIXTURE

    def mutation_class(self) -> MutationClass:
        return MutationClass.READ_ONLY

    def retry_policy(self) -> RetryPolicyDeclaration:
        return RetryPolicyDeclaration(max_attempts=1)

    def adapter_identity(self):
        return fixture_identity(self.name, capabilities=("validate_world",), version=self.adapter_version)

    def dependency_identity(self):
        return fixture_dependency("aegis", endpoint_class="fixture_opinion")

    def validate_world(self, world_id: str, meta: AdapterRequestMeta) -> AdapterResult:
        worlds = {
            "fixture_world": {
                "world": "fixture_world",
                "has_compliance_contract": True,
                "compliance_required": True,
                "valid_for_composition": True,
                "verdict": "VALID",
                "hard_conflicts": [],
                "soft_notes": [],
                "advisories": ["fixture opinion only"],
                "offending_nodes": [],
                "summary": "deterministic fixture composition opinion",
            },
            "fixture_contradictory_world": {
                "world": "fixture_contradictory_world",
                "has_compliance_contract": True,
                "compliance_required": True,
                "valid_for_composition": False,
                "verdict": "CONTRADICTORY",
                "hard_conflicts": ["fixture_conflict"],
                "soft_notes": [],
                "advisories": [],
                "offending_nodes": ["fixture_node"],
                "summary": "contradiction is a valid opinion, not adapter failure",
            },
        }
        if not isinstance(world_id, str) or world_id not in worlds:
            return envelope_for_fixture(
                adapter_name=self.name,
                adapter_version=self.adapter_version,
                operation="validate_world",
                meta=meta,
                payload={"world": str(world_id), "available": False},
                source_identity=self.adapter_identity().to_dict(),
                dependency_identity=self.dependency_identity().to_dict(),
                status=AdapterResultStatus.UNSAFE,
                error_code=ErrorCode.UNSAFE_CAPABILITY.value,
                logical_source="fixture://aegis/validate_world",
            )
        payload = worlds[world_id]
        return envelope_for_fixture(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            operation="validate_world",
            meta=meta,
            payload=payload,
            dependency_identity=self.dependency_identity().to_dict(),
            source_identity=self.adapter_identity().to_dict(),
            evidence_refs=[f"aegis-world:{world_id}"],
            logical_source="fixture://aegis/validate_world",
        )
