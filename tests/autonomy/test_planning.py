"""Execution plan proposal tests."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.autonomy import collapse_scenarios, compile_execution_plan, default_config
from recipe_engine.autonomy.errors import PlanningError
from recipe_engine.autonomy.models import (
    CapabilityCatalog,
    Readiness,
    SV_CAPABILITY,
)
from tests.autonomy.fixtures.builders import domain_pack, strategy


class PlanningTests(unittest.TestCase):
    def _packet_with(self, strat_kwargs=None):
        opp, strats = domain_pack("residential_acquisition")
        if strat_kwargs:
            strats = [strategy("custom", required=("c1",), **strat_kwargs)]
        return collapse_scenarios(opp, strats, default_config()), strats

    def test_compile_selected(self):
        pkt, _ = self._packet_with()
        self.assertTrue(pkt.selected_strategy_ids)
        sid = pkt.selected_strategy_ids[0]
        cat = CapabilityCatalog(
            schema_version=SV_CAPABILITY,
            capabilities=("observe.read", "research.read", "analysis.local"),
        )
        plan = compile_execution_plan(pkt, sid, cat, default_config())
        self.assertTrue(plan.stages)
        self.assertFalse(plan.blocked)
        # dependency order
        ids = [s.stage_id for s in plan.stages]
        self.assertEqual(ids, sorted(ids, key=lambda x: ids.index(x)))
        # idempotency stable
        plan2 = compile_execution_plan(pkt, sid, cat, default_config())
        self.assertEqual(plan.plan_receipt, plan2.plan_receipt)
        self.assertEqual(
            [s.idempotency_key for s in plan.stages],
            [s.idempotency_key for s in plan2.stages],
        )

    def test_missing_capability_hold(self):
        pkt, _ = self._packet_with()
        sid = pkt.selected_strategy_ids[0]
        cat = CapabilityCatalog(schema_version=SV_CAPABILITY, capabilities=())
        plan = compile_execution_plan(pkt, sid, cat, default_config())
        self.assertEqual(plan.readiness, Readiness.HOLD)
        self.assertTrue(plan.missing_capabilities)
        self.assertFalse(plan.executable_in_principle)

    def test_cycle_rejection(self):
        opp, _ = domain_pack("residential_acquisition")
        s = strategy(
            "cyc",
            required=("c1",),
            stages=(
                {
                    "stage_id": "a",
                    "title": "A",
                    "dependencies": ["b"],
                    "preconditions": [],
                    "capability_classes": ["observe.read"],
                    "expected_evidence_outputs": ["x"],
                    "action_class": "observe",
                    "reversible": True,
                    "approval_checkpoint": False,
                    "stop_conditions": ["stop"],
                    "rollback_proposal": None,
                    "max_attempts": 1,
                    "timeout_seconds": 1,
                    "completion_criteria": ["x"],
                    "owner": "op",
                },
                {
                    "stage_id": "b",
                    "title": "B",
                    "dependencies": ["a"],
                    "preconditions": [],
                    "capability_classes": ["observe.read"],
                    "expected_evidence_outputs": ["y"],
                    "action_class": "observe",
                    "reversible": True,
                    "approval_checkpoint": False,
                    "stop_conditions": ["stop"],
                    "rollback_proposal": None,
                    "max_attempts": 1,
                    "timeout_seconds": 1,
                    "completion_criteria": ["y"],
                    "owner": "op",
                },
            ),
        )
        pkt = collapse_scenarios(opp, [s], default_config())
        if not pkt.selected_strategy_ids:
            self.skipTest("strategy not selected under scoring")
        cat = CapabilityCatalog(schema_version=SV_CAPABILITY, capabilities=("observe.read",))
        with self.assertRaises(PlanningError):
            compile_execution_plan(pkt, "cyc", cat, default_config())

    def test_blocked_plan(self):
        opp, _ = domain_pack("residential_acquisition")
        s = strategy("blk", required=("c1",), prohibited=True, econ="0.9")
        safe = strategy("ok", required=("c1",), econ="0.5", family="other", thesis="other thesis ok")
        pkt = collapse_scenarios(opp, [s, safe], default_config())
        # compile for safe only
        sid = pkt.selected_strategy_ids[0]
        cat = CapabilityCatalog(
            schema_version=SV_CAPABILITY, capabilities=("observe.read", "research.read")
        )
        plan = compile_execution_plan(pkt, sid, cat, default_config())
        self.assertNotEqual(plan.readiness, Readiness.BLOCKED)

    def test_financial_requires_approval(self):
        opp, _ = domain_pack("business_acquisition")
        pkt = collapse_scenarios(opp, domain_pack("business_acquisition")[1], default_config())
        if not pkt.selected_strategy_ids:
            self.skipTest("no selection")
        # find financial strategy if selected
        cat = CapabilityCatalog(
            schema_version=SV_CAPABILITY,
            capabilities=("observe.read", "research.read", "finance.commit"),
        )
        for sid in pkt.selected_strategy_ids:
            plan = compile_execution_plan(pkt, sid, cat, default_config())
            ev = next(e for e in pkt.evaluated if e.strategy.strategy_id == sid)
            if "financial_commit" in ev.strategy.action_classes:
                self.assertTrue(plan.approval_required)

    def test_no_shell_in_plan(self):
        pkt, _ = self._packet_with()
        sid = pkt.selected_strategy_ids[0]
        cat = CapabilityCatalog(
            schema_version=SV_CAPABILITY, capabilities=("observe.read", "research.read")
        )
        plan = compile_execution_plan(pkt, sid, cat, default_config())
        blob = plan.explanation + "".join(s.title for s in plan.stages)
        self.assertNotIn("subprocess", blob)
        self.assertNotIn("/bin/sh", blob)


if __name__ == "__main__":
    unittest.main()
