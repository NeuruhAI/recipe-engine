"""State machine — requires MissionEvent; model output cannot transition."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.errors import StateTransitionInvalid
from recipe_engine.founder_runtime.contracts.events import MissionEvent, EventType, SAME_STATE_EVENT_TYPES
from .states import MissionState, TERMINAL_STATES
from .transitions import is_transition_allowed


class StateMachine:
    """Pure transition validator. Persistence/runtime apply events transactionally."""

    @staticmethod
    def assert_transition(
        prior: MissionState | None,
        next_state: MissionState,
        *,
        event: MissionEvent | None = None,
        require_event: bool = True,
    ) -> None:
        if require_event and event is None:
            raise StateTransitionInvalid(
                "state change requires a valid MissionEvent",
                {"prior": None if prior is None else prior.value, "next": next_state.value},
            )
        if event is not None:
            if event.next_state != next_state:
                raise StateTransitionInvalid(
                    "event next_state mismatch",
                    {"event_next": event.next_state.value, "requested": next_state.value},
                )
            if prior is not None and event.prior_state is not None and event.prior_state != prior:
                raise StateTransitionInvalid(
                    "event prior_state mismatch",
                    {"event_prior": event.prior_state.value, "actual": prior.value},
                )
        # creation: prior None -> DRAFT or INTAKE via mission_created
        if prior is None:
            if next_state not in (MissionState.DRAFT, MissionState.INTAKE):
                raise StateTransitionInvalid(
                    "initial state must be DRAFT or INTAKE",
                    {"next": next_state.value},
                )
            return
        # same-state body/audit events
        if prior == next_state:
            if event is not None and event.event_type in SAME_STATE_EVENT_TYPES:
                return
            raise StateTransitionInvalid(
                "same-state transition requires allowed body/audit event type",
                {
                    "prior": prior.value,
                    "event_type": None if event is None else event.event_type.value,
                },
            )
        if prior in TERMINAL_STATES:
            raise StateTransitionInvalid(
                "terminal state cannot transition",
                {"prior": prior.value, "next": next_state.value},
            )
        if not is_transition_allowed(prior, next_state):
            raise StateTransitionInvalid(
                "transition not in explicit table",
                {"prior": prior.value, "next": next_state.value},
            )

    @staticmethod
    def is_terminal(state: MissionState) -> bool:
        return state in TERMINAL_STATES
