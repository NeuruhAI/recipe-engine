"""Learning must show numbers; promotion never grants deployment authority."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from recipe_engine.closed_loop.attribution import (
    AttributionSet, AttributionStrength, Contribution, ContributorKind,
)
from recipe_engine.closed_loop.calibration import (
    CalibrationDimension, ScoredPair, calibrate,
)
from recipe_engine.closed_loop.identity import WorldMode
from recipe_engine.closed_loop.learning import (
    AUTO_APPLY_FORBIDDEN, BLACK_DOCTRINE_SOURCE, ChangeKind, EvidenceWindow,
    ExpectedBenefit, LearningProposal, LearningRefusal, MIN_SAMPLES_FOR_CLAIM,
    MIN_SAMPLES_FOR_WEIGHT_CHANGE, ProposedChange, RegressionRisk, propose,
)
from recipe_engine.closed_loop.promotion import (
    PromotionError, PromotionOutcome, PromotionPolicy, PromotionPrerequisites, Stage,
    evaluate_promotion, promote,
)


def report_and_cell(count=120):
    pairs = [ScoredPair(f"s{i}", "f" * 64, "o" * 64, Decimal("0.8"),
                        Decimal(1) if i % 4 == 0 else Decimal(0),
                        "2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z",
                        {"recipe": "graduation@v1"}) for i in range(count)]
    report = calibrate(pairs, report_id="r", window_start="2026-01-01T00:00:00Z",
                       window_end="2026-06-30T00:00:00Z", computed_at="2026-08-19T00:00:00Z")
    return report, report.cell(CalibrationDimension.RECIPE, "graduation@v1")


def window(**overrides) -> EvidenceWindow:
    kwargs = dict(start="2026-01-01T00:00:00Z", end="2026-06-30T00:00:00Z",
                  population="owner_class=heirs_estate", sample_count=120,
                  holdout_count=20)
    kwargs.update(overrides)
    return EvidenceWindow(**kwargs)


def ordering_change() -> ProposedChange:
    return ProposedChange(ChangeKind.SOURCE_ORDERING, "heirs_estate.first_source",
                          "paid_skiptrace", "county_tax_mailing_party", "higher yield")


def weight_change() -> ProposedChange:
    return ProposedChange(ChangeKind.RECIPE_WEIGHT_ADJUSTMENT, "distress.weight",
                          "0.5", "0.6", "distress under-weighted")


def benefit() -> ExpectedBenefit:
    return ExpectedBenefit("cost_per_resolved_identity", Decimal("1.31"),
                           Decimal("0.13"), 40)


def risk(**overrides) -> RegressionRisk:
    kwargs = dict(risk_class="low", affected_populations=("heirs_estate",),
                  known_regressions=0, critical_regressions=0)
    kwargs.update(overrides)
    return RegressionRisk(**kwargs)


def make_proposal(**overrides) -> LearningProposal:
    report, cell = report_and_cell()
    kwargs = dict(
        proposal_id="lup-1", target_id="source_order:heirs_estate",
        target_kind="routing_config", current_version="v1",
        current_artifact_digest="a" * 64, candidate_version="v2",
        candidate_artifact_digest="b" * 64, evidence_window=window(),
        changes=(ordering_change(),), expected_benefit=(benefit(),),
        regression_risk=risk(), calibration=report, supporting_cell=cell,
        attributions=(), proposed_at="2026-08-19T00:00:00Z")
    kwargs.update(overrides)
    return propose(**kwargs)


class TestAntiHallucination:
    def test_the_black_doctrine_floors_are_the_ported_ones(self):
        assert MIN_SAMPLES_FOR_CLAIM == 30
        assert MIN_SAMPLES_FOR_WEIGHT_CHANGE == 100
        assert AUTO_APPLY_FORBIDDEN is True
        assert "NO_FIT_LT_30" in BLACK_DOCTRINE_SOURCE

    def test_a_proposal_with_no_expected_benefit_is_refused(self):
        with pytest.raises(LearningRefusal, match="expected benefit"):
            make_proposal(expected_benefit=())

    def test_an_expected_benefit_measured_on_nothing_is_an_opinion(self):
        with pytest.raises(LearningRefusal, match="opinion"):
            ExpectedBenefit("x", Decimal(1), Decimal(2), 0)

    def test_a_change_to_the_same_value_is_not_a_change(self):
        with pytest.raises(LearningRefusal, match="not a change"):
            ProposedChange(ChangeKind.SOURCE_ORDERING, "k", "a", "a", "r")

    def test_below_the_claim_floor_is_refused(self):
        with pytest.raises(LearningRefusal, match="insufficient evidence"):
            make_proposal(evidence_window=window(sample_count=12))

    def test_a_weight_change_needs_the_higher_floor(self):
        with pytest.raises(LearningRefusal, match="weight-class"):
            make_proposal(changes=(weight_change(),),
                          evidence_window=window(sample_count=60))

    def test_a_weight_change_passes_at_the_higher_floor(self):
        proposal = make_proposal(changes=(weight_change(),),
                                 evidence_window=window(sample_count=120))
        assert proposal.weight_class and proposal.required_samples == 100

    def test_a_holdout_is_required(self):
        with pytest.raises(LearningRefusal, match="learned the sample"):
            make_proposal(evidence_window=window(holdout_count=0))

    def test_an_unscored_supporting_cell_is_refused(self):
        thin_report, thin_cell = report_and_cell(count=5)
        with pytest.raises(LearningRefusal, match="is not scored"):
            make_proposal(calibration=thin_report, supporting_cell=thin_cell)

    def test_the_supporting_cell_must_be_in_the_cited_report(self):
        report, cell = report_and_cell()
        foreign = replace(cell, key="a-key-this-report-never-scored")
        with pytest.raises(LearningRefusal, match="not present in the cited report"):
            make_proposal(calibration=report, supporting_cell=foreign)

    def test_associated_only_attribution_is_not_evidence_of_effect(self):
        associated = AttributionSet(
            "attr-1", "out-1", "c" * 64,
            (Contribution(ContributorKind.SOURCE, "s1", AttributionStrength.ASSOCIATED,
                          "in the lineage"),),
            (), "2026-08-19T00:00:00Z")
        with pytest.raises(LearningRefusal, match="not evidence of effect"):
            make_proposal(attributions=(associated,))

    def test_a_proposal_never_carries_authority_or_auto_apply(self):
        proposal = make_proposal()
        assert proposal.authority == "NONE" and proposal.auto_apply is False

    def test_an_auto_apply_proposal_is_refused(self):
        with pytest.raises(LearningRefusal, match="never applies"):
            LearningProposal(
                proposal_id="p", target_id="t", target_kind="routing_config",
                current_version="v1", current_artifact_digest="a" * 64,
                candidate_version="v2", candidate_artifact_digest="b" * 64,
                evidence_window=window(), changes=(ordering_change(),),
                expected_benefit=(benefit(),), regression_risk=risk(),
                calibration_report_digest="d" * 64,
                supporting_attribution_digests=(), proposed_at="2026-08-19T00:00:00Z",
                auto_apply=True)


def prerequisites(**overrides) -> PromotionPrerequisites:
    kwargs = dict(tests_passed=True, test_report_digest="t" * 64, regression_count=0,
                  critical_regression_count=0, human_approval_digest="h" * 64,
                  reversibility_contract_digest="r" * 64,
                  canary_evaluation_digest=None, evidence_modes=(WorldMode.LIVE,))
    kwargs.update(overrides)
    return PromotionPrerequisites(**kwargs)


def decide(*, proposal=None, prereqs=None, stage=Stage.CANARY, policy=None):
    return evaluate_promotion(
        proposal or make_proposal(), prereqs or prerequisites(),
        policy or PromotionPolicy(policy_id="p.v1"), decision_id="promo-1",
        requested_stage=stage, decided_at="2026-08-19T00:00:00Z")


class TestPromotionGate:
    def test_live_evidence_with_every_prerequisite_is_eligible(self):
        decision = decide()
        assert decision.outcome is PromotionOutcome.ELIGIBLE

    def test_eligibility_never_grants_deployment_authority(self):
        assert decide().deployment_authority is False

    def test_a_decision_claiming_deployment_authority_cannot_be_constructed(self):
        from recipe_engine.closed_loop.promotion import PromotionDecision
        with pytest.raises(PromotionError, match="never grants deployment authority"):
            PromotionDecision("d", "p" * 64, "pol", "v", Stage.CANARY,
                              PromotionOutcome.ELIGIBLE, ("ok",), "2026-08-19T00:00:00Z",
                              deployment_authority=True)

    @pytest.mark.parametrize("mode", [WorldMode.SYNTHETIC, WorldMode.FIXTURE,
                                      WorldMode.SHADOW])
    def test_non_live_lineage_is_held_not_eligible(self, mode):
        decision = decide(prereqs=prerequisites(evidence_modes=(mode,)))
        assert decision.outcome is PromotionOutcome.HELD
        assert any("non-live" in reason for reason in decision.reasons)

    def test_a_single_non_live_source_taints_a_mixed_lineage(self):
        decision = decide(prereqs=prerequisites(
            evidence_modes=(WorldMode.LIVE, WorldMode.SYNTHETIC)))
        assert decision.outcome is PromotionOutcome.HELD

    def test_missing_human_approval_holds(self):
        decision = decide(prereqs=prerequisites(human_approval_digest=None))
        assert decision.outcome is PromotionOutcome.HELD
        assert any("human approval" in r for r in decision.reasons)

    def test_missing_reversibility_holds(self):
        decision = decide(prereqs=prerequisites(reversibility_contract_digest=None))
        assert any("reversibility" in r for r in decision.reasons)

    def test_failing_tests_hold(self):
        decision = decide(prereqs=prerequisites(tests_passed=False))
        assert any("tests did not pass" in r for r in decision.reasons)

    def test_a_critical_regression_blocks_rather_than_holds(self):
        decision = decide(prereqs=prerequisites(regression_count=2,
                                                critical_regression_count=1))
        assert decision.outcome is PromotionOutcome.BLOCKED

    def test_production_stage_requires_a_canary(self):
        decision = decide(stage=Stage.PRODUCTION)
        assert decision.outcome is PromotionOutcome.HELD
        assert any("canary" in r for r in decision.reasons)

    def test_a_disallowed_stage_blocks(self):
        policy = PromotionPolicy(policy_id="p.v1", allowed_stages=(Stage.SANDBOX,))
        decision = decide(stage=Stage.PRODUCTION, policy=policy)
        assert decision.outcome is PromotionOutcome.BLOCKED

    def test_evidence_modes_must_be_stated(self):
        with pytest.raises(PromotionError, match="must be stated"):
            prerequisites(evidence_modes=())


class TestVersionPromotion:
    def test_promotion_requires_an_eligible_decision(self):
        held = decide(prereqs=prerequisites(evidence_modes=(WorldMode.SYNTHETIC,)))
        with pytest.raises(PromotionError, match="cannot promote on a held decision"):
            promote(held, promotion_id="pr-1", target_id="t", from_version="v1",
                    to_version="v2", external_approval_ref="approval://x",
                    canary_evaluation_digest=None, promoted_at="2026-08-19T00:00:00Z",
                    rollback_ref="rollback/x")

    def test_promotion_requires_an_external_approval_reference(self):
        with pytest.raises(PromotionError, match="eligibility is not approval"):
            promote(decide(), promotion_id="pr-1", target_id="t", from_version="v1",
                    to_version="v2", external_approval_ref="  ",
                    canary_evaluation_digest=None, promoted_at="2026-08-19T00:00:00Z",
                    rollback_ref="rollback/x")

    def test_an_eligible_decision_plus_external_approval_records_a_promotion(self):
        record = promote(decide(), promotion_id="pr-1", target_id="t",
                         from_version="v1", to_version="v2",
                         external_approval_ref="approval://founder/2026-08-19",
                         canary_evaluation_digest="c" * 64,
                         promoted_at="2026-08-19T00:00:00Z", rollback_ref="rollback/x")
        assert record.to_version == "v2" and record.rollback_ref == "rollback/x"

    def test_a_promotion_must_change_the_version_in_force(self):
        with pytest.raises(PromotionError, match="must change the version"):
            promote(decide(), promotion_id="pr-1", target_id="t", from_version="v1",
                    to_version="v1", external_approval_ref="approval://x",
                    canary_evaluation_digest=None, promoted_at="2026-08-19T00:00:00Z",
                    rollback_ref="rollback/x")
