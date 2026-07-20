"""Explicit guards for doctrine expressly deferred beyond fixture-only P1.2."""

from recipe_engine.founder_runtime.errors import AdapterModeInvalid


P2_GATES = {
    "R-L": "resume_executor_and_skip_completed",
    "R-M": "external_side_effect_reconciliation",
    "R-N": "retry_scheduler_and_concurrent_dispatch",
    "R-O": "production_persistence_and_concurrency",
}
PRODUCTION_PERSISTENCE_AUTHORIZED = False
EXECUTION_RESUME_AUTHORIZED = False


def require_p2_gate(gate: str) -> None:
    if gate not in P2_GATES:
        raise AdapterModeInvalid("unknown P2 gate", {"gate": gate})
    raise AdapterModeInvalid(
        "P1.2 fixture kernel rejects deferred capability",
        {"gate": gate, "capability": P2_GATES[gate]},
    )
