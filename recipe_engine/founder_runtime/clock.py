"""Clock abstraction for deterministic tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now_iso(self) -> str: ...


class SystemClock:
    def now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class FixedClock:
    def __init__(self, iso: str):
        self._iso = iso

    def now_iso(self) -> str:
        return self._iso

    def advance(self, iso: str) -> None:
        self._iso = iso


class SteppingClock:
    """Monotonic synthetic clock for tests."""

    def __init__(self, start: str = "2026-07-19T00:00:00.000Z", step_ms: int = 1000):
        self._ms = _parse_ms(start)
        self._step = step_ms
        self._last = start

    def now_iso(self) -> str:
        self._last = _fmt_ms(self._ms)
        self._ms += self._step
        return self._last

    def peek(self) -> str:
        return _fmt_ms(self._ms)


def _parse_ms(iso: str) -> int:
    # Accept ...Z with optional fractional seconds
    s = iso.replace("Z", "+00:00")
    if "." not in s:
        s = s.replace("+00:00", ".000+00:00")
    dt = datetime.fromisoformat(s)
    return int(dt.timestamp() * 1000)


def _fmt_ms(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(dt.microsecond/1000):03d}Z"
