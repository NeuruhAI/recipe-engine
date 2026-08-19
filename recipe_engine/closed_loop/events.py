"""The one event grammar for the closed loop.

Everything the loop does becomes one of these events, in one ledger, with one
identity contract. That is the whole point: the reason Neuruh could not learn
from what actually happened is that observation, decision, action and outcome
each lived in a different shape, so nothing could be joined without guessing.

Two rules make the grammar trustworthy rather than merely tidy:

1. **Required bindings are per type.** A DECISION_APPROVED with no decision ref
   is rejected at construction. The table below is the contract; it is data, so
   an independent verifier (neuruh-ledgers) can enforce the same rule without
   importing this package.

2. **No hidden magic linking.** A causal predecessor is a *declared* claim with
   a stated relation and basis. The loop never infers "these two events are
   about the same thing because their timestamps are close" — if a link is not
   declared, it does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.identity import (
    EventIdentity, EvidenceRef, IdentityError, RefKind, canonical_json, digest,
)


SCHEMA_EVENT = "neuruh.closed-loop.event.v1"
GENESIS_HASH = "0" * 64


class LoopEventType(str, Enum):
    """The canonical private event family. Ordered as the loop runs."""
    SOURCE_OBSERVED = "source_observed"
    SIGNAL_NORMALIZED = "signal_normalized"

    RECIPE_EVALUATED = "recipe_evaluated"
    OPPORTUNITY_CREATED = "opportunity_created"
    OPPORTUNITY_UPDATED = "opportunity_updated"

    DECISION_PROPOSED = "decision_proposed"
    DECISION_APPROVED = "decision_approved"
    DECISION_BLOCKED = "decision_blocked"

    ACTION_INTENT_CREATED = "action_intent_created"
    ACTION_EXECUTED = "action_executed"
    ACTION_FAILED = "action_failed"

    FORECAST_SEALED = "forecast_sealed"
    OUTCOME_OBSERVED = "outcome_observed"
    OUTCOME_ATTRIBUTED = "outcome_attributed"

    CALIBRATION_RECORDED = "calibration_recorded"
    LEARNING_PROPOSED = "learning_proposed"

    PROMOTION_ELIGIBLE = "promotion_eligible"
    PROMOTION_HELD = "promotion_held"
    PROMOTION_BLOCKED = "promotion_blocked"

    VERSION_PROMOTED = "version_promoted"

    STATE_DRIFT_OBSERVED = "state_drift_observed"


#: Per-type required identity references. World, actor and mode are required on
#: every event by EventIdentity itself, so they are not repeated here.
#: This table is exported to JSON for the independent verifier — see
#: `grammar_manifest()`.
REQUIRED_REFS: dict[LoopEventType, tuple[RefKind, ...]] = {
    LoopEventType.SOURCE_OBSERVED: (RefKind.RUN,),
    LoopEventType.SIGNAL_NORMALIZED: (RefKind.RUN, RefKind.SUBJECT),
    LoopEventType.RECIPE_EVALUATED: (RefKind.RUN, RefKind.SUBJECT),
    LoopEventType.OPPORTUNITY_CREATED: (RefKind.RUN, RefKind.SUBJECT),
    LoopEventType.OPPORTUNITY_UPDATED: (RefKind.RUN, RefKind.SUBJECT),
    LoopEventType.DECISION_PROPOSED: (RefKind.RUN, RefKind.SUBJECT, RefKind.DECISION),
    LoopEventType.DECISION_APPROVED: (RefKind.RUN, RefKind.SUBJECT, RefKind.DECISION),
    LoopEventType.DECISION_BLOCKED: (RefKind.RUN, RefKind.SUBJECT, RefKind.DECISION),
    LoopEventType.ACTION_INTENT_CREATED: (RefKind.RUN, RefKind.SUBJECT, RefKind.DECISION, RefKind.ACTION),
    LoopEventType.ACTION_EXECUTED: (RefKind.RUN, RefKind.SUBJECT, RefKind.DECISION, RefKind.ACTION),
    LoopEventType.ACTION_FAILED: (RefKind.RUN, RefKind.SUBJECT, RefKind.DECISION, RefKind.ACTION),
    LoopEventType.FORECAST_SEALED: (RefKind.RUN, RefKind.SUBJECT, RefKind.DECISION),
    LoopEventType.OUTCOME_OBSERVED: (RefKind.SUBJECT,),
    LoopEventType.OUTCOME_ATTRIBUTED: (RefKind.SUBJECT,),
    LoopEventType.CALIBRATION_RECORDED: (),
    LoopEventType.LEARNING_PROPOSED: (),
    LoopEventType.PROMOTION_ELIGIBLE: (),
    LoopEventType.PROMOTION_HELD: (),
    LoopEventType.PROMOTION_BLOCKED: (),
    LoopEventType.VERSION_PROMOTED: (),
    LoopEventType.STATE_DRIFT_OBSERVED: (),
}

#: Event types that assert something about the world and therefore may not be
#: recorded without at least one piece of source evidence. Decisions, gates and
#: proposals are *our* acts, not world facts, so they are exempt — they bind to
#: the events that carry the evidence instead.
EVIDENCE_REQUIRED: frozenset[LoopEventType] = frozenset({
    LoopEventType.SOURCE_OBSERVED,
    LoopEventType.SIGNAL_NORMALIZED,
    LoopEventType.OUTCOME_OBSERVED,
    LoopEventType.STATE_DRIFT_OBSERVED,
})


class CausalRelation(str, Enum):
    """How a predecessor relates to this event. Every link is one of these and
    says which; `SUSPECTED` is legal and is not the same as `PRODUCED_BY`."""
    PRODUCED_BY = "produced_by"          # this event exists because that one did
    EVALUATED = "evaluated"              # this event scored that one's material
    APPROVED = "approved"                # this event is the gate on that one
    EXECUTED = "executed"                # this event is that intent, carried out
    OBSERVED_EFFECT_OF = "observed_effect_of"   # claimed effect, with a basis
    SUPERSEDES = "supersedes"
    SUSPECTED = "suspected"              # declared hunch; never treated as fact


@dataclass(frozen=True)
class CausalClaim:
    """A declared edge in the causal graph.

    `basis` is mandatory. "Because the ledger says so" is a basis; silence is
    not. This is the mechanism that keeps attribution from becoming folklore.
    """
    predecessor_event_id: str
    relation: CausalRelation
    basis: str

    def __post_init__(self) -> None:
        if not str(self.predecessor_event_id).strip():
            raise EventGrammarError("causal claim needs a predecessor_event_id")
        if not isinstance(self.relation, CausalRelation):
            raise EventGrammarError("causal claim relation must be a CausalRelation")
        if not str(self.basis).strip():
            raise EventGrammarError("causal claim needs a stated basis — no silent links")

    def to_dict(self) -> dict[str, Any]:
        return {"predecessor_event_id": self.predecessor_event_id,
                "relation": self.relation.value, "basis": self.basis}

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "CausalClaim":
        return CausalClaim(str(d["predecessor_event_id"]),
                           CausalRelation(d["relation"]), str(d["basis"]))


class EventGrammarError(ValueError):
    """A violation of the grammar. Always fatal to the append."""


#: Payload keys that would smuggle un-declared model reasoning into the ledger.
#: The loop stores declared rationale and declared features; it does not store
#: chain-of-thought, and it should be impossible to do so by accident.
FORBIDDEN_PAYLOAD_KEYS: frozenset[str] = frozenset({
    "chain_of_thought", "reasoning_trace", "scratchpad", "inner_monologue",
    "thoughts", "hidden_reasoning", "cot", "raw_model_output",
})


def _reject_forbidden(payload: Mapping[str, Any], path: str = "payload") -> None:
    for key, value in payload.items():
        lowered = str(key).strip().lower()
        if lowered in FORBIDDEN_PAYLOAD_KEYS:
            raise EventGrammarError(
                f"{path}.{key} is un-declared model reasoning; the ledger stores "
                "declared rationale and features only")
        if isinstance(value, Mapping):
            _reject_forbidden(value, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                if isinstance(item, Mapping):
                    _reject_forbidden(item, f"{path}.{key}[{i}]")


@dataclass
class LoopEvent:
    """One fact in the loop, bound to its world and to what came before it.

    `occurred_at` is when it happened out there; `observed_at` is when we
    learned of it. Collapsing the two is how a system convinces itself it knew
    something earlier than it did, so they are separate and both required.
    """
    event_type: LoopEventType
    identity: EventIdentity
    occurred_at: str
    observed_at: str
    reason: str
    payload: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[EvidenceRef, ...] = ()
    causal_predecessors: tuple[CausalClaim, ...] = ()
    schema_version: str = SCHEMA_EVENT
    event_id: str = ""
    sequence: int | None = None
    previous_event_hash: str = GENESIS_HASH
    event_hash: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, LoopEventType):
            raise EventGrammarError(f"unknown event type: {self.event_type!r}")
        if not isinstance(self.identity, EventIdentity):
            raise EventGrammarError("identity must be an EventIdentity")
        for name in ("occurred_at", "observed_at", "reason"):
            if not str(getattr(self, name)).strip():
                raise EventGrammarError(f"{name} must be non-empty")
        if str(self.observed_at) < str(self.occurred_at):
            raise EventGrammarError(
                "observed_at precedes occurred_at — we cannot learn of a thing "
                "before it happens")
        missing = [k.value for k in REQUIRED_REFS[self.event_type]
                   if self.identity.get(k) is None]
        if missing:
            raise EventGrammarError(
                f"{self.event_type.value} requires identity refs {missing}")
        if self.event_type in EVIDENCE_REQUIRED and not self.evidence:
            raise EventGrammarError(
                f"{self.event_type.value} asserts something about the world and "
                "requires at least one evidence reference")
        _reject_forbidden(self.payload)
        self.evidence = tuple(self.evidence)
        self.causal_predecessors = tuple(self.causal_predecessors)
        seen: set[tuple[str, str]] = set()
        for claim in self.causal_predecessors:
            key = (claim.predecessor_event_id, claim.relation.value)
            if key in seen:
                raise EventGrammarError("duplicate causal claim")
            seen.add(key)
        if not self.event_id:
            self.event_id = self.derived_event_id()

    # -- identity / hashing -------------------------------------------------

    def body(self) -> dict[str, Any]:
        """Everything the hash covers except chain position, which is added by
        the ledger. Kept separate so an event has a stable identity before it
        is ever appended."""
        return {
            "schema_version": self.schema_version,
            "event_type": self.event_type.value,
            "identity": self.identity.to_dict(),
            "occurred_at": self.occurred_at,
            "observed_at": self.observed_at,
            "reason": self.reason,
            "payload": dict(self.payload),
            "evidence": [e.to_dict() for e in self.evidence],
            "causal_predecessors": [c.to_dict() for c in self.causal_predecessors],
        }

    def derived_event_id(self) -> str:
        """Content-addressed. Two byte-identical events are the same event, so
        a replay does not manufacture a second copy of history."""
        return digest(self.body())

    def hash_material(self) -> dict[str, Any]:
        return {**self.body(), "event_id": self.event_id,
                "sequence": self.sequence,
                "previous_event_hash": self.previous_event_hash}

    def computed_hash(self) -> str:
        return digest(self.hash_material())

    def seal(self, *, sequence: int, previous_event_hash: str) -> "LoopEvent":
        self.sequence = sequence
        self.previous_event_hash = previous_event_hash
        self.event_hash = self.computed_hash()
        return self

    def verify(self) -> bool:
        return (self.event_id == self.derived_event_id()
                and bool(self.event_hash)
                and self.event_hash == self.computed_hash())

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {**self.hash_material(), "event_hash": self.event_hash}

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "LoopEvent":
        event = LoopEvent(
            event_type=LoopEventType(d["event_type"]),
            identity=EventIdentity.from_dict(d["identity"]),
            occurred_at=str(d["occurred_at"]),
            observed_at=str(d["observed_at"]),
            reason=str(d["reason"]),
            payload=dict(d.get("payload") or {}),
            evidence=tuple(EvidenceRef.from_dict(e) for e in (d.get("evidence") or ())),
            causal_predecessors=tuple(
                CausalClaim.from_dict(c) for c in (d.get("causal_predecessors") or ())),
            schema_version=str(d.get("schema_version") or SCHEMA_EVENT),
            event_id=str(d.get("event_id") or ""),
        )
        event.sequence = d.get("sequence")
        event.previous_event_hash = str(d.get("previous_event_hash") or GENESIS_HASH)
        event.event_hash = str(d.get("event_hash") or "")
        return event


def grammar_manifest() -> dict[str, Any]:
    """The grammar as data, so a verifier outside this package can enforce it.

    neuruh-ledgers/tools/verify_loop_ledger consumes exactly this shape. If the
    two ever disagree, the ledger's copy is a stale claim and the manifest
    digest recorded with a lane will say so.
    """
    return {
        "schema_version": SCHEMA_EVENT,
        "genesis_hash": GENESIS_HASH,
        "event_types": [t.value for t in LoopEventType],
        "required_refs": {t.value: [k.value for k in REQUIRED_REFS[t]] for t in LoopEventType},
        "evidence_required": sorted(t.value for t in EVIDENCE_REQUIRED),
        "causal_relations": [r.value for r in CausalRelation],
        "forbidden_payload_keys": sorted(FORBIDDEN_PAYLOAD_KEYS),
    }


def grammar_digest() -> str:
    return digest(grammar_manifest())
