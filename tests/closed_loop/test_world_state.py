"""The world does not change quietly, and unknowns stay named."""
from __future__ import annotations

import hashlib

import pytest

from recipe_engine.closed_loop.identity import EvidenceRef
from recipe_engine.closed_loop.world_state import (
    Belief, EpistemicStatus, WorldState, WorldStateError, apply_delta,
)


def evidence(name="ev-1") -> EvidenceRef:
    return EvidenceRef(name, "county_tax_roll", "county://x", "2026-06-01T00:00:00Z",
                       hashlib.sha256(name.encode()).hexdigest())


class TestEpistemicStatus:
    def test_known_requires_evidence(self):
        with pytest.raises(WorldStateError, match="inference alone cannot make"):
            Belief("owner", "x", EpistemicStatus.KNOWN, ())

    def test_inferred_requires_a_stated_basis(self):
        with pytest.raises(WorldStateError, match="inferred from"):
            Belief("motivated", True, EpistemicStatus.INFERRED, ())

    def test_inference_is_permitted_without_evidence_when_it_says_so(self):
        belief = Belief("motivated", True, EpistemicStatus.INFERRED, (),
                        "delinquency plus estate ownership")
        assert belief.status is EpistemicStatus.INFERRED

    def test_external_gate_must_name_who_closes_it(self):
        with pytest.raises(WorldStateError, match="who or what closes it"):
            Belief("administrator", None, EpistemicStatus.EXTERNAL_GATE, ())

    def test_an_unknown_may_not_carry_a_value(self):
        with pytest.raises(WorldStateError, match="may not carry a value"):
            Belief("phone", "555-0100", EpistemicStatus.UNKNOWN, ())


class TestDelta:
    def test_a_change_must_state_why(self):
        with pytest.raises(WorldStateError, match="must state why"):
            apply_delta(WorldState("s"), proposed=[], new_evidence=[],
                        recorded_at="2026-08-19T00:00:00Z", why="")

    def test_evidence_must_be_present_not_merely_cited(self):
        with pytest.raises(WorldStateError, match="not present in this delta"):
            apply_delta(WorldState("s"),
                        proposed=[Belief("owner", "x", EpistemicStatus.KNOWN, ("ev-absent",))],
                        new_evidence=[], recorded_at="2026-08-19T00:00:00Z",
                        why="claims evidence it does not carry")

    def test_a_delta_records_both_sides_of_every_change(self):
        state, _ = apply_delta(
            WorldState("s"),
            proposed=[Belief("owner", "estate", EpistemicStatus.KNOWN, ("ev-1",))],
            new_evidence=[evidence()], recorded_at="2026-08-19T00:00:00Z",
            why="probate index")
        state, delta = apply_delta(
            state,
            proposed=[Belief("owner", "individual", EpistemicStatus.KNOWN, ("ev-1",))],
            new_evidence=[evidence()], recorded_at="2026-08-20T00:00:00Z",
            why="deed correction")
        change = delta.changes[0]
        assert change.before is not None and change.before.value == "estate"
        assert change.after.value == "individual"
        assert change.why == "deed correction"

    def test_an_identical_proposal_is_a_noop_and_does_not_bump_the_version(self):
        state, _ = apply_delta(
            WorldState("s"),
            proposed=[Belief("owner", "estate", EpistemicStatus.KNOWN, ("ev-1",))],
            new_evidence=[evidence()], recorded_at="2026-08-19T00:00:00Z", why="read")
        again, delta = apply_delta(
            state,
            proposed=[Belief("owner", "estate", EpistemicStatus.KNOWN, ("ev-1",))],
            new_evidence=[evidence()], recorded_at="2026-08-20T00:00:00Z", why="re-read")
        assert delta.is_noop()
        assert again.version == state.version
        assert again.digest == state.digest

    def test_prior_support_counts_as_present_evidence(self):
        state, _ = apply_delta(
            WorldState("s"),
            proposed=[Belief("owner", "estate", EpistemicStatus.KNOWN, ("ev-1",))],
            new_evidence=[evidence()], recorded_at="2026-08-19T00:00:00Z", why="read")
        state, delta = apply_delta(
            state,
            proposed=[Belief("owner", "estate of record", EpistemicStatus.KNOWN, ("ev-1",))],
            new_evidence=[], recorded_at="2026-08-20T00:00:00Z", why="restated")
        assert len(delta.changes) == 1

    def test_unknowns_and_gates_are_carried_forward_by_name(self):
        state, delta = apply_delta(
            WorldState("s"),
            proposed=[
                Belief("owner", "estate", EpistemicStatus.KNOWN, ("ev-1",)),
                Belief("phone", None, EpistemicStatus.UNKNOWN, ()),
                Belief("administrator", None, EpistemicStatus.EXTERNAL_GATE, (),
                       "clerk of court"),
            ],
            new_evidence=[evidence()], recorded_at="2026-08-19T00:00:00Z", why="sweep")
        assert delta.remaining_unknown == ("phone",)
        assert delta.external_gates == ("administrator",)
        assert state.unknowns() == ("phone",)

    def test_digests_bind_the_move_from_one_state_to_the_next(self):
        start = WorldState("s")
        end, delta = apply_delta(
            start, proposed=[Belief("owner", "estate", EpistemicStatus.KNOWN, ("ev-1",))],
            new_evidence=[evidence()], recorded_at="2026-08-19T00:00:00Z", why="read")
        assert delta.prior_state_digest == start.digest
        assert delta.next_state_digest == end.digest
