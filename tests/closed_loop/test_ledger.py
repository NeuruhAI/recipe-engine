"""Chain integrity, replay as a fixed point, and no forward references."""
from __future__ import annotations

import hashlib
import json

import pytest

from recipe_engine.closed_loop import identity as ident
from recipe_engine.closed_loop.events import (
    CausalClaim, CausalRelation, GENESIS_HASH, LoopEvent, LoopEventType,
)
from recipe_engine.closed_loop.identity import EventIdentity, EvidenceRef, WorldMode
from recipe_engine.closed_loop.ledger import (
    LedgerIntegrityError, LoopLedger, verify_chain,
)


def identity(**refs) -> EventIdentity:
    return EventIdentity(world=ident.world("w"), actor=ident.actor("a"),
                         mode=WorldMode.SYNTHETIC, **refs)


def evidence(name="ev") -> EvidenceRef:
    return EvidenceRef(name, "src", "ptr", "2026-01-01T00:00:00Z",
                       hashlib.sha256(name.encode()).hexdigest())


def observation(reason="sweep") -> LoopEvent:
    return LoopEvent(event_type=LoopEventType.SOURCE_OBSERVED,
                     identity=identity(run=ident.run("r")),
                     occurred_at="2026-08-19T00:00:00Z",
                     observed_at="2026-08-19T00:00:00Z", reason=reason,
                     evidence=(evidence(),))


class TestChain:
    def test_first_event_links_to_genesis(self):
        ledger = LoopLedger()
        event, appended = ledger.append(observation())
        assert appended and event.sequence == 0
        assert event.previous_event_hash == GENESIS_HASH

    def test_each_event_links_to_its_predecessor(self):
        ledger = LoopLedger()
        first, _ = ledger.append(observation("one"))
        second, _ = ledger.append(observation("two"))
        assert second.previous_event_hash == first.event_hash
        assert ledger.verify()

    def test_editing_an_earlier_event_breaks_the_chain(self):
        ledger = LoopLedger()
        ledger.append(observation("one"))
        ledger.append(observation("two"))
        events = list(ledger.events)
        events[0].reason = "tampered"
        with pytest.raises(LedgerIntegrityError):
            verify_chain(events)

    def test_tip_advances(self):
        ledger = LoopLedger()
        assert ledger.tip == GENESIS_HASH
        event, _ = ledger.append(observation())
        assert ledger.tip == event.event_hash


class TestFixedPoint:
    def test_appending_an_identical_event_appends_nothing(self):
        ledger = LoopLedger()
        first, appended_first = ledger.append(observation())
        second, appended_second = ledger.append(observation())
        assert appended_first is True and appended_second is False
        assert len(ledger) == 1
        assert second is first

    def test_a_full_replay_pass_is_a_fixed_point(self):
        ledger = LoopLedger()
        batch = [observation("one"), observation("two"), observation("three")]
        assert ledger.extend(batch) == {"appended": 3, "retained": 0}
        assert ledger.extend([observation("one"), observation("two"),
                              observation("three")]) == {"appended": 0, "retained": 3}
        assert len(ledger) == 3

    def test_a_renamed_event_is_refused(self):
        ledger = LoopLedger()
        event = observation()
        event.event_id = "0" * 64
        with pytest.raises(LedgerIntegrityError, match="cannot be renamed"):
            ledger.append(event)


class TestNoForwardReferences:
    def test_a_predecessor_must_already_be_in_the_chain(self):
        ledger = LoopLedger()
        orphan = LoopEvent(
            event_type=LoopEventType.SIGNAL_NORMALIZED,
            identity=identity(run=ident.run("r"), subject=ident.subject("s")),
            occurred_at="2026-08-19T00:00:00Z", observed_at="2026-08-19T00:00:00Z",
            reason="normalized", evidence=(evidence(),),
            causal_predecessors=(CausalClaim("f" * 64, CausalRelation.PRODUCED_BY,
                                             "claims a future cause"),))
        with pytest.raises(LedgerIntegrityError, match="no forward references"):
            ledger.append(orphan)

    def test_a_declared_predecessor_already_present_is_accepted(self):
        ledger = LoopLedger()
        first, _ = ledger.append(observation())
        second, appended = ledger.append(LoopEvent(
            event_type=LoopEventType.SIGNAL_NORMALIZED,
            identity=identity(run=ident.run("r"), subject=ident.subject("s")),
            occurred_at="2026-08-19T00:00:00Z", observed_at="2026-08-19T00:00:00Z",
            reason="normalized", evidence=(evidence(),),
            causal_predecessors=(CausalClaim(first.event_id, CausalRelation.PRODUCED_BY,
                                             "normalized from it"),)))
        assert appended and ledger.verify()


class TestDurability:
    def test_a_reloaded_ledger_verifies_and_stays_a_fixed_point(self, tmp_path):
        path = tmp_path / "loop.jsonl"
        first = LoopLedger(path)
        first.extend([observation("one"), observation("two")])

        reloaded = LoopLedger(path)
        assert len(reloaded) == 2
        assert reloaded.verify()
        assert reloaded.tip == first.tip
        assert reloaded.extend([observation("one")]) == {"appended": 0, "retained": 1}

    def test_a_torn_final_line_does_not_lose_earlier_history(self, tmp_path):
        path = tmp_path / "loop.jsonl"
        ledger = LoopLedger(path)
        ledger.extend([observation("one"), observation("two")])
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"schema_version": "neuruh.closed-loop.ev')

        recovered = LoopLedger(path)
        assert len(recovered) == 2
        assert recovered.verify()

    def test_on_disk_lines_are_canonical_json(self, tmp_path):
        path = tmp_path / "loop.jsonl"
        LoopLedger(path).append(observation())
        line = path.read_text(encoding="utf-8").strip()
        assert json.dumps(json.loads(line), sort_keys=True,
                          separators=(",", ":")) == line
