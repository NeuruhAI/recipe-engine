"""Structured local logs. No secrets/PII."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime import RUNTIME_VERSION


class RuntimeLog:
    def __init__(self):
        self.records: list[dict[str, Any]] = []

    def emit(
        self,
        *,
        event_type: str,
        mission_id: str | None = None,
        plan_id: str | None = None,
        step_id: str | None = None,
        actor: str = "runtime",
        adapter: str | None = None,
        state: str | None = None,
        authority_class: str | None = None,
        duration_ms: int | None = None,
        result: str = "ok",
        receipt_id: str | None = None,
        error_code: str | None = None,
        timestamp: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rec = {
            "timestamp": timestamp,
            "runtime_version": RUNTIME_VERSION,
            "mission_id": mission_id,
            "plan_id": plan_id,
            "step_id": step_id,
            "event_type": event_type,
            "actor": actor,
            "adapter": adapter,
            "state": state,
            "authority_class": authority_class,
            "duration_ms": duration_ms,
            "result": result,
            "receipt_id": receipt_id,
            "error_code": error_code,
        }
        if extra:
            rec["extra"] = extra
        self.records.append(rec)
        return rec
