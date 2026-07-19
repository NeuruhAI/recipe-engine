"""State machine tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.state.states import MissionState, TERMINAL_STATES, ALL_STATES
from recipe_engine.founder_runtime.state.transitions import is_transition_allowed, all_valid_edges, TRANSITIONS
from recipe_engine.founder_runtime.state.state_machine import StateMachine
from recipe_engine.founder_runtime.errors import StateTransitionInvalid
from recipe_engine.founder_runtime.contracts.events import MissionEvent, EventType, SCHEMA_EVENT


def _ev(prior, nxt):
    return MissionEvent(
        schema_version=SCHEMA_EVENT,
        event_id="e1",
        mission_id="m1",
        prior_state=prior,
        next_state=nxt,
        event_type=EventType.STATE_TRANSITION,
        actor="t",
        reason="r",
        evidence_refs=[],
        timestamp="2026-07-19T00:00:00.000Z",
        idempotency_key="k",
        causation_id=None,
        correlation_id="m1",
    )


class StateMachineTests(unittest.TestCase):
    def test_all_states_present(self):
        expected = {
            "DRAFT","INTAKE","CONTEXT_RESOLUTION","PLANNING","AWAITING_DECISION","AWAITING_APPROVAL",
            "QUEUED","EXECUTING","VERIFYING","RECONCILING","COMPLETED","COMPLETED_WITH_EXCEPTIONS",
            "BLOCKED_INPUT","BLOCKED_DEPENDENCY","BLOCKED_POLICY","PAUSED","RECOVERING",
            "FAILED_RETRYABLE","FAILED_FINAL","CANCELLED","SUPERSEDED",
        }
        self.assertEqual({s.value for s in ALL_STATES}, expected)

    def test_terminal_set(self):
        self.assertEqual({s.value for s in TERMINAL_STATES}, {
            "COMPLETED","COMPLETED_WITH_EXCEPTIONS","FAILED_FINAL","CANCELLED","SUPERSEDED"
        })

    def test_every_valid_transition(self):
        for src, dst in all_valid_edges():
            self.assertTrue(is_transition_allowed(src, dst))
            StateMachine.assert_transition(src, dst, event=_ev(src, dst))

    def test_invalid_transition_rejected(self):
        with self.assertRaises(StateTransitionInvalid):
            StateMachine.assert_transition(MissionState.DRAFT, MissionState.COMPLETED, event=_ev(MissionState.DRAFT, MissionState.COMPLETED))

    def test_terminal_cannot_resume(self):
        for t in TERMINAL_STATES:
            with self.assertRaises(StateTransitionInvalid):
                StateMachine.assert_transition(t, MissionState.QUEUED, event=_ev(t, MissionState.QUEUED))

    def test_event_required(self):
        with self.assertRaises(StateTransitionInvalid):
            StateMachine.assert_transition(MissionState.DRAFT, MissionState.INTAKE, event=None, require_event=True)

    def test_wrong_prior_rejected(self):
        ev = _ev(MissionState.INTAKE, MissionState.CONTEXT_RESOLUTION)
        with self.assertRaises(StateTransitionInvalid):
            StateMachine.assert_transition(MissionState.DRAFT, MissionState.CONTEXT_RESOLUTION, event=ev)

    def test_happy_path_chain(self):
        chain = [
            MissionState.DRAFT, MissionState.INTAKE, MissionState.CONTEXT_RESOLUTION,
            MissionState.PLANNING, MissionState.QUEUED, MissionState.EXECUTING,
            MissionState.VERIFYING, MissionState.RECONCILING, MissionState.COMPLETED,
        ]
        for a, b in zip(chain, chain[1:]):
            StateMachine.assert_transition(a, b, event=_ev(a, b))

    def test_paused_resume(self):
        self.assertTrue(is_transition_allowed(MissionState.PAUSED, MissionState.EXECUTING))

    def test_blocked_input_recovery(self):
        self.assertTrue(is_transition_allowed(MissionState.BLOCKED_INPUT, MissionState.INTAKE))

    def test_blocked_dependency_recovery(self):
        self.assertTrue(is_transition_allowed(MissionState.BLOCKED_DEPENDENCY, MissionState.QUEUED))

    def test_failed_retryable_recovery(self):
        self.assertTrue(is_transition_allowed(MissionState.FAILED_RETRYABLE, MissionState.QUEUED))

    def test_cancelled(self):
        self.assertTrue(is_transition_allowed(MissionState.DRAFT, MissionState.CANCELLED))

    def test_superseded(self):
        self.assertTrue(is_transition_allowed(MissionState.DRAFT, MissionState.SUPERSEDED))


if __name__ == "__main__":
    unittest.main()
