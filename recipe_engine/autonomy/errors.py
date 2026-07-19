"""Autonomy core error types. Pure; no I/O."""

from __future__ import annotations


class AutonomyError(Exception):
    """Base error for the pure autonomy package."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"[{code}] {message}")


class ValidationError(AutonomyError):
    """Contract or input validation failure (fail-closed)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__("validation_error", message, details)


class ConfigError(AutonomyError):
    """Invalid engine configuration."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__("config_error", message, details)


class PlanningError(AutonomyError):
    """Execution-plan proposal compilation failure."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__("planning_error", message, details)


class ReconciliationError(AutonomyError):
    """Outcome reconciliation failure."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__("reconciliation_error", message, details)
