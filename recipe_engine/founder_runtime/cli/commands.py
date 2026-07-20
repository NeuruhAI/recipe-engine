"""CLI command implementations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.vertical_slices.system_status import (
    run_system_status_slice, SYSTEM_STATUS_PROMPT,
)
from recipe_engine.founder_runtime.contracts.receipts import ExecutionReceipt
from recipe_engine.founder_runtime.evidence.validation import validate_receipt
from recipe_engine.founder_runtime.clock import SteppingClock


def _rt(db_path: str) -> MissionRuntime:
    return MissionRuntime(db_path, clock=SteppingClock(), adapter_mode="fixture")


def cmd_create(args, *, db_path: str) -> dict[str, Any]:
    rt = _rt(db_path)
    try:
        text = args.text or SYSTEM_STATUS_PROMPT
        if args.fixture == "system-status" and not args.text:
            text = SYSTEM_STATUS_PROMPT
        key = args.idempotency_key or f"cli-create:{args.fixture}"
        m = rt.create_mission_from_text(text, creation_idempotency_key=key)
        return {"mission_id": m.mission_id, "state": m.current_state.value, "db": db_path, "title": m.title}
    finally:
        rt.close()


def cmd_run(args, *, db_path: str) -> dict[str, Any]:
    rt = _rt(db_path)
    try:
        text = args.text or SYSTEM_STATUS_PROMPT
        key = f"cli-run:{args.mission}" if args.mission else "cli-run:system-status"
        if args.mission:
            # resume path: just recover + inspect if already exists
            m = rt.get_mission(args.mission)
            if m.current_state.value == "COMPLETED":
                return {"mission_id": args.mission, "status": "already_completed", "inspect": rt.inspect(args.mission)}
        result = run_system_status_slice(
            rt,
            request_text=text,
            creation_idempotency_key=key if not args.mission else f"cli-run:{args.mission}",
        )
        result["db"] = db_path
        return result
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
