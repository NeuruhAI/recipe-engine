"""Strict receipt validation. Agent statements cannot replace receipts."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.errors import ReceiptInvalid
from recipe_engine.founder_runtime.contracts.receipts import ExecutionReceipt, ReceiptStatus


def validate_receipt(
    receipt: ExecutionReceipt,
    *,
    expected_mission_id: str,
    expected_plan_id: str,
    expected_step_id: str,
    expected_input_hash: str | None = None,
    require_authority: bool = True,
    expected_executor: str | None = "fixture_axon",
) -> list[str]:
    """Return list of errors; empty means valid."""
    errors: list[str] = []
    if receipt.mission_id != expected_mission_id:
        errors.append("wrong_mission_id")
    if receipt.plan_id != expected_plan_id:
        errors.append("wrong_plan_id")
    if receipt.step_id != expected_step_id:
        errors.append("wrong_step_id")
    if expected_executor and receipt.executor != expected_executor:
        errors.append("wrong_executor")
    if not receipt.capability:
        errors.append("missing_capability")
    if expected_input_hash is not None and receipt.input_hash != expected_input_hash:
        errors.append("bad_input_hash")
    if not receipt.output_hash:
        errors.append("bad_output_hash")
    if require_authority and not receipt.authority_reference:
        errors.append("missing_authority")
    if not receipt.idempotency_key:
        errors.append("missing_idempotency_key")
    if receipt.started_at > receipt.ended_at:
        errors.append("timestamp_order")
    # hash integrity
    stored = receipt.receipt_hash
    recomputed = receipt.compute_hash()
    if stored and stored != recomputed:
        errors.append("tampered_receipt")
    # restore
    receipt.receipt_hash = recomputed
    # repo metadata relationship if present
    meta = receipt.repository_metadata or {}
    if meta.get("base_commit") and meta.get("result_commit"):
        if meta["base_commit"] == meta["result_commit"] and receipt.files_changed:
            errors.append("repo_commit_relationship")
    # tests structure
    for t in receipt.tests:
        if not t.name:
            errors.append("invalid_test_structure")
    return errors


def assert_valid_receipt(receipt: ExecutionReceipt, **kwargs: Any) -> None:
    errs = validate_receipt(receipt, **kwargs)
    if errs:
        raise ReceiptInvalid("receipt validation failed", {"errors": errs, "receipt_id": receipt.receipt_id})
