#!/usr/bin/env python3
"""Run the long-horizon read-only mission against fixtures or the live organism.

Composition root.  It may read the environment and the sealed audits to build
live settings; the mission it launches never does — it receives an already-built
organ set and cannot reach ``live_adapters`` itself.

Usage:
    python3 scripts/run_long_horizon_mission.py [fixture|live] [db_path] [stop_after_stage]

No mutation, no production persistence, no outbound communication.  A live run
observes Mother, GitHub, Vault and Qdrant read-only; a gated or unavailable
organ is disclosed as an evidence gap and never substituted with a fixture.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.missions import run_long_horizon_mission
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime


def build_live_settings():
    # Canonical topology construction moved beside the organ builders it feeds;
    # this name stays for callers that already know it.
    from recipe_engine.live_adapters.composition import build_default_live_settings

    try:
        return build_default_live_settings()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "fixture"
    db = sys.argv[2] if len(sys.argv) > 2 else ""
    stop_after = sys.argv[3] if len(sys.argv) > 3 else None
    if not db:
        tmp = tempfile.NamedTemporaryFile(prefix="fr_long_horizon_", suffix=".sqlite", delete=False)
        db = tmp.name
        tmp.close()

    organs = executor = None
    if mode == "live":
        from recipe_engine.live_adapters.composition import build_live_executor, build_live_organs

        settings = build_live_settings()
        organs = build_live_organs(settings)
        executor = build_live_executor(settings)

    # The runtime's adapter mode is a truthful label of where its organs came
    # from — a live run must not self-describe as fixture.
    runtime = MissionRuntime(
        db, clock=SteppingClock(),
        adapter_mode="live_readonly" if mode == "live" else "fixture",
    )
    try:
        result = run_long_horizon_mission(
            runtime,
            mode=mode,
            organs=organs,
            executor=executor,
            creation_idempotency_key=f"long-horizon:{mode}",
            stop_after_stage=stop_after,
        )
    finally:
        runtime.close()
    result["db_path"] = db
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
