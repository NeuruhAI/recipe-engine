"""Serve the Founder Runtime API over loopback. CLI: python -m ...api.serve"""

from __future__ import annotations

import argparse
import sys

from recipe_engine.founder_runtime.api.adapters import LIVE_READ_ONLY, UNAVAILABLE
from recipe_engine.founder_runtime.api.app import DEFAULT_PORT, HOST, create_app


def _http_probe(service_id: str, host: str, port: int) -> dict:
    """Loopback-only health read, injected into the kernel boundary."""
    import json
    import urllib.request
    url = f"http://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8") or "{}")
                return {"service": service_id, "classification": LIVE_READ_ONLY,
                        "reachable": True,
                        "detail": body if isinstance(body, dict) else {"raw": str(body)[:200]}}
            return {"service": service_id, "classification": UNAVAILABLE,
                    "reachable": False, "detail": {"http_status": resp.status}}
    except Exception as e:  # noqa: BLE001
        return {"service": service_id, "classification": UNAVAILABLE,
                "reachable": False, "detail": {"error": type(e).__name__}}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="founder_runtime_api", description="Neuruh Founder Runtime API (loopback only)")
    p.add_argument("--db", required=True, help="SQLite mission DB path (operator-chosen)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--no-probe-live", action="store_true", help="do not probe live organs")
    args = p.parse_args(argv)
    app = create_app(args.db, probe_live=not args.no_probe_live, prober=_http_probe)
    import uvicorn
    uvicorn.run(app, host=HOST, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
