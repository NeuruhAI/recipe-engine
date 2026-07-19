"""Fixture Governance — policy opinions only. Not real Governance authority."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.contracts.authority import AuthorityClass, FIXTURE_POLICY
from .base import BaseFixtureAdapter


class FixtureGovernance(BaseFixtureAdapter):
    name = "governance"

    def decide(self, authority_class: str | AuthorityClass, *, action: str, scope: str) -> dict[str, Any]:
        ac = AuthorityClass(authority_class) if not isinstance(authority_class, AuthorityClass) else authority_class
        policy = FIXTURE_POLICY[ac]
        if policy == "allow":
            decision = "allow"
        elif policy == "enhanced_approval_required":
            decision = "approval_required"
            enhanced = True
        else:
            decision = "approval_required"
            enhanced = False
        if ac == AuthorityClass.A6:
            enhanced = True
        return {
            "schema_version": "fr.gov_fixture.v1",
            "backend": "fixture",
            "authority_class": ac.value,
            "action": action,
            "scope": scope,
            "decision": decision,
            "enhanced": locals().get("enhanced", ac == AuthorityClass.A6),
            "policy_note": "fixture policy only; autonomous-governance-core remains real authority",
        }
