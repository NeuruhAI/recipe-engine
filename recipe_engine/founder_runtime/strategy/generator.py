"""Derive candidate strategies from a compiled Mission Contract.

Phase 4 requires the runtime to *generate* candidate strategies before it
collapses them.  There was no generator: every non-test ``CandidateStrategy`` in
the tree was a source literal inside the system-status vertical slice, so
nothing connected a Mission Contract to the candidate set that gets collapsed.

What "generated" means here is deliberate.  This module does not ask a model for
strategies — model output is untrusted structured input, and a strategy that
enters the decision packet becomes the basis of a plan.  Instead it derives the
candidate set from material the mission already carries:

* the **authority envelope and constraints** decide which strategy families are
  admissible at all (a read-only mission cannot produce a mutating strategy),
* the **non-goals** eliminate families outright, with the eliminating non-goal
  recorded,
* the **claims actually observed** decide each family's evidence coverage, which
  is what its expected outcome is computed from,
* the **available sources** decide dependency satisfaction.

The consequence is that the same code yields different candidates, different
scores and different evidence gaps for different missions and different observed
evidence — which is what distinguishes generation from a second hardcoding.
Generation is pure and deterministic: no clock, no randomness, no I/O, so a
replay produces the identical candidate set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from recipe_engine.autonomy.models import (
    CandidateStrategy,
    MetricValue,
    SV_METRIC,
    SV_STRATEGY,
)
from recipe_engine.founder_runtime.contracts.mission import Mission

# Action classes a read-only mission may propose.  Anything outside this set is
# inadmissible under a read-only envelope regardless of how attractive it looks.
READ_ONLY_ACTION_CLASSES = frozenset({"observe", "analysis", "research"})

# Constraints that pin a mission to the read-only action classes.
_READ_ONLY_CONSTRAINTS = frozenset({
    "no_production_mutation",
    "fixture_mode_only",
    "read_only",
    "no_write_operations",
})

# Non-goal -> the action classes it forbids.
_NON_GOAL_FORBIDS: Mapping[str, frozenset[str]] = {
    "changing_production_state": frozenset({"mutate", "deploy", "execute"}),
    "contacting_third_parties": frozenset({"communicate", "notify"}),
    "deploying_anything": frozenset({"deploy"}),
    "writing_to_any_system": frozenset({"mutate", "write"}),
    "spending_money": frozenset({"purchase"}),
}


@dataclass(frozen=True)
class _Family:
    """A strategy shape, instantiated only if the mission admits it."""

    family: str
    title: str
    thesis: str
    action_classes: tuple[str, ...]
    wants_claims: tuple[str, ...]
    wants_sources: tuple[str, ...]
    duration: str
    time_profile: str
    next_safe_action: str
    failure_modes: tuple[str, ...]
    # Weight applied to evidence coverage.  A corroborating family is worth more
    # per unit of coverage than a single-source one; a defer family is worth
    # least because it produces no finding on its own.
    weight: str


_FAMILIES: tuple[_Family, ...] = (
    _Family(
        family="direct_observation",
        title="Observe current state directly",
        thesis="Read the organism's current state from its own surfaces and report what is true now",
        action_classes=("observe",),
        wants_claims=("claim_overall_health", "claim_blocked"),
        wants_sources=("mother", "github"),
        duration="minutes",
        time_profile="short",
        next_safe_action="review_current_state_report",
        failure_modes=("stale_snapshot", "partial_source_availability"),
        weight="0.80",
    ),
    _Family(
        family="corroborated_comparison",
        title="Corroborate across independent sources",
        thesis="Accept a finding only where two independent sources agree, and disclose where they do not",
        action_classes=("observe", "analysis"),
        wants_claims=("claim_overall_health", "claim_blocked", "claim_source_identity"),
        wants_sources=("mother", "github", "vault"),
        duration="minutes",
        time_profile="short",
        next_safe_action="review_corroboration_matrix",
        failure_modes=("source_disagreement_unresolved", "correlated_sources_mistaken_for_independent"),
        weight="0.95",
    ),
    _Family(
        family="historical_analysis",
        title="Analyse recorded history",
        thesis="Derive the finding from durable recorded evidence rather than a point-in-time reading",
        action_classes=("analysis", "research"),
        wants_claims=("claim_history", "claim_source_identity"),
        wants_sources=("github", "vault", "qdrant"),
        duration="hours",
        time_profile="medium",
        next_safe_action="review_historical_findings",
        failure_modes=("history_truncated", "retrieval_unavailable"),
        weight="0.70",
    ),
    _Family(
        family="evidence_first_defer",
        title="Gather missing evidence before deciding",
        thesis="Decline to recommend while required evidence is missing; state exactly what is needed",
        action_classes=("observe",),
        wants_claims=(),
        wants_sources=(),
        duration="days",
        time_profile="long",
        next_safe_action="obtain_missing_evidence",
        failure_modes=("stagnation",),
        weight="0.30",
    ),
)


@dataclass
class GenerationReport:
    """The candidate set plus why each rejected family was rejected."""

    candidates: list[CandidateStrategy] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    admissible_action_classes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "rejected": [dict(r) for r in self.rejected],
            "admissible_action_classes": list(self.admissible_action_classes),
            "candidate_count": len(self.candidates),
        }


def _admissible_action_classes(mission: Mission) -> frozenset[str]:
    """The action classes this mission's own contract permits."""
    allowed = set(READ_ONLY_ACTION_CLASSES)
    if not (_READ_ONLY_CONSTRAINTS & set(mission.constraints)):
        # No read-only constraint present: the contract does not restrict to
        # observation.  Still refuse anything a non-goal forbids, below.
        allowed |= {"mutate", "deploy", "execute", "write", "communicate", "notify", "purchase"}
    for non_goal in mission.non_goals:
        allowed -= _NON_GOAL_FORBIDS.get(non_goal, frozenset())
    return frozenset(allowed)


def _forbidding_non_goal(mission: Mission, action_classes: Iterable[str]) -> str | None:
    for non_goal in mission.non_goals:
        forbidden = _NON_GOAL_FORBIDS.get(non_goal, frozenset())
        if forbidden & set(action_classes):
            return non_goal
    return None


def _coverage(wants: Sequence[str], have: Sequence[str]) -> Decimal:
    """Fraction of what a family needs that the mission actually has."""
    if not wants:
        return Decimal("1")
    present = sum(1 for w in wants if w in set(have))
    return (Decimal(present) / Decimal(len(wants))).quantize(Decimal("0.01"))


def generate_candidate_strategies(
    mission: Mission,
    *,
    claim_ids: Sequence[str],
    available_sources: Sequence[str],
    max_candidates: int = 4,
) -> GenerationReport:
    """Derive the candidate set this mission admits, given what was observed.

    ``claim_ids`` are the claims genuinely established by evidence so far;
    ``available_sources`` are the adapters that answered.  Both come from the
    mission's own execution, so a family that needs a source which is blocked
    scores lower and declares the gap rather than silently assuming it.
    """
    report = GenerationReport()
    allowed = _admissible_action_classes(mission)
    report.admissible_action_classes = tuple(sorted(allowed))

    have_claims = list(claim_ids)
    have_sources = list(available_sources)

    for fam in _FAMILIES:
        blocking_non_goal = _forbidding_non_goal(mission, fam.action_classes)
        if blocking_non_goal:
            report.rejected.append({
                "strategy_family": fam.family,
                "reason": "forbidden_by_non_goal",
                "detail": blocking_non_goal,
            })
            continue
        if not set(fam.action_classes) <= allowed:
            report.rejected.append({
                "strategy_family": fam.family,
                "reason": "action_class_not_admissible",
                "detail": ",".join(sorted(set(fam.action_classes) - allowed)),
            })
            continue

        claim_cov = _coverage(fam.wants_claims, have_claims)
        source_cov = _coverage(fam.wants_sources, have_sources)
        # Expected value is evidence coverage weighted by how much this family's
        # shape is worth.  It is derived from domain-supplied evidence, not
        # invented: with no claims and no sources it is zero.
        value = (Decimal(fam.weight) * claim_cov * source_cov).quantize(Decimal("0.01"))

        missing_claims = tuple(c for c in fam.wants_claims if c not in set(have_claims))
        missing_sources = tuple(s for s in fam.wants_sources if s not in set(have_sources))
        present_sources = tuple(s for s in fam.wants_sources if s in set(have_sources))

        report.candidates.append(CandidateStrategy(
            schema_version=SV_STRATEGY,
            strategy_id=f"strat_{fam.family}",
            title=fam.title,
            thesis=fam.thesis,
            strategy_family=fam.family,
            required_claim_ids=tuple(c for c in fam.wants_claims if c in set(have_claims)),
            assumption_claim_ids=missing_claims,
            expected_outcomes=(MetricValue(
                schema_version=SV_METRIC,
                metric_id="econ.attractiveness",
                value=value,
                unit="score",
                scale="0..1",
                source="domain_supplied",
                label=f"evidence_coverage:{fam.family}",
            ),),
            resources=tuple(sorted(present_sources)),
            dependencies=tuple(sorted(fam.wants_sources)),
            duration=fam.duration,
            reversibility="reversible",
            failure_modes=fam.failure_modes,
            legal_policy_checks=tuple(sorted(set(mission.constraints))),
            governance_class="standard",
            next_safe_action=fam.next_safe_action,
            stop_conditions=tuple(sorted(set(mission.non_goals))) or ("approval_required_for_mutation",),
            evidence_gaps=missing_claims + tuple(f"source:{s}" for s in missing_sources),
            action_classes=fam.action_classes,
            capital_intensity="none",
            time_profile=fam.time_profile,
            available_dependencies=present_sources,
            missing_dependencies=missing_sources,
            feasibility_notes=(
                f"claim_coverage={claim_cov}",
                f"source_coverage={source_cov}",
                f"family_weight={fam.weight}",
            ),
            stages_hint=(
                {"stage_id": "s1", "title": fam.title, "dependencies": [],
                 "action_class": fam.action_classes[0]},
                {"stage_id": "s2", "title": "Record finding with evidence", "dependencies": ["s1"],
                 "action_class": "analysis" if "analysis" in fam.action_classes else fam.action_classes[0]},
            ),
            extensions={
                "generated_from_mission": mission.mission_id,
                "generator_version": "fr.strategy.generator.v1",
                "domain": mission.domain,
                "world": mission.world,
            },
        ))

    # The collapse stage enforces the hard 0-4 selection cap.  This bound is on
    # what is offered to it, so an unbounded family list can never inflate the
    # packet; ordering is by derived value then family name, both deterministic.
    report.candidates.sort(
        key=lambda c: (-c.expected_outcomes[0].value, c.strategy_family)
    )
    if len(report.candidates) > max_candidates:
        for dropped in report.candidates[max_candidates:]:
            report.rejected.append({
                "strategy_family": dropped.strategy_family,
                "reason": "exceeded_candidate_cap",
                "detail": str(max_candidates),
            })
        report.candidates = report.candidates[:max_candidates]
    return report
