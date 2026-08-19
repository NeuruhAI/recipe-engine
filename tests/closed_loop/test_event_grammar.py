"""The grammar's refusals are the contract. Each one is asserted here."""
from __future__ import annotations

import hashlib

import pytest

from recipe_engine.closed_loop import identity as ident
from recipe_engine.closed_loop.events import (
    CausalClaim, CausalRelation, EVIDENCE_REQUIRED, EventGrammarError,
    FORBIDDEN_PAYLOAD_KEYS, LoopEvent, LoopEventType, REQUIRED_REFS,
    grammar_digest, grammar_manifest,
)
from recipe_engine.closed_loop.identity import (
    EventIdentity, EvidenceRef, IdentityError, RefKind, WorldMode,
)


def evidence(name: str = "ev-1") -> EvidenceRef:
    return EvidenceRef(name, "county_tax_roll", "county://x", "2026-06-01T00:00:00Z",
                       hashlib.sha256(name.encode()).hexdigest())


def base_identity(**refs) -> EventIdentity:
    return EventIdentity(world=ident.world("w"), actor=ident.actor("a"),
                         mode=WorldMode.SYNTHETIC, **refs)


class TestIdentityContract:
    def test_world_and_actor_are_always_required(self):
        with pytest.raises(TypeError):
            EventIdentity(world=ident.world("w"), mode=WorldMode.LIVE)  # type: ignore[call-arg]

    def test_ref_kinds_are_enforced(self):
        with pytest.raises(IdentityError):
            EventIdentity(world=ident.subject("s"), actor=ident.actor("a"),
                          mode=WorldMode.LIVE)

    def test_empty_ref_id_is_rejected(self):
        with pytest.raises(IdentityError):
            ident.subject("   ")

    def test_evidence_digest_must_be_sha256_hex(self):
        with pytest.raises(IdentityError):
            EvidenceRef("e", "s", "p", "2026-01-01T00:00:00Z", "not-a-digest")

    def test_mode_survives_round_trip(self):
        identity = base_identity(run=ident.run("r"))
        assert EventIdentity.from_dict(identity.to_dict()).mode is WorldMode.SYNTHETIC


class TestRequiredBindings:
    def test_every_event_type_has_a_declared_binding_rule(self):
        assert set(REQUIRED_REFS) == set(LoopEventType)

    def test_decision_approved_without_a_decision_ref_is_rejected(self):
        with pytest.raises(EventGrammarError, match="requires identity refs"):
            LoopEvent(event_type=LoopEventType.DECISION_APPROVED,
                      identity=base_identity(run=ident.run("r"), subject=ident.subject("s")),
                      occurred_at="2026-08-19T00:00:00Z",
                      observed_at="2026-08-19T00:00:00Z", reason="approved")

    def test_action_executed_requires_the_full_chain_of_refs(self):
        with pytest.raises(EventGrammarError, match="action"):
            LoopEvent(event_type=LoopEventType.ACTION_EXECUTED,
                      identity=base_identity(run=ident.run("r"), subject=ident.subject("s"),
                                             decision=ident.decision("d")),
                      occurred_at="2026-08-19T00:00:00Z",
                      observed_at="2026-08-19T00:00:00Z", reason="executed")

    @pytest.mark.parametrize("event_type", sorted(EVIDENCE_REQUIRED, key=lambda t: t.value))
    def test_world_asserting_events_require_evidence(self, event_type):
        refs = {k.value: {"run": ident.run("r"), "subject": ident.subject("s")}[k.value]
                for k in REQUIRED_REFS[event_type]}
        with pytest.raises(EventGrammarError, match="requires at least one evidence"):
            LoopEvent(event_type=event_type, identity=base_identity(**refs),
                      occurred_at="2026-08-19T00:00:00Z",
                      observed_at="2026-08-19T00:00:00Z", reason="asserting")

    def test_decisions_are_exempt_from_evidence_because_they_are_our_acts(self):
        event = LoopEvent(
            event_type=LoopEventType.DECISION_PROPOSED,
            identity=base_identity(run=ident.run("r"), subject=ident.subject("s"),
                                   decision=ident.decision("d")),
            occurred_at="2026-08-19T00:00:00Z", observed_at="2026-08-19T00:00:00Z",
            reason="proposed")
        assert event.evidence == ()


class TestTemporalHonesty:
    def test_observed_at_may_not_precede_occurred_at(self):
        with pytest.raises(EventGrammarError, match="before it happens"):
            LoopEvent(event_type=LoopEventType.CALIBRATION_RECORDED,
                      identity=base_identity(),
                      occurred_at="2026-08-19T00:00:00Z",
                      observed_at="2026-08-18T00:00:00Z", reason="backdated")

    def test_learning_of_a_thing_later_is_normal(self):
        event = LoopEvent(event_type=LoopEventType.CALIBRATION_RECORDED,
                          identity=base_identity(), occurred_at="2026-08-01T00:00:00Z",
                          observed_at="2026-08-19T00:00:00Z", reason="late arrival")
        assert event.observed_at > event.occurred_at


class TestNoHiddenReasoning:
    @pytest.mark.parametrize("key", sorted(FORBIDDEN_PAYLOAD_KEYS))
    def test_forbidden_payload_keys_are_refused(self, key):
        with pytest.raises(EventGrammarError, match="un-declared model reasoning"):
            LoopEvent(event_type=LoopEventType.CALIBRATION_RECORDED,
                      identity=base_identity(), occurred_at="2026-08-19T00:00:00Z",
                      observed_at="2026-08-19T00:00:00Z", reason="r",
                      payload={key: "…"})

    def test_nested_forbidden_keys_are_refused(self):
        with pytest.raises(EventGrammarError):
            LoopEvent(event_type=LoopEventType.CALIBRATION_RECORDED,
                      identity=base_identity(), occurred_at="2026-08-19T00:00:00Z",
                      observed_at="2026-08-19T00:00:00Z", reason="r",
                      payload={"outer": {"inner": [{"chain_of_thought": "…"}]}})


class TestCausalClaims:
    def test_a_causal_claim_must_state_its_basis(self):
        with pytest.raises(EventGrammarError, match="no silent links"):
            CausalClaim("evt", CausalRelation.PRODUCED_BY, "  ")

    def test_suspected_is_a_legal_and_distinct_relation(self):
        claim = CausalClaim("evt", CausalRelation.SUSPECTED, "operator hunch, unverified")
        assert claim.relation is not CausalRelation.PRODUCED_BY

    def test_duplicate_claims_are_refused(self):
        claim = CausalClaim("evt", CausalRelation.PRODUCED_BY, "b")
        with pytest.raises(EventGrammarError, match="duplicate causal claim"):
            LoopEvent(event_type=LoopEventType.CALIBRATION_RECORDED,
                      identity=base_identity(), occurred_at="2026-08-19T00:00:00Z",
                      observed_at="2026-08-19T00:00:00Z", reason="r",
                      causal_predecessors=(claim, claim))


class TestContentAddressing:
    def test_identical_content_yields_the_same_event_id(self):
        def build():
            return LoopEvent(event_type=LoopEventType.SOURCE_OBSERVED,
                             identity=base_identity(run=ident.run("r")),
                             occurred_at="2026-08-19T00:00:00Z",
                             observed_at="2026-08-19T00:00:00Z", reason="sweep",
                             evidence=(evidence(),))
        assert build().event_id == build().event_id

    def test_a_changed_field_changes_the_event_id(self):
        first = LoopEvent(event_type=LoopEventType.SOURCE_OBSERVED,
                          identity=base_identity(run=ident.run("r")),
                          occurred_at="2026-08-19T00:00:00Z",
                          observed_at="2026-08-19T00:00:00Z", reason="sweep",
                          evidence=(evidence(),))
        second = LoopEvent(event_type=LoopEventType.SOURCE_OBSERVED,
                           identity=base_identity(run=ident.run("r")),
                           occurred_at="2026-08-19T00:00:00Z",
                           observed_at="2026-08-19T00:00:00Z", reason="different",
                           evidence=(evidence(),))
        assert first.event_id != second.event_id

    def test_mode_is_part_of_identity_so_a_fixture_is_not_a_live_fact(self):
        def build(mode):
            return LoopEvent(
                event_type=LoopEventType.SOURCE_OBSERVED,
                identity=EventIdentity(world=ident.world("w"), actor=ident.actor("a"),
                                       mode=mode, run=ident.run("r")),
                occurred_at="2026-08-19T00:00:00Z", observed_at="2026-08-19T00:00:00Z",
                reason="sweep", evidence=(evidence(),))
        assert build(WorldMode.LIVE).event_id != build(WorldMode.SYNTHETIC).event_id


class TestGrammarManifest:
    def test_manifest_covers_every_event_type(self):
        manifest = grammar_manifest()
        assert set(manifest["event_types"]) == {t.value for t in LoopEventType}
        assert set(manifest["required_refs"]) == {t.value for t in LoopEventType}

    def test_manifest_digest_is_stable_across_calls(self):
        assert grammar_digest() == grammar_digest()
