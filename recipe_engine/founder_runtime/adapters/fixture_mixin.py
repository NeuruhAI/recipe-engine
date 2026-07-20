"""Shared fixture retrofit helpers for result envelopes."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.adapters.base import AdapterMode
from recipe_engine.founder_runtime.adapters.envelope import make_result, AdapterResultStatus, AdapterResult
from recipe_engine.founder_runtime.adapters.identity import (
    EvidenceClass, DivergenceState, SCHEMA_PROVENANCE,
)
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta


def fixture_provenance(logical: str, observed_at: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_PROVENANCE,
        "source_path_or_logical": logical,
        "source_sha_or_version": "fixture",
        "runtime_sha_or_version": "fixture",
        "divergence_state": DivergenceState.NOT_APPLICABLE.value,
        "retrieval_timestamp": observed_at,
        "allowlist_policy_version": "p2a.policy.v1",
        "evidence_class": EvidenceClass.FIXTURE.value,
    }


def envelope_for_fixture(
    *,
    adapter_name: str,
    adapter_version: str,
    operation: str,
    meta: AdapterRequestMeta,
    payload: dict[str, Any],
    source_identity: dict[str, Any],
    dependency_identity: dict[str, Any],
    evidence_refs: list[str] | None = None,
    status: AdapterResultStatus = AdapterResultStatus.SUCCESS,
    error_code: str | None = None,
    logical_source: str | None = None,
) -> AdapterResult:
    observed = meta.observed_at or "1970-01-01T00:00:00.000Z"
    return make_result(
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        adapter_mode=AdapterMode.FIXTURE,
        operation=operation,
        status=status,
        observed_at=observed,
        correlation_id=meta.correlation_id,
        authority_class=meta.authority_value(),
        dependency_identity=dependency_identity,
        source_identity=source_identity,
        source_version_or_sha="fixture",
        provenance=fixture_provenance(logical_source or f"fixture://{adapter_name}", observed),
        payload=payload,
        causation_id=meta.causation_id,
        mission_id=meta.mission_id,
        plan_id=meta.plan_id,
        step_id=meta.step_id,
        mutation_attempted=False,
        evidence_refs=evidence_refs or [],
        error_code=error_code,
        retryable=False,
        fallback_used=False,
        declared_timeout_ms=meta.timeout_ms,
    )
