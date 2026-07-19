"""Fixture GitHub — repository state fixtures. No writes."""

from __future__ import annotations

from typing import Any

from .base import BaseFixtureAdapter


class FixtureGitHub(BaseFixtureAdapter):
    name = "github"

    def repo_state(self, repo: str) -> dict[str, Any]:
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
