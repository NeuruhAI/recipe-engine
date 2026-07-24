"""Operator CLI for the Founder Runtime API. Never prints the operator token.

Token resolution order: --token-env NAME (reads that env var) -> FR_API_OPERATOR_TOKEN.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from recipe_engine.founder_runtime.api.client import ApiClientError, FounderRuntimeClient


def _resolve_token(args) -> str:
    if args.token_env:
        tok = os.environ.get(args.token_env, "").strip()
        if tok:
            return tok
        print(f"error: env var {args.token_env} is not set", file=sys.stderr)
        raise SystemExit(2)
    tok = os.environ.get("FR_API_OPERATOR_TOKEN", "").strip()
    if not tok:
        print("error: operator token not found. Set FR_API_OPERATOR_TOKEN or pass --token-env NAME",
              file=sys.stderr)
        raise SystemExit(2)
    return tok


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fr-api", description="Founder Runtime API operator CLI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=19090)
    p.add_argument("--token-env", default="", help="name of env var holding the operator token")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("create-mission", help="Create a mission and print its decision packet")
    c.add_argument("--objective", required=True)
    c.add_argument("--title", default="")
    c.add_argument("--done-criteria", default="")

    sub.add_parser("list-missions", help="List missions")

    g = sub.add_parser("get-mission", help="Get one mission")
    g.add_argument("--mission", required=True)

    f = sub.add_parser("follow-events", help="Stream mission events (read-only)")
    f.add_argument("--mission", required=True)

    a = sub.add_parser("approve", help="Approve the proposed plan (authority only; does not execute)")
    a.add_argument("--mission", required=True)
    a.add_argument("--reason", default="")

    r = sub.add_parser("reject", help="Reject the proposed plan with a reason")
    r.add_argument("--mission", required=True)
    r.add_argument("--reason", required=True)

    pa = sub.add_parser("pause", help="Pause a mission safely")
    pa.add_argument("--mission", required=True)

    re_ = sub.add_parser("resume", help="Resume a paused mission")
    re_.add_argument("--mission", required=True)

    ca = sub.add_parser("cancel", help="Cancel a mission safely")
    ca.add_argument("--mission", required=True)

    rc = sub.add_parser("recover", help="Perform deterministic recovery for a mission")
    rc.add_argument("--mission", required=True)

    sub.add_parser("adapters", help="Show organ/adapter status matrix")
    return p


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    token = _resolve_token(args)
    client = FounderRuntimeClient(token, host=args.host, port=args.port)
    try:
        if args.command == "create-mission":
            result = client.create_mission(args.objective, title=args.title, done_criteria=args.done_criteria)
            out = {
                "envelope": result["envelope"],
                "decision_packet": result.get("decision_packet"),
                "proposed_plan": result.get("proposed_plan"),
            }
            _print(out)
        elif args.command == "list-missions":
            _print(client.list_missions())
        elif args.command == "get-mission":
            _print(client.get_mission(args.mission))
        elif args.command == "follow-events":
            for ev in client.stream_events(args.mission):
                _print(ev)
        elif args.command == "approve":
            _print(client.approve(args.mission, reason=args.reason))
        elif args.command == "reject":
            _print(client.reject(args.mission, reason=args.reason))
        elif args.command == "pause":
            _print(client.pause(args.mission))
        elif args.command == "resume":
            _print(client.resume(args.mission))
        elif args.command == "cancel":
            _print(client.cancel(args.mission))
        elif args.command == "recover":
            _print(client.recover(args.mission))
        elif args.command == "adapters":
            _print(client.adapters_status())
        return 0
    except ApiClientError as e:
        print(json.dumps({"error": e.payload, "status": e.status}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
