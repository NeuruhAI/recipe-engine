"""Fixture AXON — pre-authored read-only receipts. No shell execution."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.contracts.receipts import (
    ExecutionReceipt, ReceiptStatus, TestResult, ArtifactRef, SCHEMA_RECEIPT,
)
from recipe_engine.founder_runtime.ids import hash_payload, stable_id
from .base import BaseFixtureAdapter


class FixtureAxon(BaseFixtureAdapter):
    name = "axon"

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
