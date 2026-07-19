"""CLI entry: python -m recipe_engine.founder_runtime.cli ..."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from recipe_engine.founder_runtime.cli.commands import (
    cmd_create, cmd_run, cmd_inspect, cmd_events, cmd_replay, cmd_recover, cmd_verify_receipt,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="founder_runtime", description="Neuruh Founder Runtime P1 CLI (fixture only)")
    p.add_argument("--db", default="", help="SQLite path (default: temp)")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("create", help="Create mission from fixture prompt")
    c.add_argument("--fixture", default="system-status")
    c.add_argument("--text", default="")
    c.add_argument("--idempotency-key", default="")

    r = sub.add_parser("run", help="Run mission (system-status fixture slice)")
    r.add_argument("--mission", default="")
    r.add_argument("--fixture", action="store_true", default=True)
    r.add_argument("--text", default="")

    i = sub.add_parser("inspect", help="Inspect mission")
    i.add_argument("--mission", required=True)

    e = sub.add_parser("events", help="List events")
    e.add_argument("--mission", required=True)

    rp = sub.add_parser("replay", help="Replay mission events")
    rp.add_argument("--mission", required=True)

    rc = sub.add_parser("recover", help="Recover mission")
    rc.add_argument("--mission", required=True)

    v = sub.add_parser("verify-receipt", help="Validate a receipt JSON file")
    v.add_argument("--file", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    db = args.db
    cleanup = None
    if not db:
        tmp = tempfile.NamedTemporaryFile(prefix="fr_p1_", suffix=".sqlite", delete=False)
        db = tmp.name
        tmp.close()
        cleanup = db

    try:
        handlers = {
            "create": cmd_create,
            "run": cmd_run,
            "inspect": cmd_inspect,
            "events": cmd_events,
            "replay": cmd_replay,
            "recover": cmd_recover,
            "verify-receipt": cmd_verify_receipt,
        }
        result = handlers[args.command](args, db_path=db)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e), "type": type(e).__name__}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
