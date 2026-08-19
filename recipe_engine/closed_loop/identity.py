"""Identity — what every closed-loop event must be able to say about itself.

The grand-theory loop only works if a later reader can answer "which world,
which subject, which run, which decision, which action produced this?" without
guessing. So identity is a *contract*, not a convention: every event binds a
fixed set of references, and the ones a given event type genuinely cannot know
are recorded as `None` rather than quietly omitted.

WORLD MODE is part of identity, not metadata. A synthetic exercise and a live
observation are not the same kind of fact, and the difference must survive
serialization — otherwise a fixture can eventually be mistaken for evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


SCHEMA_IDENTITY = "neuruh.closed-loop.identity.v1"


def canonical_json(value: Any) -> str:
    """One serialization, so one digest. Sorted keys, no incidental spacing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class WorldMode(str, Enum):
    """How much a fact is allowed to mean.

    LIVE      — observed from the real world; may support production learning.
    SHADOW    — real inputs, non-acting run; observation only.
    FIXTURE   — a court exercise over realistic shapes; proves mechanism.
    SYNTHETIC — invented inputs; proves mechanism and NOTHING about the world.
    """
    LIVE = "live"
    SHADOW = "shadow"
    FIXTURE = "fixture"
    SYNTHETIC = "synthetic"


#: Modes whose lineage may ever reach promotion eligibility. Everything else
#: can traverse the whole loop and must stop at HELD. See promotion.py.
PROMOTABLE_MODES: frozenset[WorldMode] = frozenset({WorldMode.LIVE})


class RefKind(str, Enum):
    WORLD = "world"
    SUBJECT = "subject"
    ACTOR = "actor"
    RUN = "run"
    MISSION = "mission"
    DECISION = "decision"
    ACTION = "action"


@dataclass(frozen=True)
class Ref:
    """A typed pointer. `kind` + `ref_id` is the whole of it — a Ref never
    carries the referent's payload, so it cannot drift from it."""
    kind: RefKind
    ref_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RefKind):
            raise IdentityError(f"ref kind must be a RefKind, got {self.kind!r}")
        if not str(self.ref_id).strip():
            raise IdentityError(f"{self.kind.value} ref_id must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "ref_id": self.ref_id}

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Ref":
        return Ref(RefKind(d["kind"]), str(d["ref_id"]))


class IdentityError(ValueError):
    """Raised when identity is malformed. Never downgraded to a warning."""


@dataclass(frozen=True)
class EvidenceRef:
    """Where a claim came from, and what it looked like when we read it.

    `content_digest` is what makes this auditable: the same pointer with a
    different digest is a different observation, and the loop must be able to
    tell those apart.
    """
    evidence_id: str
    source_type: str
    pointer: str
    observed_at: str
    content_digest: str

    def __post_init__(self) -> None:
        for name in ("evidence_id", "source_type", "pointer", "observed_at", "content_digest"):
            if not str(getattr(self, name)).strip():
                raise IdentityError(f"evidence.{name} must be non-empty")
        if len(self.content_digest) != 64 or any(c not in "0123456789abcdef" for c in self.content_digest):
            raise IdentityError("evidence.content_digest must be lowercase sha256 hex")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "pointer": self.pointer,
            "observed_at": self.observed_at,
            "content_digest": self.content_digest,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "EvidenceRef":
        return EvidenceRef(
            evidence_id=str(d["evidence_id"]), source_type=str(d["source_type"]),
            pointer=str(d["pointer"]), observed_at=str(d["observed_at"]),
            content_digest=str(d["content_digest"]),
        )


@dataclass(frozen=True)
class EventIdentity:
    """The full binding carried by every closed-loop event.

    `world` and `actor` are always required — an event with no world is not
    locatable and an event with no actor is not accountable. The rest are
    required per event type (see events.REQUIRED_REFS); where an event type
    genuinely has no such referent the slot is explicitly None.
    """
    world: Ref
    actor: Ref
    mode: WorldMode
    subject: Ref | None = None
    run: Ref | None = None
    mission: Ref | None = None
    decision: Ref | None = None
    action: Ref | None = None

    def __post_init__(self) -> None:
        if self.world.kind is not RefKind.WORLD:
            raise IdentityError("world ref must be kind=world")
        if self.actor.kind is not RefKind.ACTOR:
            raise IdentityError("actor ref must be kind=actor")
        for name, expected in (("subject", RefKind.SUBJECT), ("run", RefKind.RUN),
                               ("mission", RefKind.MISSION), ("decision", RefKind.DECISION),
                               ("action", RefKind.ACTION)):
            ref = getattr(self, name)
            if ref is not None and ref.kind is not expected:
                raise IdentityError(f"{name} ref must be kind={expected.value}")
        if not isinstance(self.mode, WorldMode):
            raise IdentityError("mode must be a WorldMode")

    def get(self, kind: RefKind) -> Ref | None:
        return {
            RefKind.WORLD: self.world, RefKind.ACTOR: self.actor,
            RefKind.SUBJECT: self.subject, RefKind.RUN: self.run,
            RefKind.MISSION: self.mission, RefKind.DECISION: self.decision,
            RefKind.ACTION: self.action,
        }[kind]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_IDENTITY,
            "world": self.world.to_dict(),
            "actor": self.actor.to_dict(),
            "mode": self.mode.value,
            "subject": None if self.subject is None else self.subject.to_dict(),
            "run": None if self.run is None else self.run.to_dict(),
            "mission": None if self.mission is None else self.mission.to_dict(),
            "decision": None if self.decision is None else self.decision.to_dict(),
            "action": None if self.action is None else self.action.to_dict(),
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "EventIdentity":
        def opt(key: str) -> Ref | None:
            raw = d.get(key)
            return None if raw is None else Ref.from_dict(raw)
        return EventIdentity(
            world=Ref.from_dict(d["world"]), actor=Ref.from_dict(d["actor"]),
            mode=WorldMode(d["mode"]), subject=opt("subject"), run=opt("run"),
            mission=opt("mission"), decision=opt("decision"), action=opt("action"),
        )


def world(ref_id: str) -> Ref:
    return Ref(RefKind.WORLD, ref_id)


def subject(ref_id: str) -> Ref:
    return Ref(RefKind.SUBJECT, ref_id)


def actor(ref_id: str) -> Ref:
    return Ref(RefKind.ACTOR, ref_id)


def run(ref_id: str) -> Ref:
    return Ref(RefKind.RUN, ref_id)


def mission(ref_id: str) -> Ref:
    return Ref(RefKind.MISSION, ref_id)


def decision(ref_id: str) -> Ref:
    return Ref(RefKind.DECISION, ref_id)


def action(ref_id: str) -> Ref:
    return Ref(RefKind.ACTION, ref_id)
