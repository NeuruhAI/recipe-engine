# Mission State Machine

Explicit table in `recipe_engine/founder_runtime/state/transitions.py`.

## States

DRAFT, INTAKE, CONTEXT_RESOLUTION, PLANNING, AWAITING_DECISION, AWAITING_APPROVAL,
QUEUED, EXECUTING, VERIFYING, RECONCILING, COMPLETED, COMPLETED_WITH_EXCEPTIONS,
BLOCKED_INPUT, BLOCKED_DEPENDENCY, BLOCKED_POLICY, PAUSED, RECOVERING,
FAILED_RETRYABLE, FAILED_FINAL, CANCELLED, SUPERSEDED

## Terminal

COMPLETED, COMPLETED_WITH_EXCEPTIONS, FAILED_FINAL, CANCELLED, SUPERSEDED

## Rules

- Every state change requires a `MissionEvent` inside a transaction
- Unknown transitions fail closed
- Model output cannot transition state
- Terminal states cannot resume
