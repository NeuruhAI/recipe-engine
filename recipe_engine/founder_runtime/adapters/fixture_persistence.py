"""Fixture mission persistence adapter identity — wraps local SQLite only."""

from __future__ import annotations

from recipe_engine.founder_runtime.adapters.base import BaseFixtureAdapter, AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.identity import fixture_identity, fixture_dependency
from recipe_engine.founder_runtime.adapters.protocols import RetryPolicyDeclaration


class FixturePersistence(BaseFixtureAdapter):
    name = "persistence"
    adapter_version = "p2a.fixture.persistence.1"

    def mode(self) -> AdapterMode:
        return AdapterMode.FIXTURE

    def mutation_class(self) -> MutationClass:
        return MutationClass.LOCAL_REVERSIBLE  # local sqlite projection only

    def retry_policy(self) -> RetryPolicyDeclaration:
        return RetryPolicyDeclaration(max_attempts=1)

    def adapter_identity(self):
        return fixture_identity(
            self.name,
            capabilities=("local_sqlite",),
            mutation=False,
            version=self.adapter_version,
        )

    def dependency_identity(self):
        return fixture_dependency("sqlite", endpoint_class="local_file")

    def backend_class(self) -> str:
        return "sqlite_local_fixture"
