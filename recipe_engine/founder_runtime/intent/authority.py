"""Authority classification for missions."""

from __future__ import annotations

from recipe_engine.founder_runtime.contracts.authority import AuthorityClass, AuthorityRequirement


def classify_authority(intent: str) -> list[AuthorityRequirement]:
    if intent == "system_status":
        return [
            AuthorityRequirement(
                schema_version="fr.authority_req.v1",
                authority_class=AuthorityClass.A0,
                action="read_fixture_status",
                scope="local_fixture",
                reason="read-only system status",
                requires_approval=False,
            )
        ]
    if intent == "destructive":
        return [
            AuthorityRequirement(
                schema_version="fr.authority_req.v1",
                authority_class=AuthorityClass.A6,
                action="destructive_operation",
                scope="production",
                reason="destructive language detected",
                requires_approval=True,
                enhanced=True,
            )
        ]
    return [
        AuthorityRequirement(
            schema_version="fr.authority_req.v1",
            authority_class=AuthorityClass.A1,
            action="local_analysis",
            scope="local_fixture",
            reason="default analysis",
            requires_approval=False,
        )
    ]
