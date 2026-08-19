"""Intent binds execution; forecasts seal before outcomes; outcomes stay tiered."""
from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest

from recipe_engine.closed_loop.action import (
    ActionError, ActionExecutionReceipt, ActionIntent, ExecutionStatus,
    ExpectedEffect, Reversibility, RiskClass, bind_execution,
)
from recipe_engine.closed_loop.decision import AuthorityRef
from recipe_engine.closed_loop.forecast import Forecast, ForecastError, ForecastedMetric
from recipe_engine.closed_loop.identity import EvidenceRef
from recipe_engine.closed_loop.outcome import (
    GENERIC_METRICS, LoopOutcomeRecord, OutcomeError, OutcomeGrade, OutcomeMetric,
    OutcomeObservation, OutcomeTier,
)


def authority() -> AuthorityRef:
    return AuthorityRef("auth:desk", "operator", "founder", "ledger://desk")


def evidence(name="ev") -> EvidenceRef:
    return EvidenceRef(name, "county", "ptr", "2026-08-19T00:00:00Z",
                       hashlib.sha256(name.encode()).hexdigest())


def intent(**overrides) -> ActionIntent:
    kwargs = dict(
        action_id="act-1", decision_id="dec-1", subject_id="subj-1",
        what="resolve mailing party", why_reason_codes=("identity_unknown",),
        target="county_tax_mailing_party", capability="public_record_read",
        expected_effects=(ExpectedEffect("identity_resolved", True),),
        risk=RiskClass.LOW, cost_estimate={"source_cost": "0.00"},
        reversibility=Reversibility.REVERSIBLE, authority=authority(),
        idempotency_key="idem-1", declared_at="2026-08-19T00:00:00Z")
    kwargs.update(overrides)
    return ActionIntent(**kwargs)


class TestActionIntent:
    def test_an_action_with_no_expected_effect_cannot_be_judged(self):
        with pytest.raises(ActionError, match="cannot be judged"):
            intent(expected_effects=())

    def test_an_action_must_say_why(self):
        with pytest.raises(ActionError, match="state why"):
            intent(why_reason_codes=())

    def test_an_irreversible_action_must_be_declared_high_risk(self):
        with pytest.raises(ActionError, match="high risk by construction"):
            intent(reversibility=Reversibility.IRREVERSIBLE, risk=RiskClass.LOW)

    def test_an_intent_must_carry_authority(self):
        with pytest.raises(ActionError, match="AuthorityRef"):
            intent(authority=None)


class TestExecutionBinding:
    def receipt(self, declared: ActionIntent, **overrides) -> ActionExecutionReceipt:
        kwargs = dict(
            receipt_id="rcpt-1", intent_digest=declared.digest, action_id="act-1",
            status=ExecutionStatus.EXECUTED, started_at="2026-08-19T00:01:00Z",
            finished_at="2026-08-19T00:02:00Z",
            observed_effects={"identity_resolved": True}, actual_cost={})
        kwargs.update(overrides)
        return ActionExecutionReceipt(**kwargs)

    def test_a_receipt_bound_to_a_different_intent_is_refused(self):
        declared = intent()
        other = intent(action_id="act-2", idempotency_key="idem-2")
        with pytest.raises(ActionError, match="action nobody authorised"):
            bind_execution(declared, self.receipt(other, action_id="act-2"))

    def test_binding_reports_each_expected_effect(self):
        declared = intent(expected_effects=(ExpectedEffect("identity_resolved", True),
                                            ExpectedEffect("phone_found", True)))
        result = bind_execution(declared, self.receipt(declared))
        assert result["unobserved_effects"] == ["phone_found"]
        matched = {e["fact_key"]: e["matched"] for e in result["effects"]}
        assert matched == {"identity_resolved": True, "phone_found": False}

    def test_a_failed_execution_must_state_error_codes(self):
        declared = intent()
        with pytest.raises(ActionError, match="must state error codes"):
            self.receipt(declared, status=ExecutionStatus.FAILED, error_codes=())

    def test_an_executed_action_may_not_carry_error_codes(self):
        declared = intent()
        with pytest.raises(ActionError, match="may not also carry error codes"):
            self.receipt(declared, error_codes=("timeout",))


class TestForecastSealing:
    def forecast(self, **overrides) -> Forecast:
        kwargs = dict(
            forecast_id="fc-1", subject_id="subj-1", decision_id="dec-1",
            recipe_key="graduation@v1", probability=Decimal("0.7"),
            outcome_metric_key="identity_resolved", horizon_days=30,
            metrics=(ForecastedMetric("operator_minutes", Decimal("6"), "minutes", 30),),
            recorded_at="2026-08-01T00:00:00Z", forecast_version="fc.v1")
        kwargs.update(overrides)
        return Forecast(**kwargs)

    def test_an_unsealed_forecast_has_no_digest(self):
        with pytest.raises(ForecastError, match="can still be edited"):
            _ = self.forecast().digest

    def test_sealing_fixes_the_content(self):
        sealed = self.forecast().seal()
        assert sealed.is_sealed and len(sealed.digest) == 64

    def test_editing_a_sealed_forecast_breaks_the_seal(self):
        sealed = self.forecast().seal()
        tampered = Forecast(**{**sealed.__dict__, "probability": Decimal("0.9")})
        assert not tampered.is_sealed

    def test_hindsight_is_refused(self):
        sealed = self.forecast(recorded_at="2026-08-19T00:00:00Z").seal()
        with pytest.raises(ForecastError, match="not pre-outcome"):
            sealed.assert_pre_outcome("2026-08-18T00:00:00Z")

    def test_a_simultaneous_forecast_is_also_refused(self):
        sealed = self.forecast(recorded_at="2026-08-19T00:00:00Z").seal()
        with pytest.raises(ForecastError, match="not strictly before"):
            sealed.assert_pre_outcome("2026-08-19T00:00:00Z")

    def test_a_pre_outcome_forecast_passes(self):
        assert self.forecast().seal().assert_pre_outcome("2026-08-19T00:00:00Z")

    def test_a_zero_horizon_is_refused(self):
        with pytest.raises(ForecastError, match="positive"):
            self.forecast(horizon_days=0)


class TestOutcomeTiers:
    def process_observation(self) -> OutcomeObservation:
        return OutcomeObservation("obs-1", OutcomeTier.PROCESS, "lookup_completed",
                                  True, "2026-08-19T00:00:00Z",
                                  metrics=(OutcomeMetric("operator_minutes",
                                                         Decimal("4"), "minutes"),))

    def test_process_completion_needs_no_external_evidence(self):
        assert self.process_observation().tier is OutcomeTier.PROCESS

    def test_a_business_outcome_without_evidence_is_refused(self):
        with pytest.raises(OutcomeError, match="process completion is not evidence"):
            OutcomeObservation("obs-2", OutcomeTier.BUSINESS, "contract_closed", True,
                               "2026-08-19T00:00:00Z")

    def test_a_world_outcome_without_evidence_is_refused(self):
        with pytest.raises(OutcomeError, match="requires evidence"):
            OutcomeObservation("obs-3", OutcomeTier.WORLD, "property_sold", True,
                               "2026-08-19T00:00:00Z")

    def test_a_business_outcome_with_evidence_is_accepted(self):
        observation = OutcomeObservation("obs-4", OutcomeTier.BUSINESS, "appointment_set",
                                         True, "2026-08-19T00:00:00Z",
                                         evidence=(evidence(),))
        assert observation.evidence

    def test_the_generic_metric_vocabulary_carries_no_domain_economics(self):
        assert "gross_margin" in GENERIC_METRICS and "revenue" in GENERIC_METRICS
        assert not any("deed" in k or "parcel" in k or "apn" in k for k in GENERIC_METRICS)

    def record(self, **overrides) -> LoopOutcomeRecord:
        kwargs = dict(
            outcome_id="out-1", subject_id="subj-1", decision_id="dec-1",
            decision_digest="a" * 64, action_receipt_digest="b" * 64,
            forecast_digest="c" * 64, observations=(self.process_observation(),),
            grade=OutcomeGrade.SUCCESS, grader_role="desk",
            graded_at="2026-08-19T01:00:00Z", observed_at="2026-08-19T01:00:00Z")
        kwargs.update(overrides)
        return LoopOutcomeRecord(**kwargs)

    def test_a_process_only_record_reports_no_business_outcome(self):
        assert self.record().has_business_outcome() is False

    def test_a_failure_must_name_its_lost_reason(self):
        with pytest.raises(OutcomeError, match="lost_reason_code"):
            self.record(grade=OutcomeGrade.FAILURE)

    def test_calibration_blockers_are_stated_not_implied(self):
        blockers = self.record(forecast_digest=None, grader_role=None,
                               graded_at=None).calibration_blockers()
        assert any("forecast" in b for b in blockers)
        assert any("human grade" in b for b in blockers)

    def test_a_fully_bound_record_reports_no_blockers(self):
        assert self.record().calibration_blockers() == ()

    def test_a_record_cannot_predate_what_it_contains(self):
        with pytest.raises(OutcomeError, match="cannot predate"):
            self.record(observed_at="2026-08-18T00:00:00Z")
