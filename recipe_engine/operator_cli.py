"""Operator CLI entry with live composition: python -m recipe_engine.operator_cli ...

The composition root for the operator surface. ``founder_runtime`` is a
confined package — its reachable graph must not contain live_adapters (sockets,
subprocess, filesystem I/O), so it cannot construct live organs itself. This
module lives outside that graph: it builds the live read-only organ set from
the sealed GREEN audits and injects it into the CLI's ``LIVE_COMPOSER`` hook,
then delegates to the ordinary CLI main.

Same commands, same JSON output. ``--mode live`` works here; through
``python -m recipe_engine.founder_runtime.cli`` it fails closed instead —
never a silent fixture substitution.

The organs injected are read-only by construction (mutation is a hard stop in
every configuration). Authority is unchanged: Governance authorizes, AXON
executes, IAR infers; this surface plans, records and verifies.
"""

from __future__ import annotations

import sys

from recipe_engine.founder_runtime.cli import commands
from recipe_engine.founder_runtime.cli.main import main as _cli_main


def _compose_live():
    from recipe_engine.live_adapters.composition import (
        build_default_live_settings,
        build_live_executor,
        build_live_organs,
    )

    settings = build_default_live_settings()
    return build_live_organs(settings), build_live_executor(settings)


def main(argv: list[str] | None = None) -> int:
    commands.LIVE_COMPOSER = _compose_live
    return _cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
