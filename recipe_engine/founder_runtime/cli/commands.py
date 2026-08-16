"""CLI command implementations — the operator surface.

Previously fixture-locked: ``_rt()`` hardwired ``adapter_mode="fixture"``, so the
live read-only adapter layer built in P2-B was unreachable from the operator
surface, and ``run --mission`` re-ran the slice from scratch rather than resuming
from the last checkpoint. Both are wired here.

Live organs are built by a composition root outside ``founder_runtime`` and
injected through ``LIVE_COMPOSER``; this module never imports ``live_adapters``
at all — not even lazily — because the founder_runtime -> live_adapters
dependency is one-way and the AST confinement scan walks every import statement
in the package, function bodies included. Fixture mode never consults the hook,
and live mode without an injected composer fails closed rather than falling
back to fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.contracts.receipts import ExecutionReceipt
from recipe_engine.founder_runtime.evidence.validation import validate_receipt
from recipe_engine.founder_runtime.missions import (
    LONG_HORIZON_OBJECTIVE,
    LONG_HORIZON_STAGES,
    run_long_horizon_mission,
)
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.vertical_slices.system_status import (
    SYSTEM_STATUS_PROMPT,
    run_system_status_slice,
)

FIXTURE = "fixture"
LIVE = "live"


def _runtime_mode(mode: str) -> str:
    return "live_readonly" if mode == LIVE else "fixture"


def _rt(db_path: str, mode: str = FIXTURE) -> MissionRuntime:
    return MissionRuntime(db_path, clock=SteppingClock(), adapter_mode=_runtime_mode(mode))


# Composition hook. founder_runtime cannot construct live organs — the
# confinement scan forbids even a lazy import of live_adapters — so a
# composition root outside this package (recipe_engine.operator_cli) injects a
# zero-argument callable here that returns ``(organs, executor)``. Fixture mode
# never consults it.
LIVE_COMPOSER = None


class LiveComposerMissing(RuntimeError):
    """Live mode was requested but no composition root injected the organs."""


def _live_parts(mode: str):
    """The injected live organ set. Returns (organs, executor) or (None, None)."""
    if mode != LIVE:
        return None, None
    if LIVE_COMPOSER is None:
        raise LiveComposerMissing(
            "live mode requires a composition root: invoke the CLI via "
            "`python3 -m recipe_engine.operator_cli` (which injects the live "
            "organ set) — founder_runtime itself cannot construct live organs, "
            "and it never silently falls back to fixtures"
        )
    return LIVE_COMPOSER()


def cmd_create(args, *, db_path: str) -> dict[str, Any]:
    rt = _rt(db_path, getattr(args, "mode", FIXTURE))
    try:
        text = args.text or SYSTEM_STATUS_PROMPT
        key = args.idempotency_key or f"cli-create:{args.fixture}"
        m = rt.create_mission_from_text(text, creation_idempotency_key=key)
        return {"mission_id": m.mission_id, "state": m.current_state.value,
                "db": db_path, "title": m.title}
    finally:
        rt.close()


def cmd_run(args, *, db_path: str) -> dict[str, Any]:
    """Run the system-status vertical slice (fixture only, by its own contract)."""
    rt = _rt(db_path)
    try:
        text = args.text or SYSTEM_STATUS_PROMPT
        key = f"cli-run:{args.mission}" if args.mission else "cli-run:system-status"
        if args.mission:
            m = rt.get_mission(args.mission)
            if m.current_state.value == "COMPLETED":
                return {"mission_id": args.mission, "status": "already_completed",
                        "inspect": rt.inspect(args.mission)}
        result = run_system_status_slice(rt, request_text=text, creation_idempotency_key=key)
        result["db"] = db_path
        return result
    finally:
        rt.close()


def cmd_long_horizon(args, *, db_path: str) -> dict[str, Any]:
    """Run — or resume — the long-horizon read-only mission.

    Resume is not a separate command. Mission creation is idempotent on its key
    and a stage whose receipt exists is skipped, so re-invoking with the same
    ``--key`` continues exactly where the last run stopped and never re-executes
    completed work.
    """
    mode = args.mode
    organs, executor = _live_parts(mode)
    rt = _rt(db_path, mode)
    try:
        result = run_long_horizon_mission(
            rt,
            request_text=args.text or LONG_HORIZON_OBJECTIVE,
            mode=mode,
            organs=organs,
            executor=executor,
            creation_idempotency_key=args.key,
            stop_after_stage=args.stop_after or None,
        )
        result["db"] = db_path
        return result
    finally:
        rt.close()


def cmd_resume(args, *, db_path: str) -> dict[str, Any]:
    """Recover a mission, then continue it from the last verified boundary.

    ``recover`` alone produces a plan and stops — that is the ratified P2 gate
    and it is not bypassed here. This command reports that plan and then re-enters
    the mission, which resumes by skipping stages whose receipts already exist.
    """
    mode = args.mode
    organs, executor = _live_parts(mode)
    rt = _rt(db_path, mode)
    try:
        recovery = rt.recover(args.mission)
        out: dict[str, Any] = {"mission_id": args.mission, "recovery": recovery}
        if recovery["status"] not in ("safe_to_resume",):
            out["resumed"] = False
            out["reason"] = recovery.get("reason") or recovery["status"]
            return out
        mission = rt.get_mission(args.mission)
        if mission.current_state.value in ("COMPLETED", "COMPLETED_WITH_EXCEPTIONS"):
            out["resumed"] = False
            out["reason"] = "already_terminal"
            return out
        key = mission.creation_idempotency_key
        if not key:
            out["resumed"] = False
            out["reason"] = "mission has no creation idempotency key; cannot re-enter"
            return out
        result = run_long_horizon_mission(
            rt, mode=mode, organs=organs, executor=executor,
            creation_idempotency_key=key,
        )
        out["resumed"] = True
        out["result"] = result
        return out
    finally:
        rt.close()


def cmd_status(args, *, db_path: str) -> dict[str, Any]:
    """Operator view: where the mission is, what it decided, what is missing."""
    rt = _rt(db_path)
    try:
        mission = rt.get_mission(args.mission)
        receipts = rt.store.list_receipts(args.mission)
        done = [r.step_id for r in receipts]
        plans = rt.store.list_plans(args.mission)
        latest = rt.store.latest_plan(args.mission)
        packet = None
        if latest and latest.get("packet_id"):
            packet = rt.store.get_decision_packet(latest["packet_id"])
        return {
            "mission_id": mission.mission_id,
            "state": mission.current_state.value,
            "objective": mission.objective,
            "world": mission.world,
            "domain": mission.domain,
            "non_goals": list(mission.non_goals),
            "constraints": list(mission.constraints),
            "approval_gates": list(mission.approval_gates),
            "time_budget": mission.time_budget,
            "resource_budget": dict(mission.resource_budget),
            "completed_stages": sorted(done),
            "pending_stages": [s for s in LONG_HORIZON_STAGES if s not in done],
            "plan_lineage": [
                {"plan_version": p.get("plan_version"), "plan_id": p.get("plan_id"),
                 "strategy_id": p.get("strategy_id"), "supersedes": p.get("supersedes")}
                for p in plans
            ],
            "current_plan": (latest or {}).get("plan_id"),
            "selected_strategies": list((packet or {}).get("selected_strategy_ids") or []),
            "replanned": len(plans) > 1,
            "integrity": rt.store.verify_mission_chain(args.mission),
            "db": db_path,
        }
    finally:
        rt.close()


def cmd_inspect(args, *, db_path: str) -> dict[str, Any]:
    rt = _rt(db_path)
    try:
        return rt.inspect(args.mission)
    finally:
        rt.close()


def cmd_events(args, *, db_path: str) -> dict[str, Any]:
    rt = _rt(db_path)
    try:
        return {"mission_id": args.mission, "events": rt.events(args.mission)}
    finally:
        rt.close()


def cmd_replay(args, *, db_path: str) -> dict[str, Any]:
    rt = _rt(db_path)
    try:
        return rt.replay(args.mission)
    finally:
        rt.close()


def cmd_recover(args, *, db_path: str) -> dict[str, Any]:
    rt = _rt(db_path)
    try:
        return rt.recover(args.mission)
    finally:
        rt.close()


def cmd_reconcile(args, *, db_path: str) -> dict[str, Any]:
    """Reconcile interrupted side effects so recovery can trust the ledger again.

    A step that dies between proposing its side effect and recording its receipt
    leaves the ledger uncertain, and recovery rightly refuses to resume past it.
    Every step this runtime executes is a read-only observation, so the truthful
    reconciliation is to record that no external effect can exist and let the
    stage simply run again. Reconciliation never invents a receipt and never
    marks work done — a stage without a receipt remains not-done.
    """
    rt = _rt(db_path)
    try:
        receipts = {r.step_id for r in rt.store.list_receipts(args.mission)}
        reconciled: list[dict[str, Any]] = []
        unchanged: list[dict[str, Any]] = []
        for se in rt.store.list_side_effects(args.mission):
            if se["status"] not in ("proposed", "uncertain") or se["step_id"] in receipts:
                unchanged.append({"key": se["side_effect_key"], "status": se["status"]})
                continue
            rt.mark_side_effect(
                se["side_effect_key"], args.mission, se["step_id"], "reconciled",
                {
                    "resolution": "read_only_step_interrupted_before_receipt",
                    "external_effect": "none_possible_in_read_only_envelope",
                    "disposition": "stage_will_re_execute",
                },
            )
            reconciled.append({"key": se["side_effect_key"], "step_id": se["step_id"]})
        return {
            "mission_id": args.mission,
            "reconciled": reconciled,
            "unchanged": unchanged,
            "recovery": rt.recover(args.mission),
            "db": db_path,
        }
    finally:
        rt.close()


def cmd_missions(args, *, db_path: str) -> dict[str, Any]:
    """Every mission this store holds, newest first."""
    rt = _rt(db_path)
    try:
        rows = rt.store._c().execute(
            "SELECT mission_id, current_state, version, created_at, updated_at "
            "FROM missions ORDER BY created_at DESC"
        ).fetchall()
        return {"db": db_path, "count": len(rows), "missions": [dict(r) for r in rows]}
    finally:
        rt.close()


def cmd_verify_receipt(args, *, db_path: str) -> dict[str, Any]:
    data = json.loads(Path(args.file).read_text())
    receipt = ExecutionReceipt.from_dict(data)
    errs = validate_receipt(
        receipt,
        expected_mission_id=args.mission,
        expected_plan_id=args.plan,
        expected_step_id=args.step,
        expected_input_hash=args.input_hash,
        expected_output_hash=args.output_hash,
    )
    return {"valid": len(errs) == 0, "errors": errs, "receipt_id": receipt.receipt_id}
