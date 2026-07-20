"""Retry policy helpers (no external side effects)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_ms: int = 0  # fixture: no sleep


def should_retry(attempt: int, policy: RetryPolicy) -> bool:
    from .p2_gates import require_p2_gate
    require_p2_gate("R-N")
    return False
