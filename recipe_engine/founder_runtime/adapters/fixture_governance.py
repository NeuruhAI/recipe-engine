"""Fixture Governance — policy opinions only. Not real Governance authority."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.contracts.authority import AuthorityClass, FIXTURE_POLICY
from recipe_engine.founder_runtime.ids import hash_payload, stable_id
from recipe_engine.founder_runtime.adapters.base import BaseFixtureAdapter, AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.identity import fixture_identity, fixture_dependency
from recipe_engine.founder_runtime.adapters.fixture_mixin import envelope_for_fixture
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, RetryPolicyDeclaration
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult


class FixtureGovernance(BaseFixtureAdapter):
    name = "governance"
    adapter_version = "p2a.fixture.governance.1"

    def mode(self) -> AdapterMode:
        return AdapterMode.FIXTURE

    def mutation_class(self) -> MutationClass:
        return MutationClass.READ_ONLY

    def retry_policy(self) -> RetryPolicyDeclaration:
        return RetryPolicyDeclaration(max_attempts=1)

    def adapter_identity(self):
        return fixture_identity(
            self.name,
            capabilities=("health", "default_policy", "fixture_decide"),
            version=self.adapter_version,
        )

    def dependency_identity(self):
        return fixture_dependency("governance", endpoint_class="fixture_policy")

    def health(self, meta: AdapterRequestMeta) -> AdapterResult:
        return envelope_for_fixture(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            operation="health",
            meta=meta,
            payload={"ok": True, "service": "fixture_governance"},
            source_identity=self.adapter_identity().to_dict(),
            dependency_identity=self.dependency_identity().to_dict(),
            logical_source="fixture://governance/health",
        )

    def default_policy(self, meta: AdapterRequestMeta) -> AdapterResult:
        return envelope_for_fixture(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            operation="default_policy",
            meta=meta,
            payload={
                "policy_version": "fixture",
                "authority_classes": {key.value: value for key, value in FIXTURE_POLICY.items()},
                "evidence_class": "fixture",
            },
            source_identity=self.adapter_identity().to_dict(),
            dependency_identity=self.dependency_identity().to_dict(),
            logical_source="fixture://governance/default-policy",
        )

    def decide(
        self,
        authority_class: str | AuthorityClass,
        *,
        action: str,
        scope: str,
        meta: AdapterRequestMeta | None = None,
    ) -> dict[str, Any]:
        ac = AuthorityClass(authority_class) if not isinstance(authority_class, AuthorityClass) else authority_class
        policy = FIXTURE_POLICY[ac]
        enhanced = ac == AuthorityClass.A6
        if policy == "allow":
            decision = "allow"
        elif policy == "enhanced_approval_required":
            decision = "approval_required"
            enhanced = True
        else:
            decision = "approval_required"
        result = {
            "schema_version": "fr.gov_fixture.v1",
            "backend": "fixture",
            "authority_class": ac.value,
            "action": action,
            "scope": scope,
            "decision": decision,
            "enhanced": enhanced,
            "policy_note": "fixture policy only; autonomous-governance-core remains real authority",
        }
        result["decision_id"] = stable_id("fixture_gov", ac.value, action, scope, decision)
        result["decision_hash"] = hash_payload(result)
        return result

    def decide_result(
        self,
        authority_class: str | AuthorityClass,
        *,
        action: str,
        scope: str,
        meta: AdapterRequestMeta,
    ) -> AdapterResult:
        payload = self.decide(authority_class, action=action, scope=scope, meta=meta)
        return envelope_for_fixture(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            operation="decide",
            meta=meta,
            payload=payload,
            source_identity=self.adapter_identity().to_dict(),
            dependency_identity=self.dependency_identity().to_dict(),
            evidence_refs=[f"gov:{payload['authority_class']}:{payload['decision']}"],
            logical_source="fixture://governance",
        )
