"""Fixture AEGIS — evidence/risk opinion only. Not final authority."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.adapters.base import BaseFixtureAdapter, AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.envelope import make_result, AdapterResultStatus, AdapterResult
from recipe_engine.founder_runtime.adapters.identity import (
    fixture_identity, fixture_dependency, EvidenceClass, DivergenceState, Provenance, SCHEMA_PROVENANCE,
)
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, RetryPolicyDeclaration
from recipe_engine.founder_runtime.ids import hash_payload


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
        return fixture_identity(self.name, capabilities=("risk_assess",), version=self.adapter_version)

    def dependency_identity(self):
        return fixture_dependency("aegis", endpoint_class="fixture_opinion")

    def assess_risk(self, subject: dict[str, Any], meta: AdapterRequestMeta | None = None) -> dict[str, Any]:
        sid = hash_payload(subject)[:12]
        return {
            "schema_version": "fr.aegis_fixture.v1",
            "backend": "fixture",
            "subject_hash": sid,
            "risk_class": subject.get("risk_class", "low"),
            "opinion": "fixture_opinion_only",
            "final_authority": False,
            "note": "AEGIS opinion is not Governance final decision",
        }

    def assess_risk_result(self, subject: dict[str, Any], meta: AdapterRequestMeta) -> AdapterResult:
        payload = self.assess_risk(subject, meta)
        return make_result(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            adapter_mode=AdapterMode.FIXTURE,
            operation="assess_risk",
            status=AdapterResultStatus.SUCCESS,
            observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            correlation_id=meta.correlation_id,
            authority_class=meta.authority_value(),
            dependency_identity=self.dependency_identity().to_dict(),
            source_identity=self.adapter_identity().to_dict(),
            source_version_or_sha="fixture",
            provenance={
                "schema_version": SCHEMA_PROVENANCE,
                "source_path_or_logical": "fixture://aegis",
                "source_sha_or_version": "fixture",
                "runtime_sha_or_version": "fixture",
                "divergence_state": DivergenceState.NOT_APPLICABLE.value,
                "retrieval_timestamp": meta.observed_at or "",
                "allowlist_policy_version": "p2a.policy.v1",
                "evidence_class": EvidenceClass.FIXTURE.value,
            },
            payload=payload,
            causation_id=meta.causation_id,
            mission_id=meta.mission_id,
            evidence_refs=[f"aegis:{payload['subject_hash']}"],
        )
