"""
Neuruh Founder Runtime P1 — persistent mission kernel.

P1 candidate: fixture adapters only. No live execution, no production
persistence, no Cockpit integration, no autonomous production claim.

Public surface (additive over recipe_engine.autonomy P0):
  MissionRuntime, create/run/inspect/replay/recover vertical slice helpers.
"""

from __future__ import annotations

__version__ = "0.1.0"
RUNTIME_VERSION = "founder-runtime-p1.2.0"
SCHEMA_VERSION = "fr.mission.v1"

from .runtime.mission_runtime import MissionRuntime

__all__ = [
    "MissionRuntime",
    "RUNTIME_VERSION",
    "SCHEMA_VERSION",
    "__version__",
]
