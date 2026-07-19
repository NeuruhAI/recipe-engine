"""Explicit transition table. Unknown transitions fail closed."""

from __future__ import annotations

from .states import MissionState as S

# Directed edges: from_state -> allowed next states
TRANSITIONS: dict[S, frozenset[S]] = {
    S.DRAFT: frozenset({S.INTAKE, S.CANCELLED, S.SUPERSEDED}),
    S.INTAKE: frozenset({
        S.CONTEXT_RESOLUTION, S.BLOCKED_INPUT, S.CANCELLED, S.SUPERSEDED, S.PAUSED,
    }),
    S.CONTEXT_RESOLUTION: frozenset({
        S.PLANNING, S.BLOCKED_INPUT, S.BLOCKED_DEPENDENCY, S.CANCELLED, S.PAUSED,
    }),
    S.PLANNING: frozenset({
        S.AWAITING_DECISION, S.AWAITING_APPROVAL, S.QUEUED, S.BLOCKED_POLICY,
        S.BLOCKED_INPUT, S.CANCELLED, S.PAUSED,
    }),
    S.AWAITING_DECISION: frozenset({
        S.AWAITING_APPROVAL, S.QUEUED, S.BLOCKED_POLICY, S.CANCELLED, S.PAUSED,
        S.PLANNING,
    }),
    S.AWAITING_APPROVAL: frozenset({
        S.QUEUED, S.BLOCKED_POLICY, S.CANCELLED, S.PAUSED, S.PLANNING,
    }),
    S.QUEUED: frozenset({
        S.EXECUTING, S.PAUSED, S.CANCELLED, S.BLOCKED_DEPENDENCY,
    }),
    S.EXECUTING: frozenset({
        S.VERIFYING, S.RECONCILING, S.PAUSED, S.FAILED_RETRYABLE, S.FAILED_FINAL,
        S.BLOCKED_DEPENDENCY, S.BLOCKED_POLICY, S.AWAITING_APPROVAL, S.CANCELLED,
        S.RECOVERING,
    }),
    S.VERIFYING: frozenset({
        S.RECONCILING, S.COMPLETED, S.COMPLETED_WITH_EXCEPTIONS, S.FAILED_RETRYABLE,
        S.FAILED_FINAL, S.PAUSED, S.CANCELLED,
    }),
    S.RECONCILING: frozenset({
        S.COMPLETED, S.COMPLETED_WITH_EXCEPTIONS, S.FAILED_FINAL, S.FAILED_RETRYABLE,
        S.VERIFYING, S.PAUSED,
    }),
    S.BLOCKED_INPUT: frozenset({
        S.INTAKE, S.CONTEXT_RESOLUTION, S.PLANNING, S.CANCELLED, S.PAUSED, S.SUPERSEDED,
    }),
    S.BLOCKED_DEPENDENCY: frozenset({
        S.QUEUED, S.EXECUTING, S.CONTEXT_RESOLUTION, S.CANCELLED, S.PAUSED, S.RECOVERING,
    }),
    S.BLOCKED_POLICY: frozenset({
        S.AWAITING_APPROVAL, S.PLANNING, S.CANCELLED, S.PAUSED, S.SUPERSEDED,
    }),
    S.PAUSED: frozenset({
        S.QUEUED, S.EXECUTING, S.PLANNING, S.CONTEXT_RESOLUTION, S.AWAITING_APPROVAL,
        S.RECOVERING, S.CANCELLED, S.SUPERSEDED, S.INTAKE,
    }),
    S.RECOVERING: frozenset({
        S.QUEUED, S.EXECUTING, S.VERIFYING, S.RECONCILING, S.PAUSED, S.FAILED_RETRYABLE,
        S.FAILED_FINAL, S.BLOCKED_DEPENDENCY, S.CANCELLED,
    }),
    S.FAILED_RETRYABLE: frozenset({
        S.QUEUED, S.EXECUTING, S.RECOVERING, S.PAUSED, S.FAILED_FINAL, S.CANCELLED,
    }),
    # Terminal — empty (cannot leave)
    S.COMPLETED: frozenset(),
    S.COMPLETED_WITH_EXCEPTIONS: frozenset(),
    S.FAILED_FINAL: frozenset(),
    S.CANCELLED: frozenset(),
    S.SUPERSEDED: frozenset(),
}


def is_transition_allowed(prior: S, next_state: S) -> bool:
    allowed = TRANSITIONS.get(prior)
    if allowed is None:
        return False
    return next_state in allowed


def all_valid_edges() -> list[tuple[S, S]]:
    edges: list[tuple[S, S]] = []
    for src, dests in TRANSITIONS.items():
        for d in sorted(dests, key=lambda x: x.value):
            edges.append((src, d))
    return edges
