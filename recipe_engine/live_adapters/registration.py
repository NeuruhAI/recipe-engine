"""Wire the live read-only adapters into the EXISTING P2-A.1 registry.

No founder_runtime module imports this file; the composition root (canary /
operator / tests) calls :func:`register_live_adapters` on a registry built from
:func:`live_config`.  This reuses the framework's ``register(type, "live", ...)``
extension point and its ``allow_live`` gate — it does not fork the registry or
create a second framework.  Live is opt-in and explicit; there is still no
live-to-fixture fallback (the registry enforces mode/identity agreement).
"""

from __future__ import annotations

from typing import Callable

from recipe_engine.founder_runtime.adapters.base import AdapterMode
from recipe_engine.founder_runtime.adapters.config import AdapterBinding, AdapterConfig
from recipe_engine.founder_runtime.adapters.registry import AdapterRegistry
from recipe_engine.live_adapters import LIVE_POLICY_VERSION
from recipe_engine.live_adapters.live_aegis import LiveAegisAdapter
from recipe_engine.live_adapters.live_axon import LiveAxonReadonlyAdapter
from recipe_engine.live_adapters.live_github import LiveGitHubAdapter
from recipe_engine.live_adapters.live_governance import LiveGovernanceAdapter
from recipe_engine.live_adapters.live_iar import LiveIARAdapter
from recipe_engine.live_adapters.live_mother import LiveMotherAdapter
from recipe_engine.live_adapters.live_qdrant import LiveQdrantAdapter
from recipe_engine.live_adapters.live_vault import LiveVaultAdapter
from recipe_engine.live_adapters.settings import LiveAdapterSettings

# adapter_type -> live class.  Ordering here mirrors the P2-B required order:
# github, vault, qdrant, mother, iar, aegis, governance, axon_readonly.
LIVE_ADAPTERS: tuple[tuple[str, type], ...] = (
    ("github", LiveGitHubAdapter),
    ("vault", LiveVaultAdapter),
    ("qdrant", LiveQdrantAdapter),
    ("mother", LiveMotherAdapter),
    ("iar", LiveIARAdapter),
    ("aegis", LiveAegisAdapter),
    ("governance", LiveGovernanceAdapter),
    ("axon_readonly", LiveAxonReadonlyAdapter),
)

LIVE_ADAPTER_ORDER = tuple(t for t, _ in LIVE_ADAPTERS)


def live_config(live_types: tuple[str, ...] | None = None) -> AdapterConfig:
    """An AdapterConfig binding the P2-B live read-only adapters to LIVE mode.

    Every other adapter type keeps a safe non-live binding: ``axon_mutation`` is
    hard-stopped DISABLED; the remaining types stay fixture (they are not part of
    the P2-B live path but must have an explicit binding — no implicit fallback).
    """
    live = set(live_types or LIVE_ADAPTER_ORDER)
    bindings = []
    for t in live:
        bindings.append(AdapterBinding(adapter_type=t, mode=AdapterMode.LIVE, implementation="live"))
    # explicit non-live bindings for the rest
    for t in ("persistence", "clock", "cockpit"):
        bindings.append(AdapterBinding(adapter_type=t, mode=AdapterMode.FIXTURE, implementation="fixture"))
    bindings.append(AdapterBinding(adapter_type="axon_mutation", mode=AdapterMode.DISABLED, implementation="hard_stop"))
    # any live type not requested falls back to an explicit fixture binding
    for t, _ in LIVE_ADAPTERS:
        if t not in live:
            bindings.append(AdapterBinding(adapter_type=t, mode=AdapterMode.FIXTURE, implementation="fixture"))
    return AdapterConfig(
        policy_version=LIVE_POLICY_VERSION,
        bindings=tuple(bindings),
        default_mode=AdapterMode.LIVE,
        allow_live=True,
    )


def register_live_adapters(
    registry: AdapterRegistry,
    settings: LiveAdapterSettings,
    *,
    transports: dict | None = None,
) -> AdapterRegistry:
    """Register a ``"live"`` factory for each P2-B adapter type on ``registry``.

    ``transports`` optionally injects shared transports/gates (used by tests to
    point adapters at a loopback mock or an in-memory gate).  Recognized keys:
    ``git`` (GitObserver), ``http`` (LoopbackHttpClient), ``gate``
    (HostRecoveryGate), ``axon_read_allowlist`` (Mapping).
    """
    transports = transports or {}

    def _factory(adapter_type: str, cls: type) -> Callable[..., object]:
        def build(**_kw: object) -> object:
            if adapter_type == "github":
                return cls(settings, observer=transports.get("git"))
            if adapter_type == "vault":
                return cls(settings, observer=transports.get("git"))
            if adapter_type == "qdrant":
                return cls(settings, http=transports.get("http"), gate=transports.get("gate"))
            if adapter_type == "axon_readonly":
                return cls(settings, http=transports.get("http"), read_allowlist=transports.get("axon_read_allowlist"))
            # mother / iar / aegis / governance
            return cls(settings, http=transports.get("http"))
        return build

    for adapter_type, cls in LIVE_ADAPTERS:
        try:
            registry.register(adapter_type, "live", _factory(adapter_type, cls))
        except Exception:  # noqa: BLE001 - already registered (idempotent for re-wiring)
            pass
    return registry


def build_live_registry(settings: LiveAdapterSettings, *, transports: dict | None = None) -> AdapterRegistry:
    registry = AdapterRegistry(live_config())
    register_live_adapters(registry, settings, transports=transports)
    return registry
