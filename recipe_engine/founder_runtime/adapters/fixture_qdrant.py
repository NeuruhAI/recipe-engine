"""Fixture Qdrant — no live retrieval."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.adapters.base import BaseFixtureAdapter, AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.identity import fixture_identity, fixture_dependency
from recipe_engine.founder_runtime.adapters.fixture_mixin import envelope_for_fixture
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, RetryPolicyDeclaration
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult


class FixtureQdrant(BaseFixtureAdapter):
    name = "qdrant"
    adapter_version = "p2a.fixture.qdrant.1"

    def mode(self) -> AdapterMode:
        return AdapterMode.FIXTURE

    def mutation_class(self) -> MutationClass:
        return MutationClass.READ_ONLY

    def retry_policy(self) -> RetryPolicyDeclaration:
        return RetryPolicyDeclaration(max_attempts=1)

    def adapter_identity(self):
        return fixture_identity(self.name, capabilities=("search",), version=self.adapter_version)

    def dependency_identity(self):
        return fixture_dependency("qdrant", endpoint_class="fixture_vector")

    def search(self, query: str, *, limit: int = 5, meta: AdapterRequestMeta | None = None) -> list[dict[str, Any]]:
        return [
            {
                "id": "fixture_vec_1",
                "score": 0.42,
                "payload": {"title": "fixture retrieval candidate", "query_echo": query[:80]},
                "source": "fixture_qdrant",
            }
        ][:limit]

    def search_result(self, query: str, *, limit: int = 5, meta: AdapterRequestMeta | None = None) -> AdapterResult:
        meta = meta or AdapterRequestMeta(correlation_id="fixture", observed_at="1970-01-01T00:00:00.000Z")
        hits = self.search(query, limit=limit, meta=meta)
        return envelope_for_fixture(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            operation="search",
            meta=meta,
            payload={"query": query[:200], "hits": hits},
            source_identity=self.adapter_identity().to_dict(),
            dependency_identity=self.dependency_identity().to_dict(),
            evidence_refs=[h["id"] for h in hits],
            logical_source="fixture://qdrant",
        )
