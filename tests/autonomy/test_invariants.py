"""Property-style invariant tests for collapse."""

from __future__ import annotations

from itertools import permutations
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.autonomy import collapse_scenarios, default_config
from recipe_engine.autonomy.config import MAX_SELECTED
from recipe_engine.autonomy.models import GovernanceProposalState
from tests.autonomy.fixtures.builders import domain_pack


class InvariantTests(unittest.TestCase):
    def test_selected_count_0_to_4(self):
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
            n = len(pkt.selected_strategy_ids)
            self.assertGreaterEqual(n, 0)
            self.assertLessEqual(n, MAX_SELECTED)
            self.assertLessEqual(n, 4)

    def test_blocked_never_selected(self):
        for name in ("residential_acquisition", "business_acquisition"):
            opp, strats = domain_pack(name)
            pkt = collapse_scenarios(opp, strats, default_config())
            for e in pkt.evaluated:
                if e.governance.state == GovernanceProposalState.BLOCKED:
                    self.assertFalse(e.selected)

    def test_permutation_invariance(self):
        opp, strats = domain_pack("vacant_land_lot_split")
        cfg = default_config(id_salt="perm")
        base = collapse_scenarios(opp, strats, cfg)
        for perm in permutations(strats):
            pkt = collapse_scenarios(opp, list(perm), cfg)
            self.assertEqual(pkt.selected_strategy_ids, base.selected_strategy_ids)
            self.assertEqual(pkt.receipt_hash, base.receipt_hash)

    def test_no_duplicate_selected_fingerprints(self):
        opp, strats = domain_pack("residential_acquisition")
        pkt = collapse_scenarios(opp, strats, default_config())
        fps = [e.fingerprint for e in pkt.evaluated if e.selected]
        self.assertEqual(len(fps), len(set(fps)))


if __name__ == "__main__":
    unittest.main()
