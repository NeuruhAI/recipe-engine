"""Deterministic adapter registry/factory — no process-environment reads, no live-to-fixture fallback."""

from __future__ import annotations

from typing import Any, Callable

from recipe_engine.founder_runtime.adapters.base import AdapterMode, normalize_mode
from recipe_engine.founder_runtime.adapters.config import (
    AdapterConfig, AdapterBinding, REGISTRY_POLICY_VERSION, fixture_only_config,
)
from recipe_engine.founder_runtime.adapters.axon_mutation import AxonMutationHardStop
from recipe_engine.founder_runtime.adapters.stubs import ModeStubAdapter
from recipe_engine.founder_runtime.errors import (
    AdapterModeInvalid, ConfigurationMissing, LiveFallbackForbidden, MutationNotAuthorized,
)

# Known adapter type keys
ADAPTER_TYPES = frozenset({
    "mother", "github", "vault", "qdrant", "iar", "aegis",
    "governance", "axon_readonly", "axon_mutation", "persistence", "clock", "cockpit",
})


class AdapterRegistry:
    """One implementation per adapter type; explicit mode; no silent fallback."""

    def __init__(self, config: AdapterConfig | None = None):
        self.config = config or fixture_only_config()
        self.policy_version = self.config.policy_version or REGISTRY_POLICY_VERSION
        self._factories: dict[str, dict[str, Callable[..., Any]]] = {}
        self._instances: dict[str, Any] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        # Lazy imports to avoid circulars
        from recipe_engine.founder_runtime.adapters.fixture_mother import FixtureMother
        from recipe_engine.founder_runtime.adapters.fixture_github import FixtureGitHub
        from recipe_engine.founder_runtime.adapters.fixture_vault import FixtureVault
        from recipe_engine.founder_runtime.adapters.fixture_qdrant import FixtureQdrant
        from recipe_engine.founder_runtime.adapters.fixture_iar import FixtureIAR
        from recipe_engine.founder_runtime.adapters.fixture_governance import FixtureGovernance
        from recipe_engine.founder_runtime.adapters.fixture_axon import FixtureAxon
        from recipe_engine.founder_runtime.adapters.fixture_cockpit import FixtureCockpit
        from recipe_engine.founder_runtime.adapters.fixture_aegis import FixtureAegis
        from recipe_engine.founder_runtime.adapters.fixture_persistence import FixturePersistence
        from recipe_engine.founder_runtime.clock import SystemClock, SteppingClock

        self.register("mother", "fixture", lambda **kw: FixtureMother(mode="fixture"))
        self.register("github", "fixture", lambda **kw: FixtureGitHub(mode="fixture"))
        self.register("vault", "fixture", lambda **kw: FixtureVault(mode="fixture"))
        self.register("qdrant", "fixture", lambda **kw: FixtureQdrant(mode="fixture"))
        self.register("iar", "fixture", lambda **kw: FixtureIAR(mode="fixture"))
        self.register("aegis", "fixture", lambda **kw: FixtureAegis(mode="fixture"))
        self.register("governance", "fixture", lambda **kw: FixtureGovernance(mode="fixture"))
        self.register("axon_readonly", "fixture", lambda **kw: FixtureAxon(mode="fixture"))
        self.register("cockpit", "fixture", lambda **kw: FixtureCockpit(mode="fixture"))
        self.register("persistence", "fixture", lambda **kw: FixturePersistence(mode="fixture"))
        self.register("clock", "fixture", lambda **kw: SteppingClock() if kw.get("stepping") else SystemClock())
        self.register("axon_mutation", "hard_stop", lambda **kw: AxonMutationHardStop(**kw))
        self.register("axon_mutation", "disabled", lambda **kw: AxonMutationHardStop(mode=AdapterMode.DISABLED))

    def register(self, adapter_type: str, implementation: str, factory: Callable[..., Any]) -> None:
        if adapter_type not in ADAPTER_TYPES:
            raise AdapterModeInvalid(
                "unknown adapter type",
                {"adapter_type": adapter_type, "known": sorted(ADAPTER_TYPES)},
            )
        bucket = self._factories.setdefault(adapter_type, {})
        if implementation in bucket:
            raise AdapterModeInvalid(
                "duplicate adapter registration",
                {"adapter_type": adapter_type, "implementation": implementation},
            )
        bucket[implementation] = factory

    def resolve(self, adapter_type: str, *, mode: str | AdapterMode | None = None) -> Any:
        if adapter_type not in ADAPTER_TYPES:
            raise AdapterModeInvalid(
                "unknown adapter type",
                {"adapter_type": adapter_type, "known": sorted(ADAPTER_TYPES)},
            )
        binding = self.config.require_binding(adapter_type)
        effective_mode = normalize_mode(mode) if mode is not None else binding.mode

        # AXON mutation always hard-stopped
        if adapter_type == "axon_mutation":
            return AxonMutationHardStop(mode=effective_mode)

        if effective_mode == AdapterMode.LIVE:
            if not self.config.allow_live:
                raise ConfigurationMissing(
                    "live mode not authorized in P2-A config",
                    {"adapter_type": adapter_type},
                )
            # No live implementations registered in P2-A
            impls = self._factories.get(adapter_type, {})
            if "live" not in impls:
                raise ConfigurationMissing(
                    "no live implementation exists for adapter",
                    {"adapter_type": adapter_type, "mode": "live"},
                )
            # Would construct live — still no fallback
            return impls["live"]()

        if effective_mode == AdapterMode.DISABLED:
            return ModeStubAdapter(adapter_type, AdapterMode.DISABLED, reason="policy_disabled")

        if effective_mode == AdapterMode.UNAVAILABLE:
            return ModeStubAdapter(adapter_type, AdapterMode.UNAVAILABLE, reason="dependency_unavailable")

        if effective_mode == AdapterMode.FIXTURE:
            impls = self._factories.get(adapter_type, {})
            key = binding.implementation or "fixture"
            if key not in impls:
                # Never fall back across modes
                raise ConfigurationMissing(
                    "fixture implementation missing; no fallback",
                    {"adapter_type": adapter_type, "implementation": key},
                )
            return impls[key]()

        raise AdapterModeInvalid("unhandled adapter mode", {"mode": effective_mode.value})

    def resolve_all_fixture(self) -> dict[str, Any]:
        out = {}
        for t in sorted(ADAPTER_TYPES):
            if t == "axon_mutation":
                out[t] = self.resolve(t, mode=AdapterMode.DISABLED)
            else:
                out[t] = self.resolve(t, mode=AdapterMode.FIXTURE)
        return out


def default_registry() -> AdapterRegistry:
    return AdapterRegistry(fixture_only_config())
