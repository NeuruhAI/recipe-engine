"""Replay append-only events and validate projection."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.errors import PersistenceConflict, MissionNotFound
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.state.state_machine import StateMachine


def replay_mission(store, mission_id: str) -> dict[str, Any]:
    mission = store.get_mission(mission_id)
    if mission is None:
        raise MissionNotFound("mission not found for replay", {"mission_id": mission_id})
    events = store.list_events(mission_id)
    prior = None
    for ev in events:
        # verify hash
        stored = ev.event_hash
        if not ev.verify_hash():
            raise PersistenceConflict("event hash tamper detected", {"event_id": ev.event_id})
        # restore hash after verify (verify_hash recomputes)
        if stored and stored != ev.event_hash:
            raise PersistenceConflict("event hash mismatch", {"event_id": ev.event_id})
        StateMachine.assert_transition(prior, ev.next_state, event=ev, require_event=True)
        prior = ev.next_state
    final = prior if prior is not None else mission.current_state
    if final != mission.current_state:
        raise PersistenceConflict(
            "replay final state != projection",
            {"replay": final.value, "projection": mission.current_state.value},
        )
    body = mission.to_dict()
    return {
        "mission_id": mission_id,
        "event_count": len(events),
        "final_state": final.value,
        "mission_version": mission.version,
        "projection_hash": hash_payload(body),
        "mission": body,
        "events": [e.to_dict() for e in events],
    }


def project_state_from_events(events) -> MissionState | None:
    state = None
    for ev in events:
        state = ev.next_state
    return state
