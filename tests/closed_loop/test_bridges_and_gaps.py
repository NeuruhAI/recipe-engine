"""Projections stay honest across the seam; replay reports gaps rather than bridging them."""
from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest

from recipe_engine.closed_loop import identity as ident
from recipe_engine.closed_loop.bridges import (
    PRODUCER_ID, attribution_summary, to_017_summary_shape, to_019_proposal_shape,
    to_020_request_shape, to_outcome_record_v1,
)
from recipe_engine.closed_loop.calibration import CalibrationDimension
from recipe_engine.closed_loop.e2e import run_full_loop
from recipe_engine.closed_loop.events import (
    CausalClaim, CausalRelation, LoopEvent, LoopEventType,
)
from recipe_engine.closed_loop.identity import EventIdentity, EvidenceRef, WorldMode
from recipe_engine.closed_loop.ledger import LoopLedger
from recipe_engine.closed_loop.promotion import PromotionPrerequisites
from recipe_engine.closed_loop.replay import replay


@pytest.fixture(scope="module")
def artifacts():
    return run_full_loop(mode=WorldMode.LIVE)


class TestOutcomeRecordProjection:
    def projection(self, artifacts, mode=WorldMode.LIVE):
        return to_outcome_record_v1(
            artifacts.outcome, mode=mode, case_id="subject:parcel-37021-000123",
            entity={"parcel_key": "37021-000123"},
            forecast_probability=artifacts.forecast.probability,
            forecast_recorded_at=artifacts.forecast.recorded_at,
            forecast_version=artifacts.forecast.forecast_version,
            evidence_refs=("ev-mailing-party-1",))

    def test_the_projection_names_its_producer(self, artifacts):
        assert self.projection(artifacts)["producer_id"] == PRODUCER_ID

    def test_the_producer_never_asserts_calibration_eligibility(self, artifacts):
        assert self.projection(artifacts)["grading"]["calibration_eligible"] is False

    def test_mode_drives_the_test_and_synthetic_flags(self, artifacts):
        live = self.projection(artifacts, mode=WorldMode.LIVE)
        assert live["is_test"] is False and live["is_synthetic"] is False
        synthetic = self.projection(artifacts, mode=WorldMode.SYNTHETIC)
        assert synthetic["is_synthetic"] is True
        fixture = self.projection(artifacts, mode=WorldMode.FIXTURE)
        assert fixture["is_test"] is True and fixture["is_synthetic"] is False

    def test_the_decision_and_run_digests_cross_the_seam(self, artifacts):
        projection = self.projection(artifacts)
        assert projection["decision_receipt_digest"] == artifacts.decision.digest
        assert projection["run_receipt_digest"] == artifacts.receipt.digest
        assert projection["forecast"]["forecast_digest"] == artifacts.forecast.digest

    def test_the_pre_outcome_ordering_survives_the_projection(self, artifacts):
        projection = self.projection(artifacts)
        assert projection["forecast"]["recorded_at"] < projection["observed_at"]

    def test_the_three_tiers_are_preserved_separately(self, artifacts):
        result = self.projection(artifacts)["observed"]["result"]
        assert set(result) == {"process", "business", "world"}
        assert result["business"] == []
        assert result["process"] and result["world"]

    def test_absent_business_economics_project_as_none_not_zero(self, artifacts):
        economics = self.projection(artifacts)["economics"]
        assert economics["revenue"] is None and economics["cost"] is None


class TestPrimitiveProjections:
    def test_the_017_projection_omits_the_summary_digest_it_must_not_forge(self, artifacts):
        cell = artifacts.calibration.cell(CalibrationDimension.RECIPE,
                                          "graduation@graduation.v1")
        shape = to_017_summary_shape(artifacts.calibration, cell)
        assert "summary_digest" not in shape
        assert shape["count"] == cell.count

    def test_projecting_an_unscored_cell_is_refused(self, artifacts):
        cell = artifacts.calibration.cell(CalibrationDimension.SOURCE,
                                          "county_tax_mailing_party")
        thin = type(cell)(**{**cell.__dict__, "sufficient": False,
                             "insufficiency_reason": "below floor"})
        with pytest.raises(ValueError, match="not scored"):
            to_017_summary_shape(artifacts.calibration, thin)

    def test_the_019_projection_carries_versions_and_sample_count(self, artifacts):
        shape = to_019_proposal_shape(artifacts.proposal)
        assert shape["current_version"] != shape["candidate_version"]
        assert shape["sample_count"] == artifacts.proposal.evidence_window.sample_count
        assert shape["changes"] and shape["metric_projections"]

    def test_the_020_projection_carries_the_world_modes_020_cannot_see(self, artifacts):
        prerequisites = PromotionPrerequisites(
            tests_passed=True, test_report_digest="t" * 64, regression_count=0,
            critical_regression_count=0, human_approval_digest="h" * 64,
            reversibility_contract_digest="r" * 64, canary_evaluation_digest=None,
            evidence_modes=(WorldMode.LIVE, WorldMode.SYNTHETIC))
        shape = to_020_request_shape(artifacts.proposal, prerequisites,
                                     request_id="req-1", requested_stage="canary",
                                     requested_at="2026-08-19T00:00:00Z")
        assert shape["evidence_modes"] == ["live", "synthetic"]
        assert shape["non_live_evidence"] == ["synthetic"]

    def test_the_attribution_summary_is_ledger_safe(self, artifacts):
        summary = attribution_summary(artifacts.attribution)
        assert summary["learnable_count"] == 3
        assert "associated" in summary["strengths"]


def evidence(name="ev") -> EvidenceRef:
    return EvidenceRef(name, "src", "ptr", "2026-08-19T00:00:00Z",
                       hashlib.sha256(name.encode()).hexdigest())


def identity(**refs) -> EventIdentity:
    return EventIdentity(world=ident.world("w"), actor=ident.actor("a"),
                         mode=WorldMode.SYNTHETIC, **refs)


class TestReplayReportsGapsRatherThanBridgingThem:
    def test_an_outcome_with_no_action_is_reported_as_a_gap(self):
        ledger = LoopLedger()
        ledger.append(LoopEvent(
            event_type=LoopEventType.OUTCOME_OBSERVED,
            identity=identity(subject=ident.subject("s")),
            occurred_at="2026-08-19T00:00:00Z", observed_at="2026-08-19T00:00:00Z",
            reason="a good thing happened", evidence=(evidence(),),
            payload={"outcome_id": "out-1", "grade": "success"}))
        story = replay(ledger.events).subjects[0]
        assert any("no recorded action" in gap for gap in story.gaps)
        assert any("recorded, not attributed" in gap for gap in story.gaps)

    def test_a_proposal_with_no_promotion_decision_is_a_global_gap(self):
        ledger = LoopLedger()
        ledger.append(LoopEvent(
            event_type=LoopEventType.LEARNING_PROPOSED, identity=identity(),
            occurred_at="2026-08-19T00:00:00Z", observed_at="2026-08-19T00:00:00Z",
            reason="a candidate", payload={"proposal_id": "lup-1"}))
        reconstruction = replay(ledger.events)
        assert any("unresolved, not accepted" in gap
                   for gap in reconstruction.global_gaps)

    def test_a_declared_causal_link_closes_the_outcome_gap(self):
        ledger = LoopLedger()
        intent_event, _ = ledger.append(LoopEvent(
            event_type=LoopEventType.ACTION_INTENT_CREATED,
            identity=identity(run=ident.run("r"), subject=ident.subject("s"),
                              decision=ident.decision("d"), action=ident.action("a")),
            occurred_at="2026-08-19T00:00:00Z", observed_at="2026-08-19T00:00:00Z",
            reason="declared", payload={"action_id": "a"}))
        executed, _ = ledger.append(LoopEvent(
            event_type=LoopEventType.ACTION_EXECUTED,
            identity=identity(run=ident.run("r"), subject=ident.subject("s"),
                              decision=ident.decision("d"), action=ident.action("a")),
            occurred_at="2026-08-19T00:01:00Z", observed_at="2026-08-19T00:01:00Z",
            reason="executed", payload={"action_id": "a"},
            causal_predecessors=(CausalClaim(intent_event.event_id,
                                             CausalRelation.EXECUTED, "this intent"),)))
        ledger.append(LoopEvent(
            event_type=LoopEventType.OUTCOME_OBSERVED,
            identity=identity(subject=ident.subject("s")),
            occurred_at="2026-08-19T00:02:00Z", observed_at="2026-08-19T00:02:00Z",
            reason="observed", evidence=(evidence(),),
            payload={"outcome_id": "out-1"},
            causal_predecessors=(CausalClaim(executed.event_id,
                                             CausalRelation.OBSERVED_EFFECT_OF,
                                             "observed after this execution"),)))
        story = replay(ledger.events).subjects[0]
        assert not any("recorded, not attributed" in gap for gap in story.gaps)
