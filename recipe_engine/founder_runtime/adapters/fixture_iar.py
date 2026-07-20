"""Fixture IAR — structured interpretation only. No live inference."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.ids import hash_payload, stable_id
from recipe_engine.founder_runtime.adapters.base import BaseFixtureAdapter, AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.identity import fixture_identity, fixture_dependency
from recipe_engine.founder_runtime.adapters.fixture_mixin import envelope_for_fixture
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, RetryPolicyDeclaration
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult


class FixtureIAR(BaseFixtureAdapter):
    name = "iar"
    adapter_version = "p2a.fixture.iar.1"

    def mode(self) -> AdapterMode:
        return AdapterMode.FIXTURE

    def mutation_class(self) -> MutationClass:
        return MutationClass.READ_ONLY

    def retry_policy(self) -> RetryPolicyDeclaration:
        return RetryPolicyDeclaration(max_attempts=1)

    def adapter_identity(self):
        return fixture_identity(self.name, capabilities=("interpret",), version=self.adapter_version)

    def dependency_identity(self):
        return fixture_dependency("iar", endpoint_class="fixture_inference")

    def interpret(
        self,
        request_text: str,
        *,
        principal_id: str = "jeramie",
        meta: AdapterRequestMeta | None = None,
    ) -> dict[str, Any]:
        request_hash = hash_payload({"text": request_text, "principal": principal_id})
        lower = request_text.lower()
        if "where" in lower and ("organism" in lower or "status" in lower or "blocked" in lower):
            intent = "system_status"
            ambiguity = "none"
        elif "delete" in lower or "destroy" in lower or "drop production" in lower:
            intent = "destructive"
            ambiguity = "irreversible"
        elif "or" in lower and "either" in lower:
            intent = "material_choice"
            ambiguity = "material"
        elif lower.strip() in ("", "???"):
            intent = "unknown"
            ambiguity = "impossible"
        else:
            intent = "general"
            ambiguity = "low"

        output = {
            "schema_version": "fr.iar_fixture.v1",
            "backend": "fixture",
            "model": "fixture",
            "principal_id": principal_id,
            "intent": intent,
            "ambiguity": ambiguity,
            "objective": request_text.strip() or "unspecified",
            "entities": ["neuruh_organism"] if intent == "system_status" else [],
            "candidate_strategy_hints": (
                ["inspect_fixture_health", "inspect_fixture_repos", "propose_next_safe_plan"]
                if intent == "system_status"
                else ["request_clarification"]
            ),
            "risk_class": "low" if intent == "system_status" else "medium",
            "authority_hint": "A0" if intent == "system_status" else "A1",
            "request_hash": request_hash,
        }
        output["output_hash"] = hash_payload(output)
        output["receipt_id"] = stable_id("iar_receipt", request_hash)
        return output

    def interpret_result(
        self,
        request_text: str,
        *,
        principal_id: str = "jeramie",
        meta: AdapterRequestMeta | None = None,
    ) -> AdapterResult:
        meta = meta or AdapterRequestMeta(correlation_id="fixture_iar", observed_at="1970-01-01T00:00:00.000Z")
        payload = self.interpret(request_text, principal_id=principal_id, meta=meta)
        return envelope_for_fixture(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            operation="interpret",
            meta=meta,
            payload=payload,
            source_identity=self.adapter_identity().to_dict(),
            dependency_identity=self.dependency_identity().to_dict(),
            evidence_refs=[payload.get("receipt_id", "")],
            logical_source="fixture://iar",
        )
