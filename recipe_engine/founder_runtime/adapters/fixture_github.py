"""Fixture GitHub — repository state fixtures. No writes."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.adapters.base import BaseFixtureAdapter, AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.identity import fixture_identity, fixture_dependency
from recipe_engine.founder_runtime.adapters.fixture_mixin import envelope_for_fixture
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, RetryPolicyDeclaration
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult


class FixtureGitHub(BaseFixtureAdapter):
    name = "github"
    adapter_version = "p2a.fixture.github.1"

    def mode(self) -> AdapterMode:
        return AdapterMode.FIXTURE

    def mutation_class(self) -> MutationClass:
        return MutationClass.READ_ONLY

    def retry_policy(self) -> RetryPolicyDeclaration:
        return RetryPolicyDeclaration(max_attempts=1)

    def adapter_identity(self):
        return fixture_identity(self.name, capabilities=("repo_state",), version=self.adapter_version)

    def dependency_identity(self):
        return fixture_dependency("github", endpoint_class="fixture_repo_state")

    def repo_state(self, repo: str, meta: AdapterRequestMeta | None = None) -> dict[str, Any]:
        catalog = {
            "NeuruhAI/recipe-engine": {
                "repo": "NeuruhAI/recipe-engine",
                "default_branch": "main",
                "head_sha": "c32c948478a22bf32441f366c6ab4e8250f17b5d",
                "candidate_branch": "feat/autonomy-core-p0",
                "candidate_sha": "4756e3dbfee66912f8a50dcf9ed2eeaebb1acefd",
                "dirty": False,
                "source": "fixture",
            },
            "NeuruhAI/neuruh-axon": {
                "repo": "NeuruhAI/neuruh-axon",
                "default_branch": "main",
                "head_sha": "fixture_axon_sha",
                "dirty": False,
                "source": "fixture",
            },
        }
        return catalog.get(repo, {"repo": repo, "error": "unknown_fixture_repo", "source": "fixture"})

    def repo_state_result(self, repo: str, meta: AdapterRequestMeta) -> AdapterResult:
        payload = self.repo_state(repo, meta)
        return envelope_for_fixture(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            operation="repo_state",
            meta=meta,
            payload=payload,
            source_identity=self.adapter_identity().to_dict(),
            dependency_identity=self.dependency_identity().to_dict(),
            evidence_refs=[f"github:{repo}"],
            logical_source=f"fixture://github/{repo}",
        )
