"""Composition root for the Founder Runtime API.

Binds 127.0.0.1 only. The operator token comes from FR_API_OPERATOR_TOKEN or
FR_API_OPERATOR_TOKEN_FILE; if neither is set a fresh token is generated and
printed ONCE to stderr for the local operator (never logged by the app).
"""

from __future__ import annotations

import os
import sys

from recipe_engine.founder_runtime.api.auth import ENV_TOKEN, ENV_TOKEN_FILE, OperatorAuth, generate_token
from recipe_engine.founder_runtime.api.server import build_app
from recipe_engine.founder_runtime.api.service import MissionService

DEFAULT_PORT = 19090
HOST = "127.0.0.1"


def make_service(db_path: str) -> MissionService:
    return MissionService(db_path)


def make_auth() -> tuple[OperatorAuth, str | None]:
    """Return (auth, generated_token_if_any). Never returns a configured token."""
    if os.environ.get(ENV_TOKEN, "").strip() or os.environ.get(ENV_TOKEN_FILE, "").strip():
        return OperatorAuth(), None
    token = generate_token()
    return OperatorAuth(token), token


def create_app(db_path: str, *, probe_live: bool = True, prober=None):
    service = make_service(db_path)
    auth, generated = make_auth()
    if generated:
        print(f"[founder-runtime-api] generated operator token (store it safely): {generated}", file=sys.stderr)
    app = build_app(service, auth, probe_live=probe_live, prober=prober)
    app.state.service = service
    app.state.auth = auth
    return app
