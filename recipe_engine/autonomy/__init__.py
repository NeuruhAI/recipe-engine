"""
Pure deterministic autonomy core for Recipe Engine.

No database, network, env secrets, filesystem writes, models, AXON, Governance
clients, Vault/Qdrant, or shell execution.

Public API:
  collapse_scenarios
  compile_execution_plan
  reconcile_outcome
  default_config
"""

from .collapse import collapse_scenarios
from .config import ENGINE_VERSION, default_config, validate_config
from .planning import compile_execution_plan
from .reconciliation import reconcile_outcome

__all__ = [
    "collapse_scenarios",
    "compile_execution_plan",
    "reconcile_outcome",
    "default_config",
    "validate_config",
    "ENGINE_VERSION",
]

__version__ = "0.1.0"
