"""Canonical per-adapter, per-operation timeout and retry declarations.

This module is contract-only.  It never performs I/O or sleeps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from recipe_engine.founder_runtime.errors import ConfigurationMissing, UnsafeCapability


@dataclass(frozen=True)
class OperationPolicy:
    adapter_name: str
    operation: str
    default_timeout_ms: int
    ceiling_timeout_ms: int
    max_attempts: int = 1
    retry_on: tuple[str, ...] = ()
    retry_requires_fresh_before_count: bool = False
    timeout_classification: str = "TIMEOUT"

    def __post_init__(self) -> None:
        if self.default_timeout_ms <= 0 or self.ceiling_timeout_ms <= 0:
            raise ValueError("timeouts must be positive")
        if self.default_timeout_ms > self.ceiling_timeout_ms:
            raise ValueError("default timeout exceeds policy ceiling")
        if self.max_attempts < 1 or self.max_attempts > 3:
            raise ValueError("attempt count must be explicitly bounded")

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_name": self.adapter_name,
            "operation": self.operation,
            "default_timeout_ms": self.default_timeout_ms,
            "ceiling_timeout_ms": self.ceiling_timeout_ms,
            "max_attempts": self.max_attempts,
            "retry_on": list(self.retry_on),
            "retry_requires_fresh_before_count": self.retry_requires_fresh_before_count,
            "timeout_classification": self.timeout_classification,
        }


def _p(
    adapter: str,
    operation: str,
    timeout_ms: int,
    *,
    ceiling_ms: int | None = None,
    attempts: int = 1,
    retry_on: tuple[str, ...] = (),
    fresh_count: bool = False,
    timeout_classification: str = "TIMEOUT",
) -> OperationPolicy:
    return OperationPolicy(
        adapter,
        operation,
        timeout_ms,
        ceiling_ms or timeout_ms,
        attempts,
        retry_on,
        fresh_count,
        timeout_classification,
    )


_POLICIES = (
    _p("github", "repo_state", 10_000),
    _p("github", "ls_remote", 10_000, attempts=2, retry_on=("connect",)),
    _p("vault", "provenance_state", 5_000),
    _p("vault", "get_document", 5_000),
    _p("qdrant", "readyz", 6_000, attempts=2, retry_on=("connect",), fresh_count=True),
    _p("qdrant", "collection_census", 6_000, attempts=2, retry_on=("connect",), fresh_count=True),
    _p("qdrant", "manifest_scroll", 8_000, attempts=2, retry_on=("connect",), fresh_count=True),
    _p("qdrant", "verify_collection", 8_000, attempts=2, retry_on=("connect",), fresh_count=True),
    _p("qdrant", "search", 5_000),  # fixture-only compatibility; semantic live search is gated
    _p("mother", "read_equivalence_receipt", 5_000),
    _p("mother", "health_snapshot", 5_000),  # fixture-only compatibility
    _p("iar", "health_subrequest", 3_000),
    _p("iar", "health", 6_000),
    _p("iar", "backend_health_cache_ttl", 15_000),
    _p("iar", "interpret_local_deferred", 60_000, ceiling_ms=300_000, attempts=3),
    _p("iar", "interpret_cloud_deferred", 60_000, attempts=3),
    _p("iar", "verify_provenance", 6_000),
    _p("iar", "interpret", 60_000, ceiling_ms=300_000, attempts=3),  # fixture/deferred live route
    _p("aegis", "validate_world", 10_000),
    _p("aegis", "http_identity_deferred", 5_000),
    _p("governance", "health", 4_000),
    _p("governance", "default_policy", 4_000),
    _p("governance", "decide_deferred", 4_000),
    _p("governance", "decide", 4_000),  # fixture compatibility; live decision remains gated
    # T31B: the authority grant on /v2/authorize. A bounded, declared timeout is
    # part of failing closed — an authority organ that never answers must become
    # a denial at a known moment, not an open-ended wait.
    # The authority question is asked once per resolve and a timeout parks the
    # mission fail-closed; the live surface answers in milliseconds but has
    # stalled past 4s under host load, so the ceiling admits a longer ask.
    _p("governance", "authorize", 4_000, ceiling_ms=10_000),
    _p("axon_readonly", "identity", 5_000),
    _p("axon_readonly", "tools_census", 5_000),
    _p(
        "axon_readonly",
        "governed_read",
        30_000,
        timeout_classification="SIDE_EFFECT_UNCERTAIN",
    ),
    _p("axon_readonly", "traversal_probe", 5_000),
    _p("axon_readonly", "governance_subcall", 4_000),
    _p("axon_readonly", "internal_probe", 2_000),
    _p("axon", "execute_readonly_step", 30_000),  # fixture receipt compatibility
    _p("cockpit", "status", 5_000),
)

OPERATION_POLICIES: dict[tuple[str, str], OperationPolicy] = {
    (policy.adapter_name, policy.operation): policy for policy in _POLICIES
}


@dataclass(frozen=True)
class ResolvedOperationPolicy:
    declared_timeout_ms: int
    effective_timeout_ms: int
    policy: OperationPolicy

    def retry_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.policy.max_attempts,
            "retry_on": list(self.policy.retry_on),
            "requires_fresh_before_count": self.policy.retry_requires_fresh_before_count,
            "timeout_classification": self.policy.timeout_classification,
        }


def operation_policy(adapter_name: str, operation: str) -> OperationPolicy:
    try:
        return OPERATION_POLICIES[(adapter_name, operation)]
    except KeyError as exc:
        raise ConfigurationMissing(
            "no canonical operation timeout policy",
            {"adapter_name": adapter_name, "operation": operation},
        ) from exc


def resolve_operation_policy(
    adapter_name: str,
    operation: str,
    declared_timeout_ms: int | None = None,
) -> ResolvedOperationPolicy:
    policy = operation_policy(adapter_name, operation)
    declared = policy.default_timeout_ms if declared_timeout_ms is None else declared_timeout_ms
    if isinstance(declared, bool) or not isinstance(declared, int) or declared <= 0:
        raise UnsafeCapability(
            "timeout override must be a positive integer",
            {"adapter_name": adapter_name, "operation": operation},
        )
    if declared > policy.ceiling_timeout_ms:
        raise UnsafeCapability(
            "timeout override exceeds canonical policy ceiling",
            {
                "adapter_name": adapter_name,
                "operation": operation,
                "declared_timeout_ms": declared,
                "ceiling_timeout_ms": policy.ceiling_timeout_ms,
            },
        )
    return ResolvedOperationPolicy(declared, declared, policy)


NON_RETRYABLE_ERRORS = frozenset(
    {
        "AUTHENTICATION_FAILED",
        "AUTHORIZATION_DENIED",
        "CONTEXT_NOT_FOUND",
        "PROVENANCE_INVALID",
        "IDENTITY_MISMATCH",
        "QDRANT_COUNT_MISMATCH",
        "QDRANT_COUNT_CHANGED",
    }
)


def retry_permitted(resolved: ResolvedOperationPolicy, error_code: str) -> bool:
    if error_code in NON_RETRYABLE_ERRORS or resolved.policy.max_attempts == 1:
        return False
    return error_code.lower() in {value.lower() for value in resolved.policy.retry_on}
