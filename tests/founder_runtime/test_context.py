"""Context precedence tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.context.selector import select_packs
from recipe_engine.founder_runtime.context.precedence import sort_by_precedence, governance_binding_blocks_override, precedence_rank
from recipe_engine.founder_runtime.context.registry import fixture_registry
from recipe_engine.founder_runtime.contracts.context import PRECEDENCE_ORDER


class ContextTests(unittest.TestCase):
    def test_precedence_order(self):
        self.assertEqual(PRECEDENCE_ORDER[0], "explicit_founder_instruction")
        self.assertEqual(PRECEDENCE_ORDER[2], "governance_decision")

    def test_select_excludes_superseded_and_expired(self):
        pack = select_packs(["founder_core", "architecture", "current_runtime", "mission_history", "governance"])
        ids = set(pack.selected_source_ids)
        self.assertNotIn("src_old_superseded", ids)
        self.assertNotIn("src_expired", ids)
        self.assertIn("src_expired", pack.stale_sources + pack.excluded_sources)

    def test_budget_enforced(self):
        pack = select_packs(["founder_core", "architecture", "current_runtime", "governance", "code_operations"], token_budget=50)
        self.assertLessEqual(pack.total_tokens, 50)

    def test_governance_binding(self):
        reg = fixture_registry()
        sources = [reg["src_founder_instruction"], reg["src_gov_binding"]]
        self.assertTrue(governance_binding_blocks_override(sources, action_prohibited=True))

    def test_sort_precedence(self):
        reg = fixture_registry()
        sources = [reg["src_architecture"], reg["src_founder_instruction"], reg["src_gov_binding"]]
        ordered = sort_by_precedence(sources)
        self.assertEqual(ordered[0].source_id, "src_founder_instruction")
        self.assertLess(precedence_rank(ordered[0]), precedence_rank(ordered[-1]))

    def test_conflict_preserved(self):
        pack = select_packs(["architecture", "governance"], token_budget=500)
        # conflict recorded even if one source stale-excluded
        self.assertTrue(any(c.conflict_id == "conf_gov_ownership" for c in pack.conflicts) or "src_conflict_b" in pack.selected_source_ids)

    def test_irrelevant_pack_not_loaded(self):
        pack = select_packs(["founder_core"], token_budget=400)
        self.assertTrue(all(s.pack_id == "founder_core" for s in pack.sources))


if __name__ == "__main__":
    unittest.main()
