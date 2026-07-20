"""Pure, fail-closed receipt validation."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.errors import ReceiptInvalid
from recipe_engine.founder_runtime.contracts.receipts import ExecutionReceipt, ReceiptStatus
from recipe_engine.founder_runtime.ids import hash_payload


def validate_receipt(
    receipt: ExecutionReceipt,
    *,
    expected_mission_id: str,
    expected_plan_id: str,
    expected_step_id: str,
    expected_input_hash: str | None = None,
    expected_output: dict[str, Any] | None = None,
    expected_output_hash: str | None = None,
    require_authority: bool = True,
    expected_executor: str | None = "fixture_axon",
) -> list[str]:
    """Return errors without mutating the evidence object."""
    errors: list[str] = []
    if receipt.mission_id != expected_mission_id: errors.append("wrong_mission_id")
    if receipt.plan_id != expected_plan_id: errors.append("wrong_plan_id")
    if receipt.step_id != expected_step_id: errors.append("wrong_step_id")
    if expected_executor and receipt.executor != expected_executor: errors.append("wrong_executor")
    if receipt.producer_identity != receipt.executor: errors.append("producer_identity_mismatch")
    if not receipt.capability: errors.append("missing_capability")
    if expected_input_hash is not None and receipt.input_hash != expected_input_hash: errors.append("bad_input_hash")
    if receipt.status != ReceiptStatus.SUCCESS: errors.append("status_not_success")
    if not receipt.output_hash: errors.append("bad_output_hash")
    if expected_output_hash is not None and receipt.output_hash != expected_output_hash: errors.append("wrong_output_hash")
    if not receipt.outcome_data: errors.append("missing_outcome_evidence")
    elif receipt.output_hash != hash_payload(receipt.outcome_data): errors.append("outcome_hash_mismatch")
    if expected_output is not None and receipt.outcome_data != expected_output: errors.append("outcome_binding_mismatch")
    expected_action = f"{receipt.capability}:{receipt.input_hash}:{receipt.output_hash}"
    if receipt.action_binding != expected_action: errors.append("action_binding_mismatch")
    if require_authority:
        parts = receipt.authority_reference.split(":")
        if not receipt.authority_reference:
            errors.append("missing_authority")
        if len(parts) != 4 or parts[0] != "fixture_gov" or parts[2] not in {"A0", "A1"} or parts[3] != "allow":
            errors.append("unresolved_fixture_authority")
        authority = dict(receipt.authority_contract or {})
        supplied_decision_hash = authority.pop("decision_hash", None)
        if (
            authority.get("schema_version") != "fr.gov_fixture.v1"
            or authority.get("backend") != "fixture"
            or authority.get("decision") != "allow"
            or authority.get("authority_class") not in {"A0", "A1"}
            or len(parts) != 4
            or authority.get("decision_id") != (parts[1] if len(parts) == 4 else None)
            or authority.get("authority_class") != (parts[2] if len(parts) == 4 else None)
            or supplied_decision_hash != hash_payload(authority)
        ):
            errors.append("authority_contract_unresolved")
    if receipt.evidence_class != "fixture" or receipt.trust_level != "fixture_only":
        errors.append("invalid_fixture_trust")
    if receipt.mutation_authorized or receipt.files_changed or receipt.repository_metadata.get("mutation") not in (False, None):
        errors.append("fixture_mutation_forbidden")
    if any(token in receipt.capability.casefold() for token in ("write", "delete", "deploy", "send", "shell")):
        errors.append("unsafe_fixture_capability")
    if not receipt.idempotency_key: errors.append("missing_idempotency_key")
    if receipt.started_at > receipt.ended_at: errors.append("timestamp_order")
    if not receipt.receipt_hash: errors.append("missing_receipt_hash")
    elif receipt.receipt_hash != receipt.computed_hash(): errors.append("tampered_receipt")
    for test in receipt.tests:
        if not test.name or not test.passed: errors.append("invalid_test_evidence")
    return errors


def assert_valid_receipt(receipt: ExecutionReceipt, **kwargs: Any) -> None:
    errs = validate_receipt(receipt, **kwargs)
    if errs:
        raise ReceiptInvalid("receipt validation failed", {"errors": errs, "receipt_id": receipt.receipt_id})


def validated_receipt_result(receipt: ExecutionReceipt, **kwargs: Any) -> dict[str, Any]:
    errors = validate_receipt(receipt, **kwargs)
    return {
        "ok": not errors,
        "errors": errors,
        "receipt_id": receipt.receipt_id,
        "evidence_class": receipt.evidence_class,
        "trust_level": receipt.trust_level,
        "producer_identity": receipt.producer_identity,
        "mutation_authorized": receipt.mutation_authorized,
    }
