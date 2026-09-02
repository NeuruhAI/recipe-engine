"""Compose the Founder Runtime API with its LIVE Governance authorization port.

``founder_runtime`` must never import ``live_adapters`` (the AST confinement
scan walks every module under it, transitively), so the wiring that gives the
approval resolver a real Governance lives here, on the live side, and imports
the API.  Same idiom as ``composition.build_live_organs`` for missions.

    python -m recipe_engine.live_adapters.founder_api --db <sqlite> --port 19090

Adds to the plain ``api.serve`` entrypoint:
  * a loopback ``LiveGovernanceAdapter`` on ``/v2/authorize`` (FR_GOVERNANCE_PORT, default 8848);
  * the git source SHA this runtime attests (FR_SOURCE_SHA, else ``git rev-parse HEAD``
    of this package's repository; an unresolvable SHA leaves missions parked, never guessed);
  * an optional founder-signed ``gov.grant.v1`` read verbatim from FR_FOUNDER_GRANT_FILE.

Nothing here signs.  The founder signing key, if present in this process's
environment, is used only by the existing gate/approve route; the resolver has
no path to it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from recipe_engine.founder_runtime.api.app import DEFAULT_PORT, HOST, create_app
from recipe_engine.founder_runtime.api.serve import _http_probe
from recipe_engine.live_adapters.live_governance import LiveGovernanceAdapter
from recipe_engine.live_adapters.settings import LiveAdapterSettings, LiveEndpoint

ENV_SOURCE_SHA = "FR_SOURCE_SHA"
ENV_GRANT_FILE = "FR_FOUNDER_GRANT_FILE"
ENV_GOVERNANCE_PORT = "FR_GOVERNANCE_PORT"
ENV_GOVERNANCE_SHA = "GOVERNANCE_EXPECTED_SHA"
DEFAULT_GOVERNANCE_PORT = 8848
GRANT_SCHEMA = "gov.grant.v1"

_HEX = set("0123456789abcdef")


def _is_sha(value: str) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(c in _HEX for c in value.lower())


def attested_source_sha(explicit: str | None = None) -> str:
    """The 40-hex git SHA this runtime attests, or "" when it cannot be known.

    Order: explicit argument, FR_SOURCE_SHA, ``git rev-parse HEAD`` of the
    repository this package is checked out from.  Never a short SHA, never a
    branch name, never a guess: Governance binds founder decisions to it.
    """
    for candidate in (explicit, os.environ.get(ENV_SOURCE_SHA, "")):
        if candidate and _is_sha(candidate.strip()):
            return candidate.strip().lower()
    root = Path(__file__).resolve().parents[2]
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    sha = (out.stdout or "").strip().lower()
    return sha if out.returncode == 0 and _is_sha(sha) else ""


def build_governance_port(port: int = DEFAULT_GOVERNANCE_PORT, *,
                          governance_source_sha: str = "") -> LiveGovernanceAdapter:
    settings = LiveAdapterSettings(
        endpoints=(LiveEndpoint("governance", "127.0.0.1", int(port), "loopback"),),
        governance_source_sha=governance_source_sha or os.environ.get(ENV_GOVERNANCE_SHA, ""),
    )
    return LiveGovernanceAdapter(settings)


def load_founder_grant(path: str | None) -> dict[str, Any] | None:
    """A founder-signed grant, read verbatim. Malformed or unsigned => None (no grant)."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != GRANT_SCHEMA:
        return None
    if not isinstance(data.get("signature"), str) or not data["signature"]:
        return None
    return data


def build_live_founder_api(db_path: str, *, governance_port: int | None = DEFAULT_GOVERNANCE_PORT,
                           source_sha: str | None = None, grant_file: str | None = None,
                           probe_live: bool = True):
    governance = build_governance_port(governance_port) if governance_port else None
    return create_app(
        db_path,
        probe_live=probe_live,
        prober=_http_probe,
        governance=governance,
        attested_source_sha=attested_source_sha(source_sha),
        founder_grant=load_founder_grant(grant_file or os.environ.get(ENV_GRANT_FILE, "")),
        auto_resolve=True,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="founder_runtime_api_live",
                                description="Neuruh Founder Runtime API with live Governance resolver (loopback only)")
    p.add_argument("--db", required=True, help="SQLite mission DB path (operator-chosen)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--governance-port", type=int,
                   default=int(os.environ.get(ENV_GOVERNANCE_PORT, DEFAULT_GOVERNANCE_PORT)))
    p.add_argument("--no-governance", action="store_true", help="serve without an authority port (missions park)")
    p.add_argument("--source-sha", default=None, help="40-hex git SHA to attest (default: FR_SOURCE_SHA or git HEAD)")
    p.add_argument("--founder-grant-file", default=None, help="founder-signed gov.grant.v1 JSON (default: FR_FOUNDER_GRANT_FILE)")
    p.add_argument("--no-probe-live", action="store_true", help="do not probe live organs")
    args = p.parse_args(argv)
    sha = attested_source_sha(args.source_sha)
    if not sha:
        print("[founder-runtime-api] WARNING: no attested source SHA; every mission will park SOURCE_SHA_UNATTESTED",
              file=sys.stderr)
    app = build_live_founder_api(
        args.db,
        governance_port=None if args.no_governance else args.governance_port,
        source_sha=sha or None,
        grant_file=args.founder_grant_file,
        probe_live=not args.no_probe_live,
    )
    import uvicorn
    uvicorn.run(app, host=HOST, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
