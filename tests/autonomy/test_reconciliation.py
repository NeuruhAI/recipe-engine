"""Outcome reconciliation tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.autonomy import collapse_scenarios, default_config, reconcile_outcome
from recipe_engine.autonomy.models import (
    ActualMetric,
    ErrorClass,
    OutcomeRecord,
    PredictedMetric,
    SV_ACTUAL,
    SV_OUTCOME,
    SV_PREDICTED,
)
from tests.autonomy.fixtures.builders import domain_pack


def _pred(mid, val, unit="score", scale="0..1"):
    return PredictedMetric(
        schema_version=SV_PREDICTED,
        metric_id=mid,
        value=Decimal(val),
        unit=unit,
        scale=scale,
    )


def _act(mid, val, unit="score", scale="0..1", incomplete=False, evidence=("ev",)):
    return ActualMetric(
        schema_version=SV_ACTUAL,
        metric_id=mid,
        value=None if val is None else Decimal(val),
        unit=unit,
        scale=scale,
        evidence_refs=tuple(evidence),
        incomplete=incomplete,
    )


class ReconciliationTests(unittest.TestCase):
    def _packet(self):
        opp, strats = domain_pack("residential_acquisition")
        return collapse_scenarios(opp, strats, default_config())

    def test_exact_prediction(self):
        pkt = self._packet()
        sid = pkt.selected_strategy_ids[0]
        outcome = OutcomeRecord(
            schema_version=SV_OUTCOME,
            opportunity_id=pkt.opportunity.opportunity_id,
            scenario_id=sid,
            predicted=(_pred("econ.attractiveness", "0.7"),),
            actual=(_act("econ.attractiveness", "0.7"),),
            operator_decisions=(),
            external_shocks=(),
            execution_failed=False,
            governance_failed=False,
            incomplete=False,
            recorded_at="2026-07-19T00:00:00Z",
        )
        res = reconcile_outcome(pkt, outcome, default_config())
        self.assertTrue(res.promotion_prohibited)
        self.assertEqual(res.prediction_errors[0]["direction"], "exact")
        for p in res.calibration_proposals:
            self.assertTrue(p.promotion_prohibited)
            self.assertFalse(p.suggested_delta.get("apply_globally"))

    def test_over_under(self):
        pkt = self._packet()
        sid = pkt.selected_strategy_ids[0]
        over = OutcomeRecord(
            schema_version=SV_OUTCOME,
            opportunity_id=pkt.opportunity.opportunity_id,
            scenario_id=sid,
            predicted=(_pred("m", "0.5"),),
            actual=(_act("m", "0.8"),),
            operator_decisions=(),
            external_shocks=(),
            execution_failed=False,
            governance_failed=False,
            incomplete=False,
            recorded_at="t",
        )
        res = reconcile_outcome(pkt, over, default_config())
        self.assertEqual(res.prediction_errors[0]["direction"], "over")
        under = OutcomeRecord(
            schema_version=SV_OUTCOME,
            opportunity_id=pkt.opportunity.opportunity_id,
            scenario_id=sid,
            predicted=(_pred("m", "0.5"),),
            actual=(_act("m", "0.2"),),
            operator_decisions=(),
            external_shocks=(),
            execution_failed=False,
            governance_failed=False,
            incomplete=False,
            recorded_at="t",
        )
        res2 = reconcile_outcome(pkt, under, default_config())
        self.assertEqual(res2.prediction_errors[0]["direction"], "under")

    def test_unit_mismatch(self):
        pkt = self._packet()
        sid = pkt.selected_strategy_ids[0]
        outcome = OutcomeRecord(
            schema_version=SV_OUTCOME,
            opportunity_id=pkt.opportunity.opportunity_id,
            scenario_id=sid,
            predicted=(_pred("m", "1", unit="usd", scale="absolute"),),
            actual=(_act("m", "1", unit="eur", scale="absolute"),),
            operator_decisions=(),
            external_shocks=(),
            execution_failed=False,
            governance_failed=False,
            incomplete=False,
            recorded_at="t",
        )
        res = reconcile_outcome(pkt, outcome, default_config())
        self.assertTrue(any("unit_mismatch" in x for x in res.data_quality_findings))
        self.assertIn(ErrorClass.DATA_QUALITY, res.error_classes)

    def test_missing_outcome(self):
        pkt = self._packet()
        sid = pkt.selected_strategy_ids[0]
        outcome = OutcomeRecord(
            schema_version=SV_OUTCOME,
            opportunity_id=pkt.opportunity.opportunity_id,
            scenario_id=sid,
            predicted=(_pred("m", "1"),),
            actual=(),
            operator_decisions=(),
            external_shocks=(),
            execution_failed=False,
            governance_failed=False,
            incomplete=True,
            recorded_at="t",
        )
        res = reconcile_outcome(pkt, outcome, default_config())
        self.assertIn(ErrorClass.INCOMPLETE, res.error_classes)

    def test_execution_and_shock(self):
        pkt = self._packet()
        sid = pkt.selected_strategy_ids[0]
        outcome = OutcomeRecord(
            schema_version=SV_OUTCOME,
            opportunity_id=pkt.opportunity.opportunity_id,
            scenario_id=sid,
            predicted=(_pred("m", "1"),),
            actual=(_act("m", "1"),),
            operator_decisions=(),
            external_shocks=("storm",),
            execution_failed=True,
            governance_failed=False,
            incomplete=False,
            recorded_at="t",
        )
        res = reconcile_outcome(pkt, outcome, default_config())
        self.assertIn(ErrorClass.EXECUTION, res.error_classes)
        self.assertIn(ErrorClass.EXTERNAL_SHOCK, res.error_classes)

    def test_failed_assumption(self):
        pkt = self._packet()
        sid = pkt.selected_strategy_ids[0]
        ev = next(e for e in pkt.evaluated if e.strategy.strategy_id == sid)
        aid = ev.strategy.assumption_claim_ids[0] if ev.strategy.assumption_claim_ids else "c_a"
        outcome = OutcomeRecord(
            schema_version=SV_OUTCOME,
            opportunity_id=pkt.opportunity.opportunity_id,
            scenario_id=sid,
            predicted=(_pred("m", "1"),),
            actual=(_act("m", "0.5"),),
            operator_decisions=(),
            external_shocks=(),
            execution_failed=False,
            governance_failed=False,
            incomplete=False,
            recorded_at="t",
            notes=(f"failed_assumption:{aid}",),
        )
        res = reconcile_outcome(pkt, outcome, default_config())
        self.assertTrue(res.failed_assumptions)
        self.assertTrue(all(p.promotion_prohibited for p in res.calibration_proposals))
        self.assertTrue(all(not p.suggested_delta.get("apply_globally") for p in res.calibration_proposals))

    def test_one_outcome_not_global(self):
        pkt = self._packet()
        sid = pkt.selected_strategy_ids[0]
        outcome = OutcomeRecord(
            schema_version=SV_OUTCOME,
            opportunity_id=pkt.opportunity.opportunity_id,
            scenario_id=sid,
            predicted=(_pred("m", "0.5"),),
            actual=(_act("m", "0.9"),),
            operator_decisions=(),
            external_shocks=(),
            execution_failed=False,
            governance_failed=False,
            incomplete=False,
            recorded_at="t",
        )
        res = reconcile_outcome(pkt, outcome, default_config())
        for p in res.calibration_proposals:
            self.assertIn("strategy:", p.scope)
            self.assertFalse(p.suggested_delta.get("apply_globally", False))


if __name__ == "__main__":
    unittest.main()
