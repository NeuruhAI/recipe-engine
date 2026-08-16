"""T1: the operator CLI surface.

The CLI is the operator's only surface, so what it must prove is operator-shaped:
that ``resume`` is TRUE resume (the same persisted mission — same id, same event
stream and hashes, same checkpoints, same receipts — never an equivalent
re-creation), that mode is explicit and never silently substituted, and that a
mission driven entirely through argv behaves exactly like one driven through the
library.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.cli import commands
from recipe_engine.founder_runtime.cli.commands import LiveComposerMissing
from recipe_engine.founder_runtime.cli.main import build_parser, main
from recipe_engine.founder_runtime.missions.long_horizon import LONG_HORIZON_STAGES
from recipe_engine.founder_runtime.runtime.mission_runtime import (
    RUNTIME_ADAPTER_MODES,
    MissionRuntime,
)
from recipe_engine.founder_runtime.errors import AdapterModeInvalid


def _tmp_db() -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    path = tmp.name
    tmp.close()
    return path


def _run(argv: list[str]) -> tuple[int, dict]:
    """Drive the real main() and parse the one JSON document it prints."""
    import contextlib
    import io

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    text = out.getvalue() if code == 0 else err.getvalue()
    return code, json.loads(text)


# ================================================= parser & modes
class ParserTests(unittest.TestCase):
    def test_every_operator_command_is_wired(self):
        parser = build_parser()
        actions = {a.dest: a for a in parser._subparsers._group_actions}
        sub = actions["command"]
        for cmd in ("create", "run", "long-horizon", "resume", "status",
                    "missions", "inspect", "events", "replay", "recover",
                    "verify-receipt"):
            self.assertIn(cmd, sub.choices, cmd)

    def test_mode_is_a_closed_vocabulary(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["long-horizon", "--mode", "pretend"])

    def test_runtime_mode_vocabulary_is_closed(self):
        self.assertEqual(RUNTIME_ADAPTER_MODES, frozenset({"fixture", "live_readonly"}))
        with self.assertRaises(AdapterModeInvalid):
            MissionRuntime(_tmp_db(), adapter_mode="live")  # the CLI word, not the runtime label

    def test_cli_mode_maps_to_a_truthful_runtime_label(self):
        self.assertEqual(commands._runtime_mode("live"), "live_readonly")
        self.assertEqual(commands._runtime_mode("fixture"), "fixture")


# ================================================= live is injected, never faked
class LiveCompositionTests(unittest.TestCase):
    def test_live_without_a_composition_root_fails_closed(self):
        """No composer, no live organs — and never a quiet fixture instead."""
        assert commands.LIVE_COMPOSER is None
        with self.assertRaises(LiveComposerMissing):
            commands._live_parts("live")

    def test_fixture_mode_never_consults_the_composer(self):
        def bomb():
            raise AssertionError("fixture mode consulted the live composer")

        commands.LIVE_COMPOSER = bomb
        try:
            self.assertEqual(commands._live_parts("fixture"), (None, None))
        finally:
            commands.LIVE_COMPOSER = None

    def test_live_mode_uses_exactly_what_the_composer_supplies(self):
        sentinel = (object(), object())
        commands.LIVE_COMPOSER = lambda: sentinel
        try:
            self.assertIs(commands._live_parts("live")[0], sentinel[0])
            self.assertIs(commands._live_parts("live")[1], sentinel[1])
        finally:
            commands.LIVE_COMPOSER = None

    def test_operator_cli_injects_the_composer_and_delegates(self):
        from recipe_engine import operator_cli

        db = _tmp_db()
        try:
            code = None
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                code = operator_cli.main(["--db", db, "missions"])
            self.assertEqual(code, 0)
            self.assertIsNotNone(commands.LIVE_COMPOSER)
        finally:
            commands.LIVE_COMPOSER = None


# ================================================= end-to-end through argv
class CliMissionTests(unittest.TestCase):
    def test_create_is_idempotent_on_its_key(self):
        db = _tmp_db()
        code1, r1 = _run(["--db", db, "create", "--idempotency-key", "cli-idem"])
        code2, r2 = _run(["--db", db, "create", "--idempotency-key", "cli-idem"])
        self.assertEqual((code1, code2), (0, 0))
        self.assertEqual(r1["mission_id"], r2["mission_id"])

    def test_status_of_a_missing_mission_fails_with_json(self):
        code, err = _run(["--db", _tmp_db(), "status", "--mission", "msn_missing"])
        self.assertEqual(code, 1)
        self.assertEqual(err["type"], "MissionNotFound")

    def test_suspend_then_resume_is_true_resume(self):
        """One mission identity across two processes-worth of invocations.

        Run 1 suspends after compile_plan.  ``resume`` must re-enter the SAME
        mission (same id, same DB record), execute only the remaining stages,
        preserve the event chain, and leave exactly one receipt per stage.
        """
        db = _tmp_db()
        code, r1 = _run(["--db", db, "long-horizon", "--key", "cli-lh",
                         "--stop-after", "compile_plan"])
        self.assertEqual(code, 0)
        self.assertEqual(r1["status"], "suspended")
        mission_id = r1["mission_id"]
        self.assertEqual(r1["executed_this_run"],
                         ["resolve_context", "read_system_state",
                          "generate_strategies", "compile_plan"])

        # The event chain as persisted at the suspension boundary.
        code, ev1 = _run(["--db", db, "events", "--mission", mission_id])
        self.assertEqual(code, 0)
        hashes_before = [e["event_hash"] for e in ev1["events"]]

        code, status = _run(["--db", db, "status", "--mission", mission_id])
        self.assertEqual(code, 0)
        self.assertEqual(status["completed_stages"],
                         sorted(r1["executed_this_run"]))
        self.assertTrue(status["integrity"]["ok"])

        code, r2 = _run(["--db", db, "resume", "--mission", mission_id])
        self.assertEqual(code, 0)
        self.assertTrue(r2["resumed"])
        self.assertEqual(r2["recovery"]["status"], "safe_to_resume")
        self.assertFalse(r2["recovery"].get("execution_resume_authorized", False))
        result = r2["result"]
        # Same mission, not an equivalent one.
        self.assertEqual(result["mission_id"], mission_id)
        self.assertEqual(result["status"], "completed")
        # Only the remaining stages ran.
        self.assertEqual(result["executed_this_run"],
                         [s for s in LONG_HORIZON_STAGES
                          if s not in r1["executed_this_run"]])

        # The pre-suspension chain is a strict prefix of the final chain.
        code, ev2 = _run(["--db", db, "events", "--mission", mission_id])
        hashes_after = [e["event_hash"] for e in ev2["events"]]
        self.assertEqual(hashes_after[:len(hashes_before)], hashes_before)

        # Exactly one receipt per stage — resume duplicated nothing.
        code, inspect = _run(["--db", db, "inspect", "--mission", mission_id])
        step_ids = [r["step_id"] for r in inspect["receipts"]]
        self.assertEqual(sorted(step_ids), sorted(set(step_ids)))
        self.assertEqual(sorted(set(step_ids)), sorted(LONG_HORIZON_STAGES))

        # And the store holds ONE mission, not a resumed copy.
        code, missions = _run(["--db", db, "missions"])
        self.assertEqual(code, 0)
        self.assertEqual(missions["count"], 1)
        self.assertEqual(missions["missions"][0]["mission_id"], mission_id)
        self.assertEqual(missions["missions"][0]["current_state"], "COMPLETED")

    def test_resuming_a_completed_mission_re_executes_nothing(self):
        db = _tmp_db()
        code, r1 = _run(["--db", db, "long-horizon", "--key", "cli-done"])
        self.assertEqual(code, 0)
        self.assertEqual(r1["status"], "completed")
        code, r2 = _run(["--db", db, "resume", "--mission", r1["mission_id"]])
        self.assertEqual(code, 0)
        self.assertFalse(r2["resumed"])
        self.assertEqual(r2["reason"], "already_terminal")

    def test_interrupted_side_effect_blocks_then_reconcile_unblocks(self):
        """A step that died between 'proposed' and its receipt parks resume.

        Recovery must refuse (uncertain_side_effect), ``reconcile`` must record
        the truthful read-only disposition, and resume must then complete the
        mission with the interrupted stage executed exactly once.
        """
        db = _tmp_db()
        code, r1 = _run(["--db", db, "long-horizon", "--key", "cli-interrupt",
                         "--stop-after", "compile_plan"])
        self.assertEqual(code, 0)
        mission_id = r1["mission_id"]

        # Reproduce the real crash window (observed live 2026-08-16): the stage
        # proposed its side effect AND recorded its boundary checkpoint, then
        # the executor died before any receipt existed. The runtime must be
        # re-enterable past BOTH previously-emitted boundary events.
        from recipe_engine.founder_runtime.adapters.fixture_axon import FixtureAxon
        from recipe_engine.founder_runtime.clock import SteppingClock
        from recipe_engine.founder_runtime.missions.long_horizon import (
            run_long_horizon_mission,
        )

        class _DiesMidStage(FixtureAxon):
            def execute_readonly_step(self, **kwargs):
                if kwargs.get("step_id") == "read_approved_knowledge":
                    raise RuntimeError("executor lost mid-stage (simulated crash)")
                return super().execute_readonly_step(**kwargs)

        rt = MissionRuntime(db, clock=SteppingClock(), adapter_mode="fixture")
        try:
            with self.assertRaises(RuntimeError):
                run_long_horizon_mission(
                    rt, creation_idempotency_key="cli-interrupt",
                    executor=_DiesMidStage(mode="fixture"),
                )
        finally:
            rt.close()

        code, blocked = _run(["--db", db, "resume", "--mission", mission_id])
        self.assertEqual(code, 0)
        self.assertFalse(blocked["resumed"])
        self.assertEqual(blocked["recovery"]["status"], "uncertain_side_effect")

        code, rec = _run(["--db", db, "reconcile", "--mission", mission_id])
        self.assertEqual(code, 0)
        self.assertEqual([r["step_id"] for r in rec["reconciled"]],
                         ["read_approved_knowledge"])
        self.assertEqual(rec["recovery"]["status"], "safe_to_resume")

        code, r2 = _run(["--db", db, "resume", "--mission", mission_id])
        self.assertEqual(code, 0)
        self.assertTrue(r2["resumed"])
        self.assertEqual(r2["result"]["status"], "completed")
        code, inspect = _run(["--db", db, "inspect", "--mission", mission_id])
        step_ids = [r["step_id"] for r in inspect["receipts"]]
        self.assertEqual(step_ids.count("read_approved_knowledge"), 1)

    def test_replay_agrees_after_a_cli_driven_mission(self):
        db = _tmp_db()
        code, r = _run(["--db", db, "long-horizon", "--key", "cli-replay"])
        self.assertEqual(code, 0)
        code, replay = _run(["--db", db, "replay", "--mission", r["mission_id"]])
        self.assertEqual(code, 0)
        self.assertEqual(replay["final_state"], "COMPLETED")
        self.assertEqual(replay["projection_hash"], r["replay"]["projection_hash"])


if __name__ == "__main__":
    unittest.main()
