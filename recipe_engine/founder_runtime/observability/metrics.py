"""Deterministic local metrics. No network telemetry."""

from __future__ import annotations

from collections import Counter
from typing import Any


class Metrics:
    def __init__(self):
        self.counters: Counter[str] = Counter()
        self.gauges: dict[str, float] = {}

    def inc(self, name: str, n: int = 1) -> None:
        self.counters[name] += n

    def set_gauge(self, name: str, value: float) -> None:
        self.gauges[name] = value

    def snapshot(self) -> dict[str, Any]:
        return {"counters": dict(self.counters), "gauges": dict(self.gauges)}
