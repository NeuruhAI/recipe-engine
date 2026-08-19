"""World state, and the rule that it cannot change quietly.

The question this module answers is not "what is true?" but "what did we
believe, what arrived, what changed, why, and what is still unknown?" — because
a system that only stores its current beliefs cannot tell the difference
between learning something and talking itself into something.

The load-bearing rule: **a belief may only reach KNOWN on evidence.** Model
inference can produce INFERRED and nothing stronger. Promotion from INFERRED to
KNOWN requires evidence references, and the delta that did it is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.identity import EvidenceRef, canonical_json, digest


SCHEMA_BELIEF = "neuruh.closed-loop.belief.v1"
SCHEMA_DELTA = "neuruh.closed-loop.world-state-delta.v1"


class EpistemicStatus(str, Enum):
    """How a belief is held — never how strongly someone feels about it.

    KNOWN         — supported by evidence we hold and can re-read.
    INFERRED      — derived from what we hold; defensible, not observed.
    UNKNOWN       — named as missing. An explicit hole, not an absence.
    EXTERNAL_GATE — knowable only by something outside the system (an operator
                    decision, a county clerk, a court date). Not our unknown to
                    close by thinking harder.
    """
    KNOWN = "known"
    INFERRED = "inferred"
    UNKNOWN = "unknown"
    EXTERNAL_GATE = "external_gate"


#: Statuses that require at least one evidence reference to hold.
EVIDENCE_BACKED: frozenset[EpistemicStatus] = frozenset({EpistemicStatus.KNOWN})


class WorldStateError(ValueError):
    """An attempted change the world-state contract does not permit."""


@dataclass(frozen=True)
class Belief:
    """One fact about one subject, with how it is held and what supports it."""
    fact_key: str
    value: Any
    status: EpistemicStatus
    evidence_ids: tuple[str, ...] = ()
    basis: str = ""

    def __post_init__(self) -> None:
        if not str(self.fact_key).strip():
            raise WorldStateError("belief.fact_key must be non-empty")
        if not isinstance(self.status, EpistemicStatus):
            raise WorldStateError("belief.status must be an EpistemicStatus")
        if self.status in EVIDENCE_BACKED and not self.evidence_ids:
            raise WorldStateError(
                f"{self.fact_key}: {self.status.value} requires evidence — "
                "inference alone cannot make a thing known")
        if self.status is EpistemicStatus.INFERRED and not str(self.basis).strip():
            raise WorldStateError(
                f"{self.fact_key}: an inference must state what it was inferred from")
        if self.status is EpistemicStatus.EXTERNAL_GATE and not str(self.basis).strip():
            raise WorldStateError(
                f"{self.fact_key}: an external gate must name who or what closes it")
        if self.status is EpistemicStatus.UNKNOWN and self.value is not None:
            raise WorldStateError(
                f"{self.fact_key}: an unknown may not carry a value")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_BELIEF, "fact_key": self.fact_key,
                "value": self.value, "status": self.status.value,
                "evidence_ids": list(self.evidence_ids), "basis": self.basis}

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Belief":
        return Belief(str(d["fact_key"]), d.get("value"), EpistemicStatus(d["status"]),
                      tuple(d.get("evidence_ids") or ()), str(d.get("basis") or ""))


@dataclass(frozen=True)
class WorldState:
    """What we believe about one subject, right now, and nothing more."""
    subject_id: str
    beliefs: Mapping[str, Belief] = field(default_factory=dict)
    version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"subject_id": self.subject_id, "version": self.version,
                "beliefs": {k: self.beliefs[k].to_dict() for k in sorted(self.beliefs)}}

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    def unknowns(self) -> tuple[str, ...]:
        return tuple(sorted(k for k, b in self.beliefs.items()
                            if b.status is EpistemicStatus.UNKNOWN))

    def external_gates(self) -> tuple[str, ...]:
        return tuple(sorted(k for k, b in self.beliefs.items()
                            if b.status is EpistemicStatus.EXTERNAL_GATE))


@dataclass(frozen=True)
class BeliefChange:
    """One belief moving. Both sides recorded, so the move can be argued with."""
    fact_key: str
    before: Belief | None
    after: Belief
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {"fact_key": self.fact_key,
                "before": None if self.before is None else self.before.to_dict(),
                "after": self.after.to_dict(), "why": self.why}


@dataclass(frozen=True)
class WorldStateDelta:
    """What did we believe, what arrived, what changed, why, what is still open.

    This is the artifact the loop reasons over — not the state itself. A state
    with no delta behind it is a claim; a state reached through recorded deltas
    is a position that can be replayed and disputed.
    """
    subject_id: str
    prior_state_digest: str
    next_state_digest: str
    new_evidence: tuple[EvidenceRef, ...]
    changes: tuple[BeliefChange, ...]
    remaining_unknown: tuple[str, ...]
    external_gates: tuple[str, ...]
    recorded_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_DELTA,
            "subject_id": self.subject_id,
            "prior_state_digest": self.prior_state_digest,
            "next_state_digest": self.next_state_digest,
            "new_evidence": [e.to_dict() for e in self.new_evidence],
            "changes": [c.to_dict() for c in self.changes],
            "remaining_unknown": list(self.remaining_unknown),
            "external_gates": list(self.external_gates),
            "recorded_at": self.recorded_at,
        }

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    def is_noop(self) -> bool:
        return not self.changes


def apply_delta(
    state: WorldState,
    *,
    proposed: Sequence[Belief],
    new_evidence: Sequence[EvidenceRef],
    recorded_at: str,
    why: str,
) -> tuple[WorldState, WorldStateDelta]:
    """Move the world state, or refuse and say why.

    Refusals, all of them deliberate:

    * A belief claiming KNOWN whose evidence is not in `new_evidence` and was
      not already supporting it is rejected. Evidence must be *present*, not
      merely cited.
    * Downgrading a KNOWN belief is permitted (the world changes, and sources
      get retracted) but is recorded as a change like any other.
    * An identical proposal is a no-op delta, not a version bump — so replaying
      a scan does not manufacture the appearance of learning.
    """
    if not str(why).strip():
        raise WorldStateError("a world-state change must state why")
    available = {e.evidence_id for e in new_evidence}
    for belief in state.beliefs.values():
        available.update(belief.evidence_ids)

    changes: list[BeliefChange] = []
    merged = dict(state.beliefs)
    for belief in proposed:
        if belief.status in EVIDENCE_BACKED:
            unbacked = [eid for eid in belief.evidence_ids if eid not in available]
            if unbacked:
                raise WorldStateError(
                    f"{belief.fact_key}: cites evidence not present in this delta "
                    f"or in prior support: {unbacked}")
        before = merged.get(belief.fact_key)
        if before is not None and before.to_dict() == belief.to_dict():
            continue
        merged[belief.fact_key] = belief
        changes.append(BeliefChange(belief.fact_key, before, belief, why))

    next_state = WorldState(
        subject_id=state.subject_id,
        beliefs=merged,
        version=state.version + (1 if changes else 0),
    )
    delta = WorldStateDelta(
        subject_id=state.subject_id,
        prior_state_digest=state.digest,
        next_state_digest=next_state.digest,
        new_evidence=tuple(new_evidence),
        changes=tuple(changes),
        remaining_unknown=next_state.unknowns(),
        external_gates=next_state.external_gates(),
        recorded_at=recorded_at,
    )
    return next_state, delta
