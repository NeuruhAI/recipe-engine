"""The full-loop court.

WORLD → SIGNAL → RECIPE EVALUATION → OPPORTUNITY → DECISION → GOVERNED ACTION
INTENT → EXECUTION RECEIPT → OBSERVED OUTCOME → ATTRIBUTION → CALIBRATION →
LEARNING PROPOSAL → PROMOTION GATE → CANDIDATE VERSION.

Two runs, one assertion between them: the synthetic run traverses every stage
and stops at PROMOTION_HELD, and the live-labelled run over the identical path
reaches PROMOTION_ELIGIBLE. That pairing is what proves the HELD is the
world-mode rule biting rather than the loop being unable to finish.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from recipe_engine.closed_loop.attribution import AttributionStrength, ContributorKind
from recipe_engine.closed_loop.calibration import CalibrationDimension
from recipe_engine.closed_loop.e2e import CURRENT_RECIPE_VERSION, run_full_loop
from recipe_engine.closed_loop.events import LoopEventType
from recipe_engine.closed_loop.identity import WorldMode
from recipe_engine.closed_loop.ledger import LoopLedger
from recipe_engine.closed_loop.outcome import OutcomeTier
from recipe_engine.closed_loop.promotion import PromotionOutcome, Stage, promote
from recipe_engine.closed_loop.recipe_learning import Track
from recipe_engine.closed_loop.replay import replay
from recipe_engine.closed_loop.world_state import EpistemicStatus


@pytest.fixture(scope="module")
def synthetic():
    return run_full_loop(mode=WorldMode.SYNTHETIC)


@pytest.fixture(scope="module")
def live():
    return run_full_loop(mode=WorldMode.LIVE)


class TestEveryStageIsTraversed:
    def test_the_whole_event_family_of_one_pass_is_present_in_order(self, synthetic):
        observed = [e.event_type for e in synthetic.ledger.events]
        assert observed == [
            LoopEventType.SOURCE_OBSERVED,
            LoopEventType.SIGNAL_NORMALIZED,
            LoopEventType.RECIPE_EVALUATED,
            LoopEventType.OPPORTUNITY_CREATED,
            LoopEventType.DECISION_PROPOSED,
            LoopEventType.DECISION_APPROVED,
            LoopEventType.FORECAST_SEALED,
            LoopEventType.ACTION_INTENT_CREATED,
            LoopEventType.ACTION_EXECUTED,
            LoopEventType.OUTCOME_OBSERVED,
            LoopEventType.OUTCOME_ATTRIBUTED,
            LoopEventType.CALIBRATION_RECORDED,
            LoopEventType.LEARNING_PROPOSED,
            LoopEventType.PROMOTION_HELD,
        ]

    def test_the_chain_verifies(self, synthetic):
        assert synthetic.ledger.verify()

    def test_every_event_carries_the_run_mode(self, synthetic):
        assert {e.identity.mode for e in synthetic.ledger.events} == {WorldMode.SYNTHETIC}


class TestWorldStateHonesty:
    def test_inference_stays_inference(self, synthetic):
        belief = synthetic.world_state.beliefs["likely_motivated_seller"]
        assert belief.status is EpistemicStatus.INFERRED

    def test_the_unknown_is_named_rather_than_absent(self, synthetic):
        assert "owner_reachable" in synthetic.world_state.unknowns()

    def test_the_external_gate_names_who_closes_it(self, synthetic):
        gate = synthetic.world_state.beliefs["estate_administrator"]
        assert gate.status is EpistemicStatus.EXTERNAL_GATE
        assert "clerk of court" in gate.basis


class TestDecisionAndAction:
    def test_the_decision_binds_the_exact_evaluation_that_produced_it(self, synthetic):
        assert synthetic.decision.recipe_evaluation_digest == synthetic.evaluation.digest

    def test_the_evaluation_pins_the_recipe_version(self, synthetic):
        assert synthetic.evaluation.recipe.version == CURRENT_RECIPE_VERSION

    def test_the_execution_binds_to_the_declared_intent(self, synthetic):
        assert synthetic.execution_binding["intent_digest"] == synthetic.intent.digest
        assert synthetic.execution_binding["unobserved_effects"] == []

    def test_the_forecast_was_sealed_before_the_outcome(self, synthetic):
        assert synthetic.forecast.assert_pre_outcome(synthetic.outcome.observed_at)


class TestOutcomeAndAttribution:
    def test_no_business_outcome_is_invented(self, synthetic):
        assert synthetic.outcome.has_business_outcome() is False
        assert synthetic.outcome.tier(OutcomeTier.PROCESS)
        assert synthetic.outcome.tier(OutcomeTier.WORLD)

    def test_the_world_outcome_carries_evidence(self, synthetic):
        assert all(o.evidence for o in synthetic.outcome.tier(OutcomeTier.WORLD))

    def test_the_outcome_binds_decision_execution_and_forecast(self, synthetic):
        assert synthetic.outcome.decision_digest == synthetic.decision.digest
        assert synthetic.outcome.action_receipt_digest == synthetic.receipt.digest
        assert synthetic.outcome.forecast_digest == synthetic.forecast.digest
        assert synthetic.outcome.calibration_blockers() == ()

    def test_attribution_is_graded_across_multiple_contributors(self, synthetic):
        strengths = {c.contributor_key: c.strength
                     for c in synthetic.attribution.contributions}
        assert strengths["act-0001"] is AttributionStrength.DIRECT
        assert strengths["county_probate_index"] is AttributionStrength.ASSOCIATED
        assert len(synthetic.attribution.contributors_of(ContributorKind.SOURCE)) == 2

    def test_the_unexplained_remainder_is_recorded(self, synthetic):
        assert synthetic.attribution.unexplained


class TestLearning:
    def test_calibration_grants_no_authority(self, synthetic):
        assert synthetic.calibration.authority == "NONE"
        assert synthetic.calibration.mutates_production is False

    def test_the_recipe_cell_is_scored_over_a_real_window(self, synthetic):
        cell = synthetic.calibration.cell(CalibrationDimension.RECIPE,
                                          f"graduation@{CURRENT_RECIPE_VERSION}")
        assert cell.sufficient and cell.count == 48
        assert cell.calibration_gap < 0

    def test_the_recipe_diagnosis_names_contactability_not_a_bad_recipe(self, synthetic):
        assert synthetic.recipe_diagnosis.constraint is Track.CONTACTABILITY

    def test_the_source_recommendation_comes_from_measured_cells(self, synthetic):
        first = synthetic.source_ranking.recommended_first()
        assert first.source_type == "county_tax_mailing_party" and first.sufficient
        assert [c.source_type for c in synthetic.source_ranking.unranked] == ["obituary_index"]

    def test_the_proposal_reaches_a_candidate_version_and_no_further(self, synthetic):
        assert synthetic.proposal.candidate_version == "source-order.v2"
        assert synthetic.proposal.authority == "NONE"
        assert synthetic.proposal.auto_apply is False


class TestTheLoopStopsWhereItMust:
    def test_a_synthetic_run_is_held_at_the_gate(self, synthetic):
        assert synthetic.promotion_decision.outcome is PromotionOutcome.HELD
        assert any("non-live" in r for r in synthetic.promotion_decision.reasons)

    def test_a_held_run_cannot_be_promoted(self, synthetic):
        from recipe_engine.closed_loop.promotion import PromotionError
        with pytest.raises(PromotionError, match="cannot promote on a held decision"):
            promote(synthetic.promotion_decision, promotion_id="pr-x", target_id="t",
                    from_version="v1", to_version="v2",
                    external_approval_ref="approval://x", canary_evaluation_digest=None,
                    promoted_at="2026-08-19T00:00:00Z", rollback_ref="rollback/x")

    def test_nothing_in_the_run_promoted_a_version(self, synthetic):
        assert synthetic.ledger.of_type(LoopEventType.VERSION_PROMOTED) == ()
        assert synthetic.reconstruction.versions_promoted == ()

    def test_the_identical_live_path_reaches_eligible(self, live):
        assert live.promotion_decision.outcome is PromotionOutcome.ELIGIBLE

    def test_eligibility_still_grants_no_deployment_authority(self, live):
        assert live.promotion_decision.deployment_authority is False

    def test_the_two_runs_differ_only_in_mode(self, synthetic, live):
        assert synthetic.decision.decision_id == live.decision.decision_id
        assert synthetic.proposal.candidate_version == live.proposal.candidate_version
        assert synthetic.promotion_decision.outcome is not live.promotion_decision.outcome


class TestReplay:
    def test_the_story_of_the_subject_is_reconstructable(self, synthetic):
        story = synthetic.reconstruction.subjects[0]
        assert story.why_opportunity_existed
        assert story.recipe_evaluations and story.decisions
        assert story.actions and story.outcomes and story.attributions
        assert story.sources_observed == ("county_probate_index", "county_tax_roll")

    def test_no_gaps_are_reported_for_a_fully_declared_loop(self, synthetic):
        assert synthetic.reconstruction.subjects[0].gaps == ()
        assert synthetic.reconstruction.global_gaps == ()

    def test_replay_is_deterministic(self, synthetic):
        assert replay(synthetic.ledger.events).digest == synthetic.reconstruction.digest

    def test_replay_of_a_reloaded_ledger_is_identical(self, tmp_path):
        path = tmp_path / "loop.jsonl"
        first = run_full_loop(ledger_path=path)
        reloaded = LoopLedger(path)
        assert reloaded.verify()
        assert replay(reloaded.events).digest == first.reconstruction.digest

    def test_re_running_the_loop_over_an_existing_ledger_appends_nothing(self, tmp_path):
        path = tmp_path / "loop.jsonl"
        first = run_full_loop(ledger_path=path)
        second = run_full_loop(ledger_path=path)
        assert len(second.ledger) == len(first.ledger)
        assert second.reconstruction.digest == first.reconstruction.digest

    def test_the_reconstruction_reports_the_mode_it_ran_in(self, synthetic, live):
        assert synthetic.reconstruction.modes == ("synthetic",)
        assert live.reconstruction.modes == ("live",)
