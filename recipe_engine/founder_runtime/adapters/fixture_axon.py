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
        started_at: str,
        ended_at: str,
        side_effect_key: str | None = None,
        force_failure: bool = False,
    ) -> ExecutionReceipt:
        # No real execution — return pre-authored fixture receipt
        output = {
            "step_id": step_id,
            "capability": capability,
            "result": "fixture_ok" if not force_failure else "fixture_fail",
            "mutation": False,
        }
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
        )
        receipt.compute_hash()
        return receipt
