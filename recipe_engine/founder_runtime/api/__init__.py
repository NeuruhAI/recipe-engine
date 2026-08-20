"""Founder Runtime API v1 package.

``build_app`` is resolved lazily. It is the only export that reaches the FastAPI
transport; the authority surface — MissionService and the operator auth — is
pure Python and must stay importable on a host that has no web framework
installed, because that is what the mission's external approval channel is made
of. Importing the transport eagerly here made FastAPI a hard requirement for
recording a founder decision at all.
"""

from typing import Any

from recipe_engine.founder_runtime.api.auth import OperatorAuth, generate_token
from recipe_engine.founder_runtime.api.service import MissionService, API_VERSION

__all__ = ["OperatorAuth", "generate_token", "build_app", "MissionService", "API_VERSION"]


def build_app(*args: Any, **kwargs: Any):
    """Construct the HTTP transport. Requires fastapi at call time, not import time."""
    from recipe_engine.founder_runtime.api.server import build_app as _build_app

    return _build_app(*args, **kwargs)
