"""The append-only, hash-chained ledger the whole loop is written to.

Same discipline as the 017 calibration ledger and the graduation ledger, for
the same reason: a learning system that can quietly rewrite its own history can
prove anything, and is therefore worth nothing.

Three properties, each tested:

* **Append-only + chained.** Every event carries its sequence and the hash of
  its predecessor. Any edit to any earlier event breaks every later hash.
* **Replay is a fixed point.** Re-running an identical pass appends nothing.
  Event ids are content-addressed, so "the same fact again" is recognised as
  the same fact — the ledger does not grow on every scan.
* **No forward references.** A causal predecessor must already be in the chain.
  You cannot claim to have been caused by something that has not happened.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from recipe_engine.closed_loop.events import (
    GENESIS_HASH, EventGrammarError, LoopEvent, LoopEventType,
)


class LedgerIntegrityError(ValueError):
    """The chain does not hold. Never recoverable by ignoring it."""


class LoopLedger:
    """In-memory chain with an optional JSONL backing file.

    The file is the durable copy; the in-memory list is the working one. They
    are written in the same order and verified against each other on load.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._events: list[LoopEvent] = []
        self._by_id: dict[str, LoopEvent] = {}
        if self.path is not None and self.path.exists():
            self._load()

    # -- reading ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterator[LoopEvent]:
        return iter(self._events)

    @property
    def events(self) -> tuple[LoopEvent, ...]:
        return tuple(self._events)

    @property
    def tip(self) -> str:
        return self._events[-1].event_hash if self._events else GENESIS_HASH

    def get(self, event_id: str) -> LoopEvent | None:
        return self._by_id.get(event_id)

    def of_type(self, *types: LoopEventType) -> tuple[LoopEvent, ...]:
        wanted = set(types)
        return tuple(e for e in self._events if e.event_type in wanted)

    # -- writing ------------------------------------------------------------

    def append(self, event: LoopEvent) -> tuple[LoopEvent, bool]:
        """Seal and append. Returns (event, appended).

        `appended=False` means this exact event is already in the chain — the
        fixed-point case. The stored event is returned so callers bind to the
        original, not to a duplicate that would never be written.
        """
        derived = event.derived_event_id()
        if event.event_id and event.event_id != derived:
            raise LedgerIntegrityError(
                "event_id does not match content — an event cannot be renamed")
        event.event_id = derived
        existing = self._by_id.get(derived)
        if existing is not None:
            return existing, False

        for claim in event.causal_predecessors:
            if claim.predecessor_event_id not in self._by_id:
                raise LedgerIntegrityError(
                    f"causal predecessor {claim.predecessor_event_id[:12]}… is not "
                    "in the chain — no forward references")

        event.seal(sequence=len(self._events), previous_event_hash=self.tip)
        self._events.append(event)
        self._by_id[event.event_id] = event
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), sort_keys=True,
                                        separators=(",", ":")) + "\n")
        return event, True

    def extend(self, events: Iterable[LoopEvent]) -> dict[str, int]:
        appended = retained = 0
        for event in events:
            _, was_new = self.append(event)
            appended += int(was_new)
            retained += int(not was_new)
        return {"appended": appended, "retained": retained}

    # -- integrity ----------------------------------------------------------

    def verify(self) -> bool:
        """Full chain verification. Raises with the first broken link."""
        verify_chain(self._events)
        return True

    def _load(self) -> None:
        assert self.path is not None
        loaded: list[LoopEvent] = []
        for line_no, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                # A torn final line is a truncated write, not a licence to
                # silently drop history — everything before it still verifies.
                if line_no == len(self.path.read_text(encoding="utf-8").splitlines()):
                    break
                raise LedgerIntegrityError(f"unreadable ledger line {line_no}")
            loaded.append(LoopEvent.from_dict(raw))
        verify_chain(loaded)
        self._events = loaded
        self._by_id = {e.event_id: e for e in loaded}


def verify_chain(events: Sequence[LoopEvent]) -> bool:
    """The independent check: sequence, linkage, hashes, and no forward refs."""
    previous = GENESIS_HASH
    seen: set[str] = set()
    for index, event in enumerate(events):
        if event.sequence != index:
            raise LedgerIntegrityError(
                f"sequence gap at {index}: event claims {event.sequence}")
        if event.previous_event_hash != previous:
            raise LedgerIntegrityError(f"broken link at sequence {index}")
        if event.event_id != event.derived_event_id():
            raise LedgerIntegrityError(f"event_id mismatch at sequence {index}")
        if not event.event_hash or event.event_hash != event.computed_hash():
            raise LedgerIntegrityError(f"event_hash mismatch at sequence {index}")
        if event.event_id in seen:
            raise LedgerIntegrityError(f"duplicate event_id at sequence {index}")
        for claim in event.causal_predecessors:
            if claim.predecessor_event_id not in seen:
                raise LedgerIntegrityError(
                    f"forward causal reference at sequence {index}")
        seen.add(event.event_id)
        previous = event.event_hash
    return True
