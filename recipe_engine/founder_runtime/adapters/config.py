"""Explicit typed adapter configuration — no process-environment reads in domain contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from recipe_engine.founder_runtime.adapters.base import AdapterMode, normalize_mode
from recipe_engine.founder_runtime.errors import ConfigurationMissing, AdapterModeInvalid


REGISTRY_POLICY_VERSION = "p2a.registry.v1"


@dataclass(frozen=True)
class AdapterBinding:
    """One adapter type → mode + optional implementation key."""

    adapter_type: str
    mode: AdapterMode
    implementation: str | None = None  # e.g. "fixture", "disabled", "unavailable"
    options: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_type": self.adapter_type,
            "mode": self.mode.value,
            "implementation": self.implementation,
            "options": dict(self.options),
        }


@dataclass(frozen=True)
class AdapterConfig:
    """Typed configuration object supplied by caller/tests — never process environment variables."""

    schema_version: str = "fr.adapter_config.v1"
    policy_version: str = REGISTRY_POLICY_VERSION
    bindings: tuple[AdapterBinding, ...] = ()
    default_mode: AdapterMode = AdapterMode.FIXTURE
    allow_live: bool = False  # P2-A: live implementations do not exist

    def binding_for(self, adapter_type: str) -> AdapterBinding | None:
        for b in self.bindings:
            if b.adapter_type == adapter_type:
                return b
        return None

    def require_binding(self, adapter_type: str) -> AdapterBinding:
        b = self.binding_for(adapter_type)
        if b is None:
            # fall back to default fixture binding for known types
            return AdapterBinding(
                adapter_type=adapter_type,
                mode=self.default_mode,
                implementation="fixture" if self.default_mode == AdapterMode.FIXTURE else self.default_mode.value,
            )
        return b

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "default_mode": self.default_mode.value,
            "allow_live": self.allow_live,
            "bindings": [b.to_dict() for b in self.bindings],
        }


def fixture_only_config(adapter_types: tuple[str, ...] | None = None) -> AdapterConfig:
    types = adapter_types or (
        "mother", "github", "vault", "qdrant", "iar", "aegis",
        "governance", "axon_readonly", "axon_mutation", "persistence", "clock",
    )
    bindings = tuple(
        AdapterBinding(adapter_type=t, mode=AdapterMode.FIXTURE, implementation="fixture")
        for t in types
        if t != "axon_mutation"
    ) + (
        AdapterBinding(adapter_type="axon_mutation", mode=AdapterMode.DISABLED, implementation="hard_stop"),
    )
    return AdapterConfig(bindings=bindings, default_mode=AdapterMode.FIXTURE, allow_live=False)


def parse_mode(value: str | AdapterMode) -> AdapterMode:
    return normalize_mode(value)
