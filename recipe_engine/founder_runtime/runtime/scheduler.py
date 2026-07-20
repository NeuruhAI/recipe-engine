"""Local in-process scheduler stub — no ports, no daemons."""

from __future__ import annotations

from typing import Callable, Any


class LocalScheduler:
    """Queues callables in-memory for tests. Does not start services."""

    def __init__(self):
        self._q: list[tuple[str, Callable[[], Any]]] = []

    def enqueue(self, name: str, fn: Callable[[], Any]) -> None:
        from .p2_gates import require_p2_gate
        require_p2_gate("R-N")

    def drain(self) -> list[Any]:
        from .p2_gates import require_p2_gate
        require_p2_gate("R-N")
        return []
