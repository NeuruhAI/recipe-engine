"""One complete synthetic loop, start to finish, with nothing mutated.

WORLD → SIGNAL → RECIPE EVALUATION → OPPORTUNITY → DECISION → GOVERNED ACTION
INTENT → EXECUTION RECEIPT → OBSERVED OUTCOME → ATTRIBUTION → CALIBRATION →
LEARNING PROPOSAL → PROMOTION GATE → CANDIDATE VERSION.

It runs in `WorldMode.SYNTHETIC`, and that is not a detail — it is the assertion
under test. The loop traverses every stage and then **stops at PROMOTION_HELD**,
because `promotion.evaluate_promotion` refuses eligibility to any proposal whose
evidence lineage is not live. So the mechanism is proven end to end and there is
no input to this function that produces a production change.

`run_full_loop(mode=WorldMode.LIVE)` runs the identical path with live-labelled
evidence and reaches PROMOTION_ELIGIBLE — which is how we know the HELD above is
the mode rule biting, and not the loop simply being unable to finish.

Nothing here writes outside a caller-supplied ledger path.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop import identity as ident
from recipe_engine.closed_loop.action import (
    ActionExecutionReceipt, ActionIntent, ExecutionStatus, ExpectedEffect,
    Reversibility, RiskClass, bind_execution,
)
from recipe_engine.closed_loop.attribution import (
    AttributionSet, Contribution, ContributorKind, strength_for,
)
from recipe_engine.closed_loop.calibration import (
    CalibrationDimension, ScoredPair, calibrate,
)
from recipe_engine.closed_loop.decision import (
    AuthorityRef, DeclaredRationale, DecisionDisposition, DecisionRecord,
    GovernanceVerdict,
)
from recipe_engine.closed_loop.events import (
    CausalClaim, CausalRelation, LoopEvent, LoopEventType,
)
from recipe_engine.closed_loop.forecast import Forecast, ForecastedMetric
from recipe_engine.closed_loop.identity import (
    EvidenceRef, EventIdentity, WorldMode, digest,
)
from recipe_engine.closed_loop.ledger import LoopLedger
from recipe_engine.closed_loop.learning import (
    ChangeKind, EvidenceWindow, ExpectedBenefit, ProposedChange, RegressionRisk,
    propose,
)
from recipe_engine.closed_loop.outcome import (
    LoopOutcomeRecord, OutcomeGrade, OutcomeMetric, OutcomeObservation, OutcomeTier,
)
from recipe_engine.closed_loop.promotion import (
    PromotionOutcome, PromotionPolicy, PromotionPrerequisites, Stage,
    evaluate_promotion,
)
from recipe_engine.closed_loop.recipe_eval import (
    QualificationState, RecipeEvaluationRecord, RecipeRef, ScoreComponent,
)
from recipe_engine.closed_loop.recipe_learning import (
    RecipeObservationCounts, diagnose, score_recipe,
)
from recipe_engine.closed_loop.replay import LoopReconstruction, replay
from recipe_engine.closed_loop.source_learning import (
    SourceAttempt, build_cells, rank_sources,
)
from recipe_engine.closed_loop.world_state import (
    Belief, EpistemicStatus, WorldState, apply_delta,
)


WORLD_ID = "world:nc-estate"
ACTOR_ID = "actor:recipe-engine.closed_loop.e2e"
RUN_ID = "run:t24d-synthetic-0001"
SUBJECT_ID = "subject:parcel-37021-000123"
RECIPE_ID = "graduation"
CURRENT_RECIPE_VERSION = "graduation.v1"
CANDIDATE_RECIPE_VERSION = "graduation.v1-candidate-t24d"


def _hexdigest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class LoopArtifacts:
    """Everything the loop produced, for inspection by tests and operators."""
    ledger: LoopLedger
    world_state: WorldState
    world_delta: Any
    evaluation: RecipeEvaluationRecord
    decision: DecisionRecord
    intent: ActionIntent
    receipt: ActionExecutionReceipt
    execution_binding: Mapping[str, Any]
    forecast: Forecast
    outcome: LoopOutcomeRecord
    attribution: AttributionSet
    calibration: Any
    source_ranking: Any
    recipe_scorecard: Any
    recipe_diagnosis: Any
    proposal: Any
    promotion_decision: Any
    reconstruction: LoopReconstruction


def _ident(mode: WorldMode, **refs: Any) -> EventIdentity:
    return EventIdentity(world=ident.world(WORLD_ID), actor=ident.actor(ACTOR_ID),
                         mode=mode, **refs)


def run_full_loop(*, mode: WorldMode = WorldMode.SYNTHETIC,
                  ledger_path: Path | str | None = None,
                  now: str = "2026-08-19T12:00:00Z") -> LoopArtifacts:
    """Drive every stage once. Deterministic given `mode` and `now`."""
    ledger = LoopLedger(ledger_path)
    run_ref = ident.run(RUN_ID)
    subject_ref = ident.subject(SUBJECT_ID)

    # ---- WORLD → SOURCE_OBSERVED ------------------------------------------
    tax_evidence = EvidenceRef(
        evidence_id="ev-tax-roll-1", source_type="county_tax_roll",
        pointer="county://37021/tax/2026/parcel-000123",
        observed_at="2026-06-01T00:00:00Z", content_digest=_hexdigest("tax-roll-row"))
    probate_evidence = EvidenceRef(
        evidence_id="ev-probate-1", source_type="county_probate_index",
        pointer="county://37021/probate/2026/E-0456",
        observed_at="2026-06-14T00:00:00Z", content_digest=_hexdigest("probate-row"))

    observed, _ = ledger.append(LoopEvent(
        event_type=LoopEventType.SOURCE_OBSERVED,
        identity=_ident(mode, run=run_ref),
        occurred_at="2026-06-01T00:00:00Z", observed_at="2026-06-02T00:00:00Z",
        reason="scheduled county source sweep",
        evidence=(tax_evidence, probate_evidence),
        payload={"source_types": ["county_tax_roll", "county_probate_index"]}))

    # ---- SIGNAL_NORMALIZED + world-state delta ----------------------------
    state = WorldState(subject_id=SUBJECT_ID)
    state, world_delta = apply_delta(
        state,
        proposed=[
            Belief("parcel_identity", "37021-000123", EpistemicStatus.KNOWN,
                   ("ev-tax-roll-1",), "county parcel roll"),
            Belief("owner_class", "heirs_estate", EpistemicStatus.KNOWN,
                   ("ev-probate-1",), "probate index names an estate"),
            Belief("delinquent_tax", True, EpistemicStatus.KNOWN,
                   ("ev-tax-roll-1",), "tax roll delinquency flag"),
            Belief("owner_reachable", None, EpistemicStatus.UNKNOWN, (),
                   "no contact point resolved yet"),
            Belief("estate_administrator", None, EpistemicStatus.EXTERNAL_GATE, (),
                   "closed only by the clerk of court's estate file"),
            Belief("likely_motivated_seller", True, EpistemicStatus.INFERRED, (),
                   "delinquency plus estate ownership; not observed"),
        ],
        new_evidence=[tax_evidence, probate_evidence],
        recorded_at=now,
        why="county sweep read the tax roll and the probate index")

    normalized, _ = ledger.append(LoopEvent(
        event_type=LoopEventType.SIGNAL_NORMALIZED,
        identity=_ident(mode, run=run_ref, subject=subject_ref),
        occurred_at="2026-06-14T00:00:00Z", observed_at="2026-06-15T00:00:00Z",
        reason="two county sources normalized onto one parcel",
        evidence=(tax_evidence, probate_evidence),
        payload={"world_state_delta_digest": world_delta.digest,
                 "remaining_unknown": list(world_delta.remaining_unknown),
                 "external_gates": list(world_delta.external_gates)},
        causal_predecessors=(CausalClaim(observed.event_id, CausalRelation.PRODUCED_BY,
                                         "normalized from this source observation"),)))

    # ---- RECIPE_EVALUATED -------------------------------------------------
    recipe = RecipeRef.of(RECIPE_ID, CURRENT_RECIPE_VERSION,
                          {"min_corroborating_sources": 2, "min_opportunity_score": 55,
                           "max_evidence_age_days": 365})
    evaluation = RecipeEvaluationRecord(
        evaluation_id="eval-0001", recipe=recipe, subject_id=SUBJECT_ID,
        candidate_id="cand-0001",
        inputs={"distinct_sources": 2, "families": ["tax_distress", "probate"],
                "opportunity_score": 71, "evidence_age_days": [79, 66]},
        input_evidence_digests=(tax_evidence.content_digest, probate_evidence.content_digest),
        score=Decimal("71"),
        components=(
            ScoreComponent("distress", Decimal("40"), Decimal("0.5"), ("delinquent_tax",)),
            ScoreComponent("ownership", Decimal("31"), Decimal("0.5"), ("owner_class",)),
        ),
        qualification=QualificationState.QUALIFIED,
        reason_codes=("corroborated_two_sources", "distress_family_present",
                      "score_above_floor", "evidence_in_date"),
        evaluated_at=now)

    evaluated, _ = ledger.append(LoopEvent(
        event_type=LoopEventType.RECIPE_EVALUATED,
        identity=_ident(mode, run=run_ref, subject=subject_ref),
        occurred_at=now, observed_at=now,
        reason="graduation policy v1 scored the normalized signal",
        payload={"recipe_key": recipe.key, "qualification": evaluation.qualification.value,
                 "score": str(evaluation.score), "reason_codes": list(evaluation.reason_codes),
                 "evaluation_digest": evaluation.digest, "missing": []},
        causal_predecessors=(CausalClaim(normalized.event_id, CausalRelation.EVALUATED,
                                         "scored this normalized signal"),)))

    # ---- OPPORTUNITY_CREATED ----------------------------------------------
    opportunity_id = "opp-0001"
    opportunity, _ = ledger.append(LoopEvent(
        event_type=LoopEventType.OPPORTUNITY_CREATED,
        identity=_ident(mode, run=run_ref, subject=subject_ref),
        occurred_at=now, observed_at=now,
        reason="qualified candidate under graduation v1",
        payload={"opportunity_id": opportunity_id, "recipe_key": recipe.key,
                 "reason_codes": list(evaluation.reason_codes)},
        causal_predecessors=(CausalClaim(evaluated.event_id, CausalRelation.PRODUCED_BY,
                                         "the qualifying evaluation"),)))

    # ---- DECISION ---------------------------------------------------------
    authority = AuthorityRef(
        authority_id="auth:operator-desk", authority_kind="operator",
        granted_by="founder", reference="neuruh-ledgers://authority/operator-desk.v1")
    decision_id = "dec-0001"
    decision = DecisionRecord(
        decision_id=decision_id, subject_id=SUBJECT_ID, opportunity_id=opportunity_id,
        recipe_evaluation_digest=evaluation.digest,
        evidence_ids=("ev-tax-roll-1", "ev-probate-1"),
        disposition=DecisionDisposition.APPROVED,
        rationale=DeclaredRationale(
            reason_codes=("qualified_candidate", "within_operator_authority"),
            features={"opportunity_score": 71, "owner_class": "heirs_estate",
                      "distinct_sources": 2}),
        governance_verdict=GovernanceVerdict.ALLOW,
        authority=authority, alternatives=(), alternatives_declared=False,
        decided_at=now, world_state_delta_digest=world_delta.digest)
    decision_ref = ident.decision(decision_id)

    proposed_event, _ = ledger.append(LoopEvent(
        event_type=LoopEventType.DECISION_PROPOSED,
        identity=_ident(mode, run=run_ref, subject=subject_ref, decision=decision_ref),
        occurred_at=now, observed_at=now,
        reason="pursue identity resolution for a qualified candidate",
        payload={"decision_id": decision_id, "decision_digest": decision.digest,
                 "reason_codes": list(decision.rationale.reason_codes)},
        causal_predecessors=(CausalClaim(opportunity.event_id, CausalRelation.PRODUCED_BY,
                                         "decision on this opportunity"),)))

    approved, _ = ledger.append(LoopEvent(
        event_type=LoopEventType.DECISION_APPROVED,
        identity=_ident(mode, run=run_ref, subject=subject_ref, decision=decision_ref),
        occurred_at=now, observed_at=now,
        reason="governance allowed; operator authority named",
        payload={"decision_id": decision_id, "decision_digest": decision.digest,
                 "governance_verdict": decision.governance_verdict.value,
                 "authority_id": authority.authority_id},
        causal_predecessors=(CausalClaim(proposed_event.event_id, CausalRelation.APPROVED,
                                         "the gate on this proposal"),)))

    # ---- FORECAST, sealed before anything is observed ---------------------
    forecast = Forecast(
        forecast_id="fc-0001", subject_id=SUBJECT_ID, decision_id=decision_id,
        recipe_key=recipe.key, probability=Decimal("0.70"),
        outcome_metric_key="identity_resolved", horizon_days=30,
        metrics=(ForecastedMetric("operator_minutes", Decimal("6"), "minutes", 30),),
        recorded_at="2026-08-19T12:00:00Z", forecast_version="fc.v1").seal()

    sealed, _ = ledger.append(LoopEvent(
        event_type=LoopEventType.FORECAST_SEALED,
        identity=_ident(mode, run=run_ref, subject=subject_ref, decision=decision_ref),
        occurred_at=now, observed_at=now,
        reason="prediction sealed before any outcome is observed",
        payload={"forecast_id": forecast.forecast_id, "forecast_digest": forecast.digest,
                 "probability": str(forecast.probability),
                 "horizon_days": forecast.horizon_days},
        causal_predecessors=(CausalClaim(approved.event_id, CausalRelation.PRODUCED_BY,
                                         "forecast bound to the approved decision"),)))

    # ---- ACTION INTENT → EXECUTION ----------------------------------------
    action_id = "act-0001"
    action_ref = ident.action(action_id)
    intent = ActionIntent(
        action_id=action_id, decision_id=decision_id, subject_id=SUBJECT_ID,
        what="resolve the estate's mailing party from the county tax mailing record",
        why_reason_codes=("qualified_candidate", "identity_unknown"),
        target="county_tax_mailing_party", capability="public_record_read",
        expected_effects=(
            ExpectedEffect("owner_reachable", True, Decimal("0.7")),
            ExpectedEffect("identity_resolved", True, Decimal("0.7")),
        ),
        risk=RiskClass.LOW, cost_estimate={"source_cost": "0.00", "operator_minutes": "6"},
        reversibility=Reversibility.REVERSIBLE, authority=authority,
        idempotency_key=digest({"subject": SUBJECT_ID, "capability": "public_record_read",
                                "decision": decision_id}),
        declared_at=now)

    intent_event, _ = ledger.append(LoopEvent(
        event_type=LoopEventType.ACTION_INTENT_CREATED,
        identity=_ident(mode, run=run_ref, subject=subject_ref, decision=decision_ref,
                        action=action_ref),
        occurred_at=now, observed_at=now,
        reason="declared before execution so the execution can be judged",
        payload={"action_id": action_id, "intent_digest": intent.digest,
                 "capability": intent.capability},
        causal_predecessors=(CausalClaim(approved.event_id, CausalRelation.PRODUCED_BY,
                                         "intent under this approved decision"),)))

    receipt = ActionExecutionReceipt(
        receipt_id="rcpt-0001", intent_digest=intent.digest, action_id=action_id,
        status=ExecutionStatus.EXECUTED,
        started_at="2026-08-19T12:01:00Z", finished_at="2026-08-19T12:03:00Z",
        observed_effects={"owner_reachable": True, "identity_resolved": True},
        actual_cost={"source_cost": "0.00", "operator_minutes": "4"},
        external_reference="county://37021/tax/mailing/000123")
    execution_binding = bind_execution(intent, receipt)

    executed, _ = ledger.append(LoopEvent(
        event_type=LoopEventType.ACTION_EXECUTED,
        identity=_ident(mode, run=run_ref, subject=subject_ref, decision=decision_ref,
                        action=action_ref),
        occurred_at="2026-08-19T12:03:00Z", observed_at="2026-08-19T12:03:00Z",
        reason="mailing party resolved from the public record",
        payload={"action_id": action_id, "intent_digest": intent.digest,
                 "receipt_digest": receipt.digest, "status": receipt.status.value},
        causal_predecessors=(CausalClaim(intent_event.event_id, CausalRelation.EXECUTED,
                                         "this intent, carried out"),)))

    # ---- OUTCOME ----------------------------------------------------------
    outcome_evidence = EvidenceRef(
        evidence_id="ev-mailing-party-1", source_type="county_tax_mailing_party",
        pointer="county://37021/tax/mailing/000123",
        observed_at="2026-08-19T12:03:00Z", content_digest=_hexdigest("mailing-party-row"))

    outcome = LoopOutcomeRecord(
        outcome_id="out-0001", subject_id=SUBJECT_ID, decision_id=decision_id,
        decision_digest=decision.digest, action_receipt_digest=receipt.digest,
        forecast_digest=forecast.digest,
        observations=(
            OutcomeObservation(
                observation_id="obs-process-1", tier=OutcomeTier.PROCESS,
                fact_key="identity_resolved", value=True,
                observed_at="2026-08-19T12:03:00Z",
                metrics=(OutcomeMetric("operator_minutes", Decimal("4"), "minutes"),)),
            OutcomeObservation(
                observation_id="obs-world-1", tier=OutcomeTier.WORLD,
                fact_key="mailing_party_named", value="estate of a decedent, c/o administrator",
                observed_at="2026-08-19T12:03:00Z", evidence=(outcome_evidence,)),
        ),
        grade=OutcomeGrade.SUCCESS, grader_role="desk",
        graded_at="2026-08-19T13:00:00Z", observed_at="2026-08-19T13:00:00Z",
        notes=("no business outcome yet: contact has not been attempted",))

    outcome_event, _ = ledger.append(LoopEvent(
        event_type=LoopEventType.OUTCOME_OBSERVED,
        identity=_ident(mode, subject=subject_ref),
        occurred_at="2026-08-19T12:03:00Z", observed_at="2026-08-19T13:00:00Z",
        reason="process and world outcomes observed; no business outcome claimed",
        evidence=(outcome_evidence,),
        payload={"outcome_id": outcome.outcome_id, "outcome_digest": outcome.digest,
                 "grade": outcome.grade.value,
                 "tiers": sorted({o.tier.value for o in outcome.observations}),
                 "calibration_blockers": list(outcome.calibration_blockers())},
        causal_predecessors=(CausalClaim(executed.event_id,
                                         CausalRelation.OBSERVED_EFFECT_OF,
                                         "observed after, and bound to, this execution"),)))

    # ---- ATTRIBUTION ------------------------------------------------------
    attribution = AttributionSet(
        attribution_id="attr-0001", outcome_id=outcome.outcome_id,
        outcome_digest=outcome.digest,
        contributions=(
            Contribution(ContributorKind.ACTION, action_id,
                         strength_for(bound_digest=receipt.digest,
                                      other_sufficient_contributors=False,
                                      dependency_demonstrated=True),
                         "the resolution is this execution's recorded effect",
                         bound_digest=receipt.digest),
            Contribution(ContributorKind.RECIPE_VERSION, recipe.key,
                         strength_for(bound_digest=evaluation.digest,
                                      other_sufficient_contributors=True,
                                      dependency_demonstrated=True),
                         "selected the subject; the resolution itself came from the source",
                         bound_digest=evaluation.digest),
            Contribution(ContributorKind.SOURCE, "county_tax_mailing_party",
                         strength_for(bound_digest=outcome_evidence.content_digest,
                                      other_sufficient_contributors=True,
                                      dependency_demonstrated=True),
                         "supplied the mailing party record", 
                         bound_digest=outcome_evidence.content_digest),
            Contribution(ContributorKind.SOURCE, "county_probate_index",
                         strength_for(bound_digest=None,
                                      other_sufficient_contributors=True,
                                      dependency_demonstrated=True),
                         "in the lineage; no demonstrated dependency for this outcome"),
        ),
        unexplained=("whether the administrator would have been found without the "
                     "probate signal",),
        attributed_at="2026-08-19T13:05:00Z")

    ledger.append(LoopEvent(
        event_type=LoopEventType.OUTCOME_ATTRIBUTED,
        identity=_ident(mode, subject=subject_ref),
        occurred_at="2026-08-19T13:05:00Z", observed_at="2026-08-19T13:05:00Z",
        reason="graded attribution across four contributors",
        payload={"attribution_id": attribution.attribution_id,
                 "attribution_digest": attribution.digest,
                 "strengths": {c.contributor_key: c.strength.value
                               for c in attribution.contributions},
                 "unexplained": list(attribution.unexplained)},
        causal_predecessors=(CausalClaim(outcome_event.event_id, CausalRelation.PRODUCED_BY,
                                         "attribution of this outcome"),)))

    # ---- CALIBRATION over a window ----------------------------------------
    pairs = _synthetic_window_pairs(recipe.key)
    calibration_report = calibrate(
        pairs, report_id="cal-0001", window_start="2026-05-01T00:00:00Z",
        window_end="2026-08-19T00:00:00Z", computed_at=now,
        dimensions=(CalibrationDimension.RECIPE, CalibrationDimension.SOURCE,
                    CalibrationDimension.OWNER_CLASS))
    recipe_cell = calibration_report.cell(CalibrationDimension.RECIPE, recipe.key)
    assert recipe_cell is not None

    ledger.append(LoopEvent(
        event_type=LoopEventType.CALIBRATION_RECORDED,
        identity=_ident(mode),
        occurred_at=now, observed_at=now,
        reason="scored the sealed forecasts in the window; mutates nothing",
        payload={"report_id": calibration_report.report_id,
                 "report_digest": calibration_report.digest,
                 "pair_count": calibration_report.pair_count,
                 "recipe_cell": recipe_cell.to_dict(),
                 "authority": calibration_report.authority}))

    # ---- SOURCE + RECIPE LEARNING ------------------------------------------
    source_ranking = rank_sources(build_cells(_synthetic_source_attempts()),
                                  owner_class="heirs_estate", computed_at=now)
    scorecard = score_recipe(
        RecipeObservationCounts(opportunities_produced=180, opportunities_accepted=142,
                                contactable=41, callable_permitted=33, pursued=31,
                                converted=9),
        recipe_key=recipe.key, window_start="2026-05-01T00:00:00Z",
        window_end="2026-08-19T00:00:00Z", computed_at=now)
    diagnosis = diagnose(scorecard)

    # ---- LEARNING PROPOSAL -------------------------------------------------
    first_source = source_ranking.recommended_first()
    assert first_source is not None
    proposal = propose(
        proposal_id="lup-0001", target_id="source_order:heirs_estate",
        target_kind="routing_config",
        current_version="source-order.v1",
        current_artifact_digest=_hexdigest("source-order.v1"),
        candidate_version="source-order.v2",
        candidate_artifact_digest=_hexdigest("source-order.v2"),
        evidence_window=EvidenceWindow(
            start="2026-05-01T00:00:00Z", end="2026-08-19T00:00:00Z",
            population="owner_class=heirs_estate", sample_count=len(pairs),
            holdout_count=12),
        changes=(ProposedChange(
            ChangeKind.SOURCE_ORDERING, "heirs_estate.first_source",
            "paid_skiptrace", first_source.source_type,
            "higher measured high-confidence yield at lower cost per resolved identity"),),
        expected_benefit=(ExpectedBenefit(
            "cost_per_resolved_identity", Decimal("1.3125"), Decimal("0.1250"),
            first_source.attempts),),
        regression_risk=RegressionRisk(
            risk_class="low", affected_populations=("owner_class=heirs_estate",),
            known_regressions=0, critical_regressions=0,
            unknown_risks=("county coverage of the mailing-party record is uneven",)),
        calibration=calibration_report, supporting_cell=recipe_cell,
        attributions=(attribution,), proposed_at=now)

    ledger.append(LoopEvent(
        event_type=LoopEventType.LEARNING_PROPOSED,
        identity=_ident(mode),
        occurred_at=now, observed_at=now,
        reason="measured source yield argues for a different first source",
        payload={"proposal_id": proposal.proposal_id, "target_id": proposal.target_id,
                 "current_version": proposal.current_version,
                 "candidate_version": proposal.candidate_version,
                 "proposal_digest": proposal.digest,
                 "sample_count": proposal.evidence_window.sample_count,
                 "recipe_diagnosis": diagnosis.to_dict(),
                 "source_ranking_digest": source_ranking.digest,
                 "recipe_scorecard_digest": scorecard.digest}))

    # ---- PROMOTION GATE ----------------------------------------------------
    prerequisites = PromotionPrerequisites(
        tests_passed=True, test_report_digest=_hexdigest("t24d-test-report"),
        regression_count=0, critical_regression_count=0,
        human_approval_digest=_hexdigest("operator-approval-t24d"),
        reversibility_contract_digest=_hexdigest("reversibility-source-order"),
        canary_evaluation_digest=None,
        evidence_modes=(mode,))
    policy = PromotionPolicy(policy_id="t24d.promotion.v1", min_sample_count=30)
    promotion_decision = evaluate_promotion(
        proposal, prerequisites, policy, decision_id="promo-0001",
        requested_stage=Stage.CANARY, decided_at=now)

    event_type = {
        PromotionOutcome.ELIGIBLE: LoopEventType.PROMOTION_ELIGIBLE,
        PromotionOutcome.HELD: LoopEventType.PROMOTION_HELD,
        PromotionOutcome.BLOCKED: LoopEventType.PROMOTION_BLOCKED,
    }[promotion_decision.outcome]

    ledger.append(LoopEvent(
        event_type=event_type, identity=_ident(mode),
        occurred_at=now, observed_at=now,
        reason=promotion_decision.reasons[0],
        payload={"decision_id": promotion_decision.decision_id,
                 "proposal_digest": promotion_decision.proposal_digest,
                 "requested_stage": promotion_decision.requested_stage.value,
                 "reasons": list(promotion_decision.reasons),
                 "deployment_authority": promotion_decision.deployment_authority}))

    ledger.verify()
    reconstruction = replay(ledger.events)

    return LoopArtifacts(
        ledger=ledger, world_state=state, world_delta=world_delta,
        evaluation=evaluation, decision=decision, intent=intent, receipt=receipt,
        execution_binding=execution_binding, forecast=forecast, outcome=outcome,
        attribution=attribution, calibration=calibration_report,
        source_ranking=source_ranking, recipe_scorecard=scorecard,
        recipe_diagnosis=diagnosis, proposal=proposal,
        promotion_decision=promotion_decision, reconstruction=reconstruction)


def _synthetic_window_pairs(recipe_key: str) -> list[ScoredPair]:
    """A 48-pair window: 0.70 predicted, ~0.54 observed — a real, modest gap."""
    pairs: list[ScoredPair] = []
    for index in range(48):
        actual = Decimal(1) if index % 25 < 13 else Decimal(0)
        pairs.append(ScoredPair(
            subject_id=f"subject:window-{index:03d}",
            forecast_digest=_hexdigest(f"fc-{index}"),
            outcome_digest=_hexdigest(f"out-{index}"),
            probability=Decimal("0.70"), actual=actual,
            forecast_recorded_at="2026-05-01T00:00:00Z",
            observed_at="2026-08-01T00:00:00Z",
            dimensions={"recipe": recipe_key, "owner_class": "heirs_estate",
                        "source": ("county_tax_mailing_party" if index % 2 == 0
                                   else "paid_skiptrace")}))
    return pairs


def _synthetic_source_attempts() -> list[SourceAttempt]:
    attempts: list[SourceAttempt] = []
    for index in range(40):
        attempts.append(SourceAttempt(
            subject_id=f"subject:src-a-{index:03d}", owner_class="heirs_estate",
            source_type="county_tax_mailing_party",
            identity_resolved=index < 32, high_confidence=index < 28,
            false_positive=index in (1, 2), manual_minutes=Decimal("3"),
            cost=Decimal("0.10"), attempted_at="2026-05-01T00:00:00Z"))
    for index in range(35):
        attempts.append(SourceAttempt(
            subject_id=f"subject:src-b-{index:03d}", owner_class="heirs_estate",
            source_type="paid_skiptrace",
            identity_resolved=index < 20, high_confidence=index < 10,
            false_positive=index == 0, manual_minutes=Decimal("9"),
            cost=Decimal("0.75"), attempted_at="2026-05-01T00:00:00Z"))
    for index in range(5):
        attempts.append(SourceAttempt(
            subject_id=f"subject:src-c-{index:03d}", owner_class="heirs_estate",
            source_type="obituary_index", identity_resolved=True,
            high_confidence=True, false_positive=False,
            manual_minutes=Decimal("1"), cost=Decimal("0"),
            attempted_at="2026-05-01T00:00:00Z"))
    return attempts
