"""Replay append-only events and validate full projection against event log."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.errors import PersistenceConflict, MissionNotFound
from recipe_engine.founder_runtime.events.projector import (
    rebuild_mission_from_events,
    projection_hash,
)
from recipe_engine.founder_runtime.serialization import to_canonical


def replay_mission(store, mission_id: str) -> dict[str, Any]:
    mission = store.get_mission(mission_id)
    if mission is None:
        raise MissionNotFound("mission not found for replay", {"mission_id": mission_id})
    events = store.list_events(mission_id)
    rebuilt = rebuild_mission_from_events(events)
    stored_body = mission.to_dict()
    rebuilt_body = rebuilt.to_dict()
    # Compare full projection (canonical hash)
    stored_hash = hash_payload(stored_body)
    rebuilt_hash = hash_payload(rebuilt_body)
    if rebuilt_hash != stored_hash:
        raise PersistenceConflict(
            "rebuilt projection diverges from stored body",
            {
                "mission_id": mission_id,
                "rebuilt_hash": rebuilt_hash,
                "stored_hash": stored_hash,
                "rebuilt_state": rebuilt.current_state.value,
                "stored_state": mission.current_state.value,
            },
        )
    if rebuilt.current_state != mission.current_state:
        raise PersistenceConflict(
            "replay final state != projection",
            {"replay": rebuilt.current_state.value, "projection": mission.current_state.value},
        )
    if rebuilt.version != mission.version:
        raise PersistenceConflict(
            "replay version != projection version",
            {"replay": rebuilt.version, "projection": mission.version},
        )
    return {
        "mission_id": mission_id,
        "event_count": len(events),
        "final_state": rebuilt.current_state.value,
        "mission_version": rebuilt.version,
        "projection_hash": rebuilt_hash,
        "mission": rebuilt_body,
        "events": [e.to_dict() for e in events],
        "rebuilt_matches_stored": True,
    }


def project_state_from_events(events):
    if not events:
        return None
    return rebuild_mission_from_events(events).current_state
