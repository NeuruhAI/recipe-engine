"""Scenario collapse, Pareto, max-four, determinism tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.autonomy import collapse_scenarios, default_config
from recipe_engine.autonomy.config import DEFAULT_WEIGHTS, EngineConfig, validate_config
from recipe_engine.autonomy.models import GovernanceProposalState
from recipe_engine.autonomy.serialization import canonical_json
from tests.autonomy.fixtures.builders import domain_pack, strategy, claim, evidence, opportunity
from recipe_engine.autonomy.models import ClaimClass, AuthorityLevel


class CollapseTests(unittest.TestCase):
    def test_clear_winner_selects_one_or_few(self):
        e1 = evidence("ev1", authority=AuthorityLevel.DOMAIN_AUTHORITY, claims=("c1",))
        e2 = evidence("ev2", source_type="other", claims=("c1",))
        c1 = claim("c1", evidence_refs=("ev1", "ev2"), classification=ClaimClass.CORROBORATED)
        opp = opportunity("o1", "residential_acquisition", [c1], [e1, e2], completeness="0.95")
        strong = strategy(
            "s_strong",
            family="alpha",
            thesis="clearly dominant complete reversible strong evidence path",
            required=("c1",),
            econ="0.95",
            capital="low",
            reversibility="reversible",
            time_profile="fast",
            failures=(),
            next_action="a",
        )
        weak = strategy(
            "s_weak",
            family="beta",
            thesis="weak incomplete path with many failure modes",
            required=("c1",),
            econ="0.2",
            capital="high",
            reversibility="irreversible",
            time_profile="slow",
            failures=("f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8"),
            next_action="b",
            gaps=("g1", "g2", "g3"),
        )
        pkt = collapse_scenarios(opp, [strong, weak], default_config())
        self.assertIn("s_strong", pkt.selected_strategy_ids)
        self.assertLessEqual(len(pkt.selected_strategy_ids), 4)
        self.assertFalse(pkt.no_decision)

    def test_max_four_hard(self):
        e1 = evidence("ev1", authority=AuthorityLevel.DOMAIN_AUTHORITY, claims=("c1",))
        e2 = evidence("ev2", source_type="other", claims=("c1",))
        c1 = claim("c1", evidence_refs=("ev1", "ev2"), classification=ClaimClass.CORROBORATED)
        opp = opportunity("o1", "d", [c1], [e1, e2], completeness="0.9")
        cands = [
            strategy(
                f"s{i}",
                family=f"fam{i}",
                thesis=f"unique thesis number {i} with distinct words {i*7}",
                required=("c1",),
                econ=str(0.4 + i * 0.05),
                next_action=f"act{i}",
                capital=["low", "medium", "high"][i % 3],
                time_profile=["fast", "medium", "slow"][i % 3],
                reversibility=["reversible", "partially_reversible", "irreversible"][i % 3],
            )
            for i in range(8)
        ]
        pkt = collapse_scenarios(opp, cands, default_config())
        self.assertLessEqual(len(pkt.selected_strategy_ids), 4)

    def test_blocked_high_return_not_selected(self):
        e1 = evidence("ev1", authority=AuthorityLevel.DOMAIN_AUTHORITY, claims=("c1",))
        e2 = evidence("ev2", source_type="other", claims=("c1",))
        c1 = claim("c1", evidence_refs=("ev1", "ev2"), classification=ClaimClass.CORROBORATED)
        opp = opportunity("o1", "d", [c1], [e1, e2], completeness="0.9")
        blocked = strategy(
            "s_block",
            family="x",
            thesis="illegal high return path",
            required=("c1",),
            econ="0.99",
            prohibited=True,
            next_action="hack",
        )
        safe = strategy(
            "s_safe",
            family="y",
            thesis="safe modest path",
            required=("c1",),
            econ="0.5",
            next_action="review",
        )
        pkt = collapse_scenarios(opp, [blocked, safe], default_config())
        self.assertNotIn("s_block", pkt.selected_strategy_ids)
        self.assertIn("s_safe", pkt.selected_strategy_ids)

    def test_duplicate_collapse(self):
        e1 = evidence("ev1", authority=AuthorityLevel.DOMAIN_AUTHORITY, claims=("c1",))
        e2 = evidence("ev2", source_type="other", claims=("c1",))
        c1 = claim("c1", evidence_refs=("ev1", "ev2"), classification=ClaimClass.CORROBORATED)
        opp = opportunity("o1", "d", [c1], [e1, e2], completeness="0.9")
        a = strategy(
            "s_a",
            family="same",
            thesis="identical thesis text for duplicate detection",
            required=("c1",),
            econ="0.7",
            next_action="same_action",
        )
        b = strategy(
            "s_b",
            family="same",
            thesis="identical thesis text for duplicate detection",
            required=("c1",),
            econ="0.71",
            next_action="same_action",
        )
        pkt = collapse_scenarios(opp, [a, b], default_config())
        reasons = {r["strategy_id"]: r["reason"] for r in pkt.rejected}
        self.assertTrue("duplicate" in reasons.values() or len(pkt.selected_strategy_ids) == 1)

    def test_deterministic_replay(self):
        opp, strats = domain_pack("residential_acquisition")
        cfg = default_config(fixed_timestamp="2026-07-19T00:00:00Z", id_salt="t")
        p1 = collapse_scenarios(opp, strats, cfg)
        p2 = collapse_scenarios(opp, list(reversed(strats)), cfg)
        self.assertEqual(p1.input_hash, p2.input_hash)
        self.assertEqual(p1.receipt_hash, p2.receipt_hash)
        self.assertEqual(p1.selected_strategy_ids, p2.selected_strategy_ids)
        self.assertEqual(p1.packet_id, p2.packet_id)

    def test_config_version_changes_hash(self):
        opp, strats = domain_pack("residential_acquisition")
        p1 = collapse_scenarios(opp, strats, default_config(config_version="v1"))
        p2 = collapse_scenarios(opp, strats, default_config(config_version="v2"))
        self.assertNotEqual(p1.config_hash, p2.config_hash)
        self.assertNotEqual(p1.receipt_hash, p2.receipt_hash)

    def test_canonical_json_key_order_invariant(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        self.assertEqual(canonical_json(a), canonical_json(b))

    def test_cross_domain_packs(self):
        for name in (
            "residential_acquisition",
            "vacant_land_lot_split",
            "small_multifamily",
            "business_acquisition",
            "estate_sale_signal",
            "non_re_ops",
        ):
            opp, strats = domain_pack(name)
            pkt = collapse_scenarios(opp, strats, default_config())
            self.assertLessEqual(len(pkt.selected_strategy_ids), 4)
            self.assertEqual(pkt.opportunity.domain, opp.domain)

    def test_insufficient_evidence_no_decision(self):
        e1 = evidence("ev1", claims=("c1",))
        c1 = claim("c1", evidence_refs=("ev1",), critical=True)
        # missing critical required claim on strategy
        opp = opportunity("o1", "d", [c1], [e1], completeness="0.1")
        s = strategy(
            "s1",
            required=("c_missing",),
            econ="0.9",
            gaps=("critical_gap",),
        )
        pkt = collapse_scenarios(opp, [s], default_config())
        self.assertTrue(pkt.no_decision or "s1" not in pkt.selected_strategy_ids)

    def test_unknown_policy_escalate_not_allow(self):
        e1 = evidence("ev1", authority=AuthorityLevel.DOMAIN_AUTHORITY, claims=("c1",))
        e2 = evidence("ev2", source_type="other", claims=("c1",))
        c1 = claim("c1", evidence_refs=("ev1", "ev2"), classification=ClaimClass.CORROBORATED)
        opp = opportunity("o1", "d", [c1], [e1, e2], completeness="0.9")
        s = strategy("s1", required=("c1",), policy_known=False, econ="0.8")
        pkt = collapse_scenarios(opp, [s], default_config())
        ev = next(e for e in pkt.evaluated if e.strategy.strategy_id == "s1")
        self.assertEqual(ev.governance.state, GovernanceProposalState.ESCALATE)

    def test_diversity_does_not_rescue_dominated_blocked(self):
        e1 = evidence("ev1", authority=AuthorityLevel.DOMAIN_AUTHORITY, claims=("c1",))
        e2 = evidence("ev2", source_type="other", claims=("c1",))
        c1 = claim("c1", evidence_refs=("ev1", "ev2"), classification=ClaimClass.CORROBORATED)
        opp = opportunity("o1", "d", [c1], [e1, e2], completeness="0.9")
        good = strategy("s_good", family="a", thesis="good path alpha", required=("c1",), econ="0.8")
        bad = strategy(
            "s_bad",
            family="b",
            thesis="blocked path beta",
            required=("c1",),
            econ="0.99",
            prohibited=True,
        )
        pkt = collapse_scenarios(opp, [good, bad], default_config())
        self.assertNotIn("s_bad", pkt.selected_strategy_ids)

    def test_risk_and_uncertainty_separate_dimensions(self):
        opp, strats = domain_pack("residential_acquisition")
        pkt = collapse_scenarios(opp, strats, default_config())
        for e in pkt.evaluated:
            self.assertIn("uncertainty", e.score.dimensions)
            self.assertIn("downside_risk", e.score.dimensions)
            self.assertIsNotNone(e.score.dimensions["uncertainty"].value)
            self.assertIsNotNone(e.score.dimensions["downside_risk"].value)


if __name__ == "__main__":
    unittest.main()
