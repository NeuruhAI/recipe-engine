"""Composition root for the Founder Runtime API.

Binds 127.0.0.1 only. The operator token comes from FR_API_OPERATOR_TOKEN or
FR_API_OPERATOR_TOKEN_FILE; if neither is set a fresh token is generated and
printed ONCE to stderr for the local operator (never logged by the app).

The founder credential (the only one the approve routes accept) is configured as
FR_FOUNDER_TOKEN_SHA256 + FR_FOUNDER_IDENTITY. FR never sees the founder token.
"""

from __future__ import annotations

import os
import sys

from recipe_engine.founder_runtime.api.auth import ENV_TOKEN, ENV_TOKEN_FILE, FounderAuth, OperatorAuth, generate_token
from recipe_engine.founder_runtime.api.server import build_app
from recipe_engine.founder_runtime.api.service import MissionService

DEFAULT_PORT = 19090
HOST = "127.0.0.1"


def make_service(db_path: str, **service_kwargs) -> MissionService:
    return MissionService(db_path, **service_kwargs)


def make_auth() -> tuple[OperatorAuth, str | None]:
    """Return (auth, generated_token_if_any). Never returns a configured token."""
    if os.environ.get(ENV_TOKEN, "").strip() or os.environ.get(ENV_TOKEN_FILE, "").strip():
        return OperatorAuth(), None
    token = generate_token()
    return OperatorAuth(token), token


def create_app(db_path: str, *, probe_live: bool = True, prober=None, governance=None,
               attested_source_sha: str = "", founder_grant=None, auto_resolve: bool = True):
    """Build the app. ``governance`` is the resolver's authority port (an object
    with ``authorize(...)``), supplied by a composition root outside this
    package; without it every mission parks honestly (RESOLVER_GOVERNANCE_PORT_ABSENT)."""
    service = make_service(db_path, governance=governance, attested_source_sha=attested_source_sha,
                           founder_grant=founder_grant, auto_resolve=auto_resolve)
    auth, generated = make_auth()
    if generated:
        print(f"[founder-runtime-api] generated operator token (store it safely): {generated}", file=sys.stderr)
    # The founder credential for the two approval routes (G-1). A raw founder
    # token in the environment, a malformed digest, or a founder token equal to
    # the operator token raises FounderAuthConfigError here: FR refuses to start
    # rather than run with a founder credential it cannot trust.
    founder_auth = FounderAuth(operator=auth)
    app = build_app(service, auth, probe_live=probe_live, prober=prober, founder_auth=founder_auth)
    app.state.service = service
    app.state.auth = auth
    app.state.founder_auth = founder_auth
    return app
