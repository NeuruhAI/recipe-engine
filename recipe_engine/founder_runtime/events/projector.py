"""Event-sourced mission projection rebuild.

The event log is the sole authority for the complete mission projection.
Stored body_json / projection_hash are caches that must match rebuild.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any, Sequence

from recipe_engine.founder_runtime.contracts.events import (
    EventType, MissionEvent, SAME_STATE_EVENT_TYPES, GENESIS_EVENT_HASH,
)
from recipe_engine.founder_runtime.contracts.mission import Mission, Deliverable, DeliverableStatus
from recipe_engine.founder_runtime.errors import PersistenceConflict, MissionSchemaInvalid, StateTransitionInvalid
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.state.states import MissionState, TERMINAL_STATES
from recipe_engine.founder_runtime.state.state_machine import StateMachine


TERMINAL_VALUES = {s.value for s in TERMINAL_STATES}


def _apply_deliverable_status(mission: Mission, event: MissionEvent) -> Mission:
    payload = event.payload or {}
    did = payload.get("deliverable_id")
    prior = payload.get("prior_status")
    nxt = payload.get("next_status")
    if not did or prior is None or nxt is None:
        raise MissionSchemaInvalid(
            "deliverable_status_changed payload incomplete",
            {"payload_keys": sorted(payload.keys())},
        )
    try:
        next_status = DeliverableStatus(nxt)
        prior_status = DeliverableStatus(prior)
    except ValueError as e:
        raise MissionSchemaInvalid("invalid deliverable status", {"error": str(e)}) from e
    found = False
    new_dels: list[Deliverable] = []
    for d in mission.deliverables:
        if d.deliverable_id == did:
            found = True
            if d.status != prior_status:
                raise PersistenceConflict(
                    "deliverable prior_status mismatch",
                    {"deliverable_id": did, "expected": prior, "actual": d.status.value},
                )
            new_dels.append(replace(d, status=next_status))
        else:
            new_dels.append(d)
    if not found:
        raise MissionSchemaInvalid("unknown deliverable_id", {"deliverable_id": did})
    mission.deliverables = new_dels
    return mission


def apply_event(mission: Mission | None, event: MissionEvent) -> Mission:
    """Pure application of one event onto a mission (or None before create)."""
    et = event.event_type

    if et == EventType.MISSION_CREATED:
        body = (event.payload or {}).get("mission_body")
        if not isinstance(body, dict):
            raise MissionSchemaInvalid("mission_created requires payload.mission_body", {})
        m = Mission.from_dict(deepcopy(body))
        # event application sets version relative to sequence later by caller
        m.current_state = event.next_state
        m.updated_at = event.timestamp
        return m

    if mission is None:
        raise PersistenceConflict("event applied without base mission", {"event_type": et.value})

    # Work on a shallow-copied mission shell via dict round-trip for isolation
    m = Mission.from_dict(mission.to_dict())

    if et == EventType.DELIVERABLE_STATUS_CHANGED:
        m = _apply_deliverable_status(m, event)
    elif et == EventType.PLAN_BOUND:
        plan_id = (event.payload or {}).get("plan_id")
        if not plan_id:
            raise MissionSchemaInvalid("plan_bound requires plan_id", {})
        m.plan_id = str(plan_id)
    elif et == EventType.FAILURE_RECORDED or et == EventType.PLAN_COMPILE_FAILED:
        # state moves via next_state; stash failure on extensions
        ext = dict(m.extensions or {})
        ext["failure"] = {
            "event_type": et.value,
            "reason": event.reason,
            "detail": (event.payload or {}).get("detail"),
            "error_class": (event.payload or {}).get("error_class"),
        }
        m.extensions = ext
    elif et == EventType.APPROVAL_RECORDED:
        ext = dict(m.extensions or {})
        recorded = list(ext.get("approval_ids_recorded") or [])
        aid = (event.payload or {}).get("approval_id")
        if aid and aid not in recorded:
            recorded.append(aid)
        ext["approval_ids_recorded"] = recorded
        m.extensions = ext
    elif et == EventType.APPROVAL_CONSUMED:
        ext = dict(m.extensions or {})
        consumed = list(ext.get("approval_ids_consumed") or [])
        aid = (event.payload or {}).get("approval_id")
        if aid and aid not in consumed:
            consumed.append(aid)
        ext["approval_ids_consumed"] = consumed
        m.extensions = ext
    elif et == EventType.AUTHORITY_CLASSIFIED:
        from recipe_engine.founder_runtime.contracts.authority import AuthorityRequirement
        requirements = (event.payload or {}).get("requirements") or []
        if not requirements:
            raise MissionSchemaInvalid("authority event missing requirements", {})
        m.authority_requirements = [AuthorityRequirement.from_dict(x) for x in requirements]
        ext = dict(m.extensions or {})
        ext["authority_policy_version"] = (event.payload or {}).get("policy_version")
        ext["authority_reason_code"] = (event.payload or {}).get("reason_code")
        m.extensions = ext
    elif et in {
        EventType.RECEIPT_RECORDED, EventType.CHECKPOINT_RECORDED,
        EventType.ARTIFACT_REGISTERED, EventType.OUTCOME_RECORDED,
        EventType.GOVERNANCE_RECORDED, EventType.SIDE_EFFECT_MARKED,
        EventType.SIDE_EFFECT_RECONCILED, EventType.PLAN_COMPILED,
    }:
        ext = dict(m.extensions or {})
        key = f"{et.value}_refs"
        refs = list(ext.get(key) or [])
        for ref in event.evidence_refs:
            if ref not in refs:
                refs.append(ref)
        ext[key] = refs
        m.extensions = ext
    elif et == EventType.COMPLETION_RECORDED:
        report = (event.payload or {}).get("completion_report") or {}
        if not report.get("completion_hash"):
            raise MissionSchemaInvalid("completion event report missing", {})
        ext = dict(m.extensions or {})
        ext["completion_hash"] = report["completion_hash"]
        ext["outcome_ref"] = report.get("outcome_ref")
        m.extensions = ext
    # other event types: state-only or audit-only

    m.current_state = event.next_state
    m.updated_at = event.timestamp
    if event.next_state.value in TERMINAL_VALUES:
        m.completed_at = event.timestamp
    return m


def rebuild_mission_from_events(events: Sequence[MissionEvent]) -> Mission:
    """Deterministically rebuild full mission projection from validated event stream."""
    if not events:
        raise PersistenceConflict("cannot rebuild from empty event stream", {})
    mission: Mission | None = None
    prior_state: MissionState | None = None
    version = 0
    previous_hash = GENESIS_EVENT_HASH
    for expected_position, ev in enumerate(events):
        stored_hash = ev.event_hash
        if ev.chain_position != expected_position:
            raise PersistenceConflict(
                "event chain order violation",
                {"event_id": ev.event_id, "expected": expected_position, "actual": ev.chain_position},
            )
        if ev.previous_event_hash != previous_hash:
            raise PersistenceConflict(
                "event chain predecessor violation", {"event_id": ev.event_id},
            )
        if not ev.verify_hash():
            raise PersistenceConflict("event hash tamper detected", {"event_id": ev.event_id, "stored": stored_hash})
        # state machine for real transitions; same-state body events skip table
        if prior_state is None:
            StateMachine.assert_transition(None, ev.next_state, event=ev, require_event=True)
        elif prior_state != ev.next_state:
            StateMachine.assert_transition(prior_state, ev.next_state, event=ev, require_event=True)
        else:
            # same-state: must be body/audit event type
            if ev.event_type not in SAME_STATE_EVENT_TYPES and ev.event_type != EventType.MISSION_CREATED:
                # allow MISSION_CREATED only as first
                if not (ev.event_type == EventType.MISSION_CREATED and prior_state is None):
                    from recipe_engine.founder_runtime.errors import StateTransitionInvalid
                    raise StateTransitionInvalid(
                        "same-state event type not allowed",
                        {"event_type": ev.event_type.value},
                    )
        mission = apply_event(mission, ev)
        version += 1
        mission.version = version
        prior_state = ev.next_state
        previous_hash = ev.event_hash
    if mission is None:
        raise PersistenceConflict("rebuild produced no mission", {})
    return mission


def projection_hash(mission: Mission) -> str:
    return hash_payload(mission.to_dict())
