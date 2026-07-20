"""Fixture AXON — pre-authored read-only receipts. No shell execution."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.contracts.receipts import (
    ExecutionReceipt, ReceiptStatus, TestResult, ArtifactRef, SCHEMA_RECEIPT,
)
from recipe_engine.founder_runtime.ids import hash_payload, stable_id
from recipe_engine.founder_runtime.adapters.base import BaseFixtureAdapter, AdapterMode, MutationClass
from recipe_engine.founder_runtime.adapters.identity import fixture_identity, fixture_dependency
from recipe_engine.founder_runtime.adapters.fixture_mixin import envelope_for_fixture
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta, RetryPolicyDeclaration
from recipe_engine.founder_runtime.adapters.envelope import AdapterResult


class FixtureAxon(BaseFixtureAdapter):
    name = "axon"
    adapter_version = "p2a.fixture.axon.1"

    def mode(self) -> AdapterMode:
        return AdapterMode.FIXTURE

    def mutation_class(self) -> MutationClass:
        return MutationClass.READ_ONLY

    def retry_policy(self) -> RetryPolicyDeclaration:
        return RetryPolicyDeclaration(max_attempts=1)

    def adapter_identity(self):
        return fixture_identity(self.name, capabilities=("execute_readonly_step",), version=self.adapter_version)

    def dependency_identity(self):
        return fixture_dependency("axon", endpoint_class="fixture_readonly")


    def execute_readonly_step(
        self,
        *,
        mission_id: str,
        plan_id: str,
        step_id: str,
        capability: str,
        input_hash: str,
        authority_reference: str,
        authority_contract: dict[str, Any] | None = None,
        started_at: str,
        ended_at: str,
        side_effect_key: str | None = None,
        force_failure: bool = False,
        outcome_data: dict[str, Any] | None = None,
    ) -> ExecutionReceipt:
        # Deterministic fixture evidence binds the actual allowlisted fixture
        # result; callers cannot assert SUCCESS independently of that result.
        output = dict(outcome_data or {})
        output.update({"step_id": step_id, "capability": capability, "mutation": False})
        if force_failure:
            output["fixture_error"] = "forced_failure"
        output_hash = hash_payload(output)
        status = ReceiptStatus.FAILURE if force_failure else ReceiptStatus.SUCCESS
        receipt = ExecutionReceipt(
            schema_version=SCHEMA_RECEIPT,
            receipt_id=stable_id("rcpt", mission_id, plan_id, step_id, input_hash),
            mission_id=mission_id,
            plan_id=plan_id,
            step_id=step_id,
            executor="fixture_axon",
            capability=capability,
            started_at=started_at,
            ended_at=ended_at,
            status=status,
            input_hash=input_hash,
            output_hash=output_hash,
            repository_metadata={"mode": "fixture", "mutation": False},
            files_changed=[],
            tests=[TestResult(name=f"fixture_{step_id}", passed=not force_failure)],
            artifacts=[
                ArtifactRef(
                    artifact_id=stable_id("art", step_id),
                    kind="fixture_report",
                    content_hash=output_hash,
                    pointer=f"fixture://step/{step_id}",
                )
            ] if not force_failure else [],
            error="forced fixture failure" if force_failure else None,
            authority_reference=authority_reference,
            idempotency_key=f"step:{mission_id}:{plan_id}:{step_id}",
            side_effect_key=side_effect_key,
            evidence_class="fixture",
            trust_level="fixture_only",
            producer_identity="fixture_axon",
            action_binding=f"{capability}:{input_hash}:{output_hash}",
            outcome_data=output,
            mutation_authorized=False,
            authority_contract=dict(authority_contract or {}),
        )
        receipt.compute_hash()
        return receipt

    def execute_readonly_result(self, **kwargs: Any) -> AdapterResult:
        meta = kwargs.pop("meta", None)
        if meta is None:
            meta = AdapterRequestMeta(
                correlation_id=str(kwargs.get("mission_id") or "fixture_axon"),
                mission_id=kwargs.get("mission_id"),
                plan_id=kwargs.get("plan_id"),
                step_id=kwargs.get("step_id"),
                observed_at=kwargs.get("started_at") or "1970-01-01T00:00:00.000Z",
            )
        receipt = self.execute_readonly_step(**kwargs)
        return envelope_for_fixture(
            adapter_name=self.name,
            adapter_version=self.adapter_version,
            operation="execute_readonly_step",
            meta=meta,
            payload=receipt.to_dict(),
            source_identity=self.adapter_identity().to_dict(),
            dependency_identity=self.dependency_identity().to_dict(),
            evidence_refs=[receipt.receipt_id],
            logical_source="fixture://axon/readonly",
        )
