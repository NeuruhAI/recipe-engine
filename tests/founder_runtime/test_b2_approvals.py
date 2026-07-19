"""B2 — approval enforcement adversarial tests."""
from __future__ import annotations
import sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.clock import SteppingClock, FixedClock
from recipe_engine.founder_runtime.contracts.approval import Approval, ApprovalDecision, SCHEMA_APPROVAL
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.errors import ApprovalRequired, ApprovalExpired, AuthorityDenied
from recipe_engine.founder_runtime.runtime.approvals import gated_action_for


class B2ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.path = self.tmp.name
        self.tmp.close()
        self.clock = SteppingClock()
        self.rt = MissionRuntime(self.path, clock=self.clock, adapter_mode="fixture")

    def tearDown(self):
        self.rt.close()

    def _to_awaiting_approval(self, key_suffix: str = ""):
        key = "b2-" + self.id() + key_suffix
        m = self.rt.create_mission_from_text("status organism", creation_idempotency_key=key)
        self.rt.transition(m.mission_id, MissionState.INTAKE, reason="in")
        self.rt.transition(m.mission_id, MissionState.CONTEXT_RESOLUTION, reason="ctx")
        self.rt.transition(m.mission_id, MissionState.PLANNING, reason="plan")
        self.rt.transition(m.mission_id, MissionState.AWAITING_APPROVAL, reason="need_appr")
        return self.rt.get_mission(m.mission_id)

    def _allow(self, mission_id, action, scope, *, expiry="2099-01-01T00:00:00.000Z", aid=None, idem=None):
        a = Approval(
            schema_version=SCHEMA_APPROVAL,
            approval_id=aid or ("appr_" + self.id().split(".")[-1]),
            mission_id=mission_id,
            action_class="A2",
            exact_requested_action=action,
            scope=scope,
            requested_by="runtime",
            decided_by="fixture",
            decision=ApprovalDecision.ALLOW,
            conditions=[],
            expiry=expiry,
            evidence_refs=[],
            created_at="2026-07-19T00:00:00.000Z",
            decided_at="2026-07-19T00:00:00.000Z",
            idempotency_key=idem or ("idem-" + self.id()),
        )
        a.compute_hash()
        return self.rt.record_approval(a)

    def test_valid_exact_approval(self):
        m = self._to_awaiting_approval()
        action, scope = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
        ap = self._allow(m.mission_id, action, scope)
        m2 = self.rt.transition(
            m.mission_id, MissionState.QUEUED, reason="go", approval_id=ap.approval_id,
        )
        self.assertEqual(m2.current_state, MissionState.QUEUED)
        events = self.rt.events(m.mission_id)
        types = [e["event_type"] for e in events]
        self.assertIn("approval_recorded", types)
        self.assertIn("approval_consumed", types)

    def test_zero_approval(self):
        m = self._to_awaiting_approval()
        with self.assertRaises(ApprovalRequired):
            self.rt.transition(m.mission_id, MissionState.QUEUED, reason="go")

    def test_expired_approval(self):
        m = self._to_awaiting_approval()
        action, scope = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
        # record while valid relative to stepping clock start (~2026-07-19T00:00:00)
        ap = self._allow(m.mission_id, action, scope, expiry="2026-07-19T00:00:30.000Z")
        # advance stepping clock past expiry (each now_iso steps 1s)
        for _ in range(200):
            self.rt.clock.now_iso()
        with self.assertRaises(ApprovalExpired):
            self.rt.transition(m.mission_id, MissionState.QUEUED, reason="go", approval_id=ap.approval_id)

    def test_denied_approval(self):
        m = self._to_awaiting_approval()
        action, scope = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
        a = Approval(
            schema_version=SCHEMA_APPROVAL, approval_id="deny1", mission_id=m.mission_id,
            action_class="A2", exact_requested_action=action, scope=scope,
            requested_by="r", decided_by="f", decision=ApprovalDecision.DENY,
            conditions=[], expiry="2099-01-01T00:00:00.000Z", evidence_refs=[],
            created_at="t", idempotency_key="deny-idem",
        )
        a.compute_hash()
        self.rt.record_approval(a)
        with self.assertRaises(AuthorityDenied):
            self.rt.transition(m.mission_id, MissionState.QUEUED, reason="go", approval_id="deny1")

    def test_wrong_scope(self):
        m = self._to_awaiting_approval()
        action, _ = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
        ap = self._allow(m.mission_id, action, "wrong_scope")
        with self.assertRaises(AuthorityDenied):
            self.rt.transition(m.mission_id, MissionState.QUEUED, reason="go", approval_id=ap.approval_id)

    def test_expanded_scope(self):
        m = self._to_awaiting_approval()
        action, scope = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
        # broader scope than exact
        ap = self._allow(m.mission_id, action, scope + "/*")
        with self.assertRaises(AuthorityDenied):
            self.rt.transition(m.mission_id, MissionState.QUEUED, reason="go", approval_id=ap.approval_id)

    def test_wrong_mission(self):
        m1 = self._to_awaiting_approval()
        m2 = self.rt.create_mission_from_text("other", creation_idempotency_key="b2-other")
        action, scope = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
        ap = self._allow(m2.mission_id, action, scope, aid="cross", idem="cross-idem")
        with self.assertRaises(AuthorityDenied):
            self.rt.transition(m1.mission_id, MissionState.QUEUED, reason="go", approval_id="cross")

    def test_wrong_action(self):
        m = self._to_awaiting_approval()
        _, scope = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
        ap = self._allow(m.mission_id, "transition:OTHER", scope)
        with self.assertRaises(AuthorityDenied):
            self.rt.transition(m.mission_id, MissionState.QUEUED, reason="go", approval_id=ap.approval_id)

    def test_reused_approval(self):
        m = self._to_awaiting_approval()
        action, scope = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
        ap = self._allow(m.mission_id, action, scope)
        self.rt.transition(m.mission_id, MissionState.QUEUED, reason="go", approval_id=ap.approval_id)
        # back to AWAITING_APPROVAL is not allowed from QUEUED directly without path
        # try to consume again for another gated transition if we get there
        # Force: create new mission and try reuse same approval id
        m2 = self._to_awaiting_approval(key_suffix="-reuse2")
        with self.assertRaises((AuthorityDenied, ApprovalRequired)):
            self.rt.transition(m2.mission_id, MissionState.QUEUED, reason="go", approval_id=ap.approval_id)

    def test_transitive_reuse(self):
        # approval for action A cannot authorize action B
        m = self._to_awaiting_approval()
        action, scope = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
        ap = self._allow(m.mission_id, action, scope)
        # try use for PLANNING path instead
        with self.assertRaises(AuthorityDenied):
            self.rt.transition(
                m.mission_id, MissionState.PLANNING, reason="back", approval_id=ap.approval_id,
            )

    def test_duplicate_record(self):
        m = self._to_awaiting_approval()
        action, scope = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
        a1 = self._allow(m.mission_id, action, scope, aid="dupA", idem="dup-rec")
        a2 = self._allow(m.mission_id, action, scope, aid="dupA", idem="dup-rec")
        self.assertEqual(a1.approval_id, a2.approval_id)
        self.assertEqual(len(self.rt.store.list_approvals(m.mission_id)), 1)

    def test_replay_includes_approval_events(self):
        m = self._to_awaiting_approval()
        action, scope = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
        ap = self._allow(m.mission_id, action, scope)
        self.rt.transition(m.mission_id, MissionState.QUEUED, reason="go", approval_id=ap.approval_id)
        replay = self.rt.replay(m.mission_id)
        types = [e["event_type"] for e in replay["events"]]
        self.assertIn("approval_recorded", types)
        self.assertIn("approval_consumed", types)
        self.assertTrue(replay["rebuilt_matches_stored"])


if __name__ == "__main__":
    unittest.main()
