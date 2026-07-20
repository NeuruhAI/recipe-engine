"""Fixture Vault — opaque document IDs only. No file path freedom. No live retrieval."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.adapters.base import BaseFixtureAdapter, AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.identity import (
    fixture_identity, fixture_dependency, vault_provenance, EvidenceClass,
)
from recipe_engine.founder_runtime.adapters.fixture_mixin import envelope_for_fixture
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, RetryPolicyDeclaration
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult


class FixtureVault(BaseFixtureAdapter):
    name = "vault"
    adapter_version = "p2a.fixture.vault.1"

    def mode(self) -> AdapterMode:
        return AdapterMode.FIXTURE

    def mutation_class(self) -> MutationClass:
        return MutationClass.READ_ONLY

    def retry_policy(self) -> RetryPolicyDeclaration:
        return RetryPolicyDeclaration(max_attempts=1)

    def adapter_identity(self):
        return fixture_identity(self.name, capabilities=("get_document",), version=self.adapter_version)

    def dependency_identity(self):
        return fixture_dependency("vault", endpoint_class="fixture_document")

    def provenance(self, *, observed_at: str):
        # Runtime is not independent source of truth; fixture marks N/A divergence
        return vault_provenance(
            source_sha="fixture",
            runtime_sha="fixture",
            retrieval_timestamp=observed_at,
            evidence_class=EvidenceClass.FIXTURE,
        )

    def get_document(self, doc_id: str, meta: AdapterRequestMeta | None = None) -> dict[str, Any]:
        allowed = {
            "doc_founder_core": {
                "doc_id": "doc_founder_core",
                "title": "Founder core doctrine (fixture)",
                "content_hash": "hash_founder_core",
                "pointer": "fixture://vault/doc_founder_core",
            },
            "doc_architecture": {
                "doc_id": "doc_architecture",
                "title": "Architecture layer map (fixture)",
                "content_hash": "hash_architecture",
                "pointer": "fixture://vault/doc_architecture",
            },
        }
        if doc_id not in allowed:
            return {"doc_id": doc_id, "error": "not_found", "pointer": f"fixture://vault/{doc_id}"}
        return allowed[doc_id]

    def get_document_result(self, doc_id: str, meta: AdapterRequestMeta) -> AdapterResult:
        payload = self.get_document(doc_id, meta)
        return envelope_for_fixture(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            operation="get_document",
            meta=meta,
            payload=payload,
            source_identity=self.adapter_identity().to_dict(),
            dependency_identity=self.dependency_identity().to_dict(),
            evidence_refs=[payload.get("pointer") or doc_id],
            logical_source="fixture://vault",
        )
