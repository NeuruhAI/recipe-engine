"""Event store façade over persistence."""

from __future__ import annotations

from recipe_engine.founder_runtime.contracts.events import MissionEvent
from recipe_engine.founder_runtime.contracts.mission import Mission


class EventStore:
    def __init__(self, store):
        self._store = store

    def append(self, event: MissionEvent, *, expected_version: int, mission: Mission) -> MissionEvent:
        return self._store.append_event(event, expected_version=expected_version, mission_body=mission)

    def list(self, mission_id: str) -> list[MissionEvent]:
        return self._store.list_events(mission_id)
