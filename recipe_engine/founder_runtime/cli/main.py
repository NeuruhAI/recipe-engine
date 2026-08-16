"""CLI entry: python -m recipe_engine.founder_runtime.cli ...

The operator surface for the Founder Runtime. Every command prints one JSON
document and exits 0 on success, 1 on failure — composable, no prompts.

Mode is explicit everywhere it matters: ``--mode fixture`` (default) or
``--mode live``. Live mode injects the live read-only organ set built by the
composition root; it never falls back to fixtures and it never widens
authority — Governance authorizes, AXON executes, IAR infers, this runtime
only plans, records and verifies.

Resume is TRUE resume: ``resume --mission <id>`` re-enters the same persisted
mission (same DB record, same event stream and hashes, same checkpoints and
receipts) through its creation idempotency key. It never mints an equivalent
mission and calls it resumed.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile

from recipe_engine.founder_runtime.cli.commands import (
    FIXTURE,
    LIVE,
    cmd_create,
    cmd_events,
    cmd_inspect,
    cmd_long_horizon,
    cmd_missions,
    cmd_reconcile,
    cmd_recover,
    cmd_replay,
    cmd_resume,
    cmd_run,
    cmd_status,
    cmd_verify_receipt,
)


def _add_mode(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--mode",
        choices=[FIXTURE, LIVE],
        default=FIXTURE,
        help="organ world: fixture (default) or live read-only; never silently substituted",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="founder_runtime",
        description="Neuruh Founder Runtime operator CLI (read-only authority envelope)",
    )
    p.add_argument("--db", default="", help="SQLite path (default: temp — pass one to persist/resume)")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("create", help="Create mission from text")
    c.add_argument("--fixture", default="system-status")
    c.add_argument("--text", default="")
    c.add_argument("--idempotency-key", default="")
    _add_mode(c)

    r = sub.add_parser("run", help="Run the system-status vertical slice (fixture by its own contract)")
    r.add_argument("--mission", default="")
    r.add_argument("--text", default="")

    lh = sub.add_parser("long-horizon", help="Run or resume the long-horizon read-only mission")
    lh.add_argument("--text", default="")
    lh.add_argument("--key", default="long-horizon", help="creation idempotency key — reuse it to resume")
    lh.add_argument("--stop-after", default="", help="suspend after this stage (for staged operation)")
    _add_mode(lh)

    rs = sub.add_parser("resume", help="Recover a mission, then continue it from its last verified boundary")
    rs.add_argument("--mission", required=True)
    _add_mode(rs)

    st = sub.add_parser("status", help="Operator view: state, stages, plan lineage, integrity")
    st.add_argument("--mission", required=True)

    sub.add_parser("missions", help="List every mission in the store, newest first")

    i = sub.add_parser("inspect", help="Inspect mission")
    i.add_argument("--mission", required=True)

    e = sub.add_parser("events", help="List events")
    e.add_argument("--mission", required=True)

    rp = sub.add_parser("replay", help="Replay mission events")
    rp.add_argument("--mission", required=True)

    rc = sub.add_parser("recover", help="Recover mission (report only; use resume to continue)")
    rc.add_argument("--mission", required=True)

    rn = sub.add_parser("reconcile", help="Reconcile interrupted side effects, then report recovery")
    rn.add_argument("--mission", required=True)

    v = sub.add_parser("verify-receipt", help="Validate a receipt JSON file")
    v.add_argument("--file", required=True)
    v.add_argument("--mission", required=True)
    v.add_argument("--plan", required=True)
    v.add_argument("--step", required=True)
    v.add_argument("--input-hash", required=True)
    v.add_argument("--output-hash", required=True)
    return p


HANDLERS = {
    "create": cmd_create,
    "run": cmd_run,
    "long-horizon": cmd_long_horizon,
    "resume": cmd_resume,
    "status": cmd_status,
    "missions": cmd_missions,
    "inspect": cmd_inspect,
    "events": cmd_events,
    "replay": cmd_replay,
    "recover": cmd_recover,
    "reconcile": cmd_reconcile,
    "verify-receipt": cmd_verify_receipt,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    db = args.db
    if not db:
        tmp = tempfile.NamedTemporaryFile(prefix="fr_cli_", suffix=".sqlite", delete=False)
        db = tmp.name
        tmp.close()

    try:
        result = HANDLERS[args.command](args, db_path=db)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e), "type": type(e).__name__}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
