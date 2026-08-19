"""Replay — reconstruct the whole story from the ledger, and nothing else.

Given the event ledger, the recipe version, the inputs and the policy versions,
a reader must be able to answer, without the original run's memory:

    why did this opportunity exist?
    what decision was made, under whose authority?
    what action was taken?
    what outcome followed?
    what learning was proposed?

Two properties make that answer worth having.

**Determinism.** `replay(events)` over the same events produces a byte-identical
reconstruction; its digest is the test.

**No inference.** The reconstruction follows declared causal claims and bound
digests. Where the chain is not declared, it reports a *gap* rather than
bridging it with a plausible guess. A replay full of honest gaps is useful; a
replay that quietly invents the missing links is worse than none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.events import (
    CausalRelation, LoopEvent, LoopEventType,
)
from recipe_engine.closed_loop.identity import RefKind, WorldMode, canonical_json, digest


SCHEMA_REPLAY = "neuruh.closed-loop.replay.v1"


class ReplayError(ValueError):
    """The ledger cannot be replayed as given."""


@dataclass(frozen=True)
class SubjectStory:
    """Everything the ledger says about one subject, in loop order."""
    subject_id: str
    why_opportunity_existed: tuple[str, ...]
    sources_observed: tuple[str, ...]
    recipe_evaluations: tuple[Mapping[str, Any], ...]
    decisions: tuple[Mapping[str, Any], ...]
    actions: tuple[Mapping[str, Any], ...]
    outcomes: tuple[Mapping[str, Any], ...]
    attributions: tuple[Mapping[str, Any], ...]
    gaps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "why_opportunity_existed": list(self.why_opportunity_existed),
            "sources_observed": list(self.sources_observed),
            "recipe_evaluations": [dict(e) for e in self.recipe_evaluations],
            "decisions": [dict(d) for d in self.decisions],
            "actions": [dict(a) for a in self.actions],
            "outcomes": [dict(o) for o in self.outcomes],
            "attributions": [dict(a) for a in self.attributions],
            "gaps": list(self.gaps),
        }


@dataclass(frozen=True)
class LoopReconstruction:
    """The deterministic projection of a ledger."""
    event_count: int
    ledger_tip: str
    modes: tuple[str, ...]
    subjects: tuple[SubjectStory, ...]
    learning_proposed: tuple[Mapping[str, Any], ...]
    promotion_decisions: tuple[Mapping[str, Any], ...]
    versions_promoted: tuple[Mapping[str, Any], ...]
    drift_observed: tuple[Mapping[str, Any], ...]
    global_gaps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_REPLAY,
            "event_count": self.event_count,
            "ledger_tip": self.ledger_tip,
            "modes": list(self.modes),
            "subjects": [s.to_dict() for s in self.subjects],
            "learning_proposed": [dict(p) for p in self.learning_proposed],
            "promotion_decisions": [dict(p) for p in self.promotion_decisions],
            "versions_promoted": [dict(v) for v in self.versions_promoted],
            "drift_observed": [dict(d) for d in self.drift_observed],
            "global_gaps": list(self.global_gaps),
        }

    @property
    def digest(self) -> str:
        return digest(self.to_dict())


_SUBJECT_TYPES = {
    LoopEventType.SIGNAL_NORMALIZED, LoopEventType.RECIPE_EVALUATED,
    LoopEventType.OPPORTUNITY_CREATED, LoopEventType.OPPORTUNITY_UPDATED,
    LoopEventType.DECISION_PROPOSED, LoopEventType.DECISION_APPROVED,
    LoopEventType.DECISION_BLOCKED, LoopEventType.ACTION_INTENT_CREATED,
    LoopEventType.ACTION_EXECUTED, LoopEventType.ACTION_FAILED,
    LoopEventType.FORECAST_SEALED, LoopEventType.OUTCOME_OBSERVED,
    LoopEventType.OUTCOME_ATTRIBUTED,
}


def replay(events: Sequence[LoopEvent]) -> LoopReconstruction:
    """Project the ledger. Pure, ordered, and gap-honest."""
    ordered = sorted(events, key=lambda e: (e.sequence if e.sequence is not None else 0))
    by_id = {e.event_id: e for e in ordered}

    subject_events: dict[str, list[LoopEvent]] = {}
    learning: list[Mapping[str, Any]] = []
    promotions: list[Mapping[str, Any]] = []
    promoted: list[Mapping[str, Any]] = []
    drift: list[Mapping[str, Any]] = []
    modes: set[str] = set()

    for event in ordered:
        modes.add(event.identity.mode.value)
        subject = event.identity.subject
        if event.event_type in _SUBJECT_TYPES and subject is not None:
            subject_events.setdefault(subject.ref_id, []).append(event)
        elif event.event_type is LoopEventType.LEARNING_PROPOSED:
            learning.append({"event_id": event.event_id, "reason": event.reason,
                             **_pick(event.payload, ("proposal_id", "target_id",
                                                     "current_version", "candidate_version",
                                                     "proposal_digest", "sample_count"))})
        elif event.event_type in (LoopEventType.PROMOTION_ELIGIBLE,
                                  LoopEventType.PROMOTION_HELD,
                                  LoopEventType.PROMOTION_BLOCKED):
            promotions.append({"event_id": event.event_id,
                               "outcome": event.event_type.value,
                               "reason": event.reason,
                               **_pick(event.payload, ("decision_id", "proposal_digest",
                                                       "requested_stage", "reasons"))})
        elif event.event_type is LoopEventType.VERSION_PROMOTED:
            promoted.append({"event_id": event.event_id, "reason": event.reason,
                             **_pick(event.payload, ("promotion_id", "target_id",
                                                     "from_version", "to_version",
                                                     "external_approval_ref"))})
        elif event.event_type is LoopEventType.STATE_DRIFT_OBSERVED:
            drift.append({"event_id": event.event_id, "reason": event.reason,
                          **_pick(event.payload, ("expected", "observed", "drift_class"))})

    stories: list[SubjectStory] = []
    for subject_id in sorted(subject_events):
        stories.append(_subject_story(subject_id, subject_events[subject_id], by_id))

    global_gaps: list[str] = []
    if learning and not promotions:
        global_gaps.append(
            "a learning proposal was recorded with no promotion decision; the "
            "proposal is unresolved, not accepted")
    for record in promoted:
        if not record.get("external_approval_ref"):
            global_gaps.append(
                f"version promotion {record.get('promotion_id')} names no external "
                "approval reference")

    return LoopReconstruction(
        event_count=len(ordered),
        ledger_tip=ordered[-1].event_hash if ordered else "",
        modes=tuple(sorted(modes)),
        subjects=tuple(stories),
        learning_proposed=tuple(learning),
        promotion_decisions=tuple(promotions),
        versions_promoted=tuple(promoted),
        drift_observed=tuple(drift),
        global_gaps=tuple(global_gaps))


def _pick(payload: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    return {k: payload[k] for k in keys if k in payload}


def _subject_story(subject_id: str, events: Sequence[LoopEvent],
                   by_id: Mapping[str, LoopEvent]) -> SubjectStory:
    why: list[str] = []
    sources: list[str] = []
    evaluations: list[Mapping[str, Any]] = []
    decisions: list[Mapping[str, Any]] = []
    actions: list[Mapping[str, Any]] = []
    outcomes: list[Mapping[str, Any]] = []
    attributions: list[Mapping[str, Any]] = []
    gaps: list[str] = []

    for event in events:
        if event.event_type is LoopEventType.SIGNAL_NORMALIZED:
            sources.extend(e.source_type for e in event.evidence)
        elif event.event_type is LoopEventType.RECIPE_EVALUATED:
            evaluations.append({"event_id": event.event_id, "reason": event.reason,
                                **_pick(event.payload, ("recipe_key", "qualification",
                                                        "score", "reason_codes",
                                                        "evaluation_digest", "missing"))})
        elif event.event_type is LoopEventType.OPPORTUNITY_CREATED:
            why.append(event.reason)
            why.extend(str(c) for c in event.payload.get("reason_codes", ()))
        elif event.event_type in (LoopEventType.DECISION_PROPOSED,
                                  LoopEventType.DECISION_APPROVED,
                                  LoopEventType.DECISION_BLOCKED):
            decisions.append({"event_id": event.event_id,
                              "disposition": event.event_type.value,
                              "reason": event.reason,
                              **_pick(event.payload, ("decision_id", "decision_digest",
                                                      "governance_verdict", "authority_id",
                                                      "reason_codes"))})
        elif event.event_type in (LoopEventType.ACTION_INTENT_CREATED,
                                  LoopEventType.ACTION_EXECUTED,
                                  LoopEventType.ACTION_FAILED):
            actions.append({"event_id": event.event_id, "phase": event.event_type.value,
                            "reason": event.reason,
                            **_pick(event.payload, ("action_id", "intent_digest",
                                                    "receipt_digest", "capability",
                                                    "status", "error_codes"))})
        elif event.event_type is LoopEventType.OUTCOME_OBSERVED:
            outcomes.append({"event_id": event.event_id, "reason": event.reason,
                             **_pick(event.payload, ("outcome_id", "outcome_digest",
                                                     "grade", "tiers", "lost_reason_code"))})
        elif event.event_type is LoopEventType.OUTCOME_ATTRIBUTED:
            attributions.append({"event_id": event.event_id, "reason": event.reason,
                                 **_pick(event.payload, ("attribution_id", "attribution_digest",
                                                         "strengths", "unexplained"))})

    # Gap analysis — declared links only, never inferred ones.
    if evaluations and not why:
        gaps.append("evaluated but no opportunity creation recorded")
    if decisions and not evaluations:
        gaps.append("decision with no recorded recipe evaluation")
    if actions and not decisions:
        gaps.append("action with no recorded decision")
    if outcomes and not actions:
        gaps.append("outcome with no recorded action; attribution cannot be DIRECT")
    for outcome in outcomes:
        event = by_id.get(str(outcome.get("event_id")))
        if event is not None and not any(
                claim.relation in (CausalRelation.OBSERVED_EFFECT_OF,
                                   CausalRelation.EXECUTED)
                for claim in event.causal_predecessors):
            gaps.append(
                f"outcome {outcome.get('outcome_id')} declares no causal link back "
                "to an action; it is recorded, not attributed")

    return SubjectStory(
        subject_id=subject_id,
        why_opportunity_existed=tuple(why),
        sources_observed=tuple(sorted(set(sources))),
        recipe_evaluations=tuple(evaluations),
        decisions=tuple(decisions),
        actions=tuple(actions),
        outcomes=tuple(outcomes),
        attributions=tuple(attributions),
        gaps=tuple(gaps))
