"""Approval and authority fixture policy tests."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.fixture_governance import FixtureGovernance
from recipe_engine.founder_runtime.contracts.authority import AuthorityClass
from recipe_engine.founder_runtime.contracts.approval import Approval, ApprovalDecision, SCHEMA_APPROVAL
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.errors import AdapterModeInvalid
from recipe_engine.founder_runtime.adapters.base import require_fixture_mode


class ApprovalTests(unittest.TestCase):
    def test_a0_allow(self):
        g = FixtureGovernance(mode="fixture")
        d = g.decide(AuthorityClass.A0, action="read", scope="local")
        self.assertEqual(d["decision"], "allow")

    def test_a5_approval_required(self):
        g = FixtureGovernance(mode="fixture")
        d = g.decide(AuthorityClass.A5, action="push", scope="remote")
        self.assertEqual(d["decision"], "approval_required")

    def test_a6_enhanced(self):
        g = FixtureGovernance(mode="fixture")
        d = g.decide(AuthorityClass.A6, action="destroy", scope="prod")
        self.assertTrue(d["enhanced"])

    def test_invalid_mode_fails_closed(self):
        with self.assertRaises(AdapterModeInvalid):
            require_fixture_mode("live", "test")

    def test_approval_idempotent(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        path = tmp.name
        tmp.close()
        rt = MissionRuntime(path, clock=SteppingClock(), adapter_mode="fixture")
        m = rt.create_mission_from_text("status", creation_idempotency_key="ap1")
        a = Approval(
            schema_version=SCHEMA_APPROVAL,
            approval_id="appr1",
            mission_id=m.mission_id,
            action_class="A4",
            exact_requested_action="commit",
            scope="repo:recipe-engine",
            requested_by="runtime",
            decided_by="fixture",
            decision=ApprovalDecision.ALLOW,
            conditions=[],
            expiry="2026-12-31T00:00:00.000Z",
            evidence_refs=[],
            created_at="2026-07-19T00:00:00.000Z",
            idempotency_key="appr-idem-1",
        )
        a.compute_hash()
        a1 = rt.record_approval(a)
        a2 = rt.record_approval(a)
        self.assertEqual(a1.approval_id, a2.approval_id)
        self.assertEqual(len(rt.store.list_approvals(m.mission_id)), 1)
        rt.close()


if __name__ == "__main__":
    unittest.main()
