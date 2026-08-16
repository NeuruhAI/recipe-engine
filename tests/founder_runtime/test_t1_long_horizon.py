"""T1 Phase-5/6: the long-horizon read-only mission.

Proves the behaviours the runtime previously had no article for: a multi-stage
mission that survives process loss and resumes without re-executing a completed
stage, replans when later evidence contradicts the standing plan, discloses a
blocked dependency instead of substituting a fixture, and never mutates.

The pre-existing "power loss" test closes the runtime cleanly and then asks
recovery for a plan; nothing resumed, and nothing proved that a resumed mission
does not repeat work it has already done.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.contracts.receipts import (
    ExecutionReceipt,
    ReceiptStatus,
    SCHEMA_RECEIPT,
    TestResult,
)
from recipe_engine.founder_runtime.evidence.validation import validate_live_receipt
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.missions.long_horizon import (
    LONG_HORIZON_STAGES,
    LongHorizonError,
    OrganReader,
    SourceObservation,
    build_claims,
    run_long_horizon_mission,
)
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.runtime.recovery import RecoveryStatus
from recipe_engine.founder_runtime.state.states import MissionState


def _tmp_db() -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    path = tmp.name
    tmp.close()
    return path


def _runtime(path: str, *, now: str | None = None) -> MissionRuntime:
    return MissionRuntime(path, clock=SteppingClock(now) if now else SteppingClock(),
                          adapter_mode="fixture")


class _BlockedQdrant:
    """A retrieval organ that is down.  It answers with a gap, never a guess."""

    def search_result(self, query, *, limit=5, meta=None):
        raise RuntimeError("qdrant collection rotated; baseline not re-ratified")


# ================================================= end-to-end
class LongHorizonMissionTests(unittest.TestCase):
    def test_mission_completes_with_evidence(self):
        rt = _runtime(_tmp_db())
        try:
            r = run_long_horizon_mission(rt, creation_idempotency_key="lh-complete")
        finally:
            rt.close()
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["final_state"], "COMPLETED")
        self.assertEqual(sorted(r["completed_stages"]), sorted(LONG_HORIZON_STAGES))
        self.assertTrue(r["completion_hash"])
        self.assertEqual(r["replay"]["final_state"], "COMPLETED")

    def test_mission_never_mutates(self):
        rt = _runtime(_tmp_db())
        try:
            r = run_long_horizon_mission(rt, creation_idempotency_key="lh-nomutate")
            for receipt in rt.store.list_receipts(r["mission_id"]):
                self.assertFalse(receipt.mutation_authorized)
                self.assertEqual(receipt.files_changed, [])
        finally:
            rt.close()
        self.assertFalse(r["mutation"])

    def test_every_stage_leaves_a_receipt(self):
        rt = _runtime(_tmp_db())
        try:
            r = run_long_horizon_mission(rt, creation_idempotency_key="lh-receipts")
            steps = [x.step_id for x in rt.store.list_receipts(r["mission_id"])]
        finally:
            rt.close()
        self.assertEqual(sorted(steps), sorted(LONG_HORIZON_STAGES))
        self.assertEqual(len(steps), len(set(steps)), "a stage produced two receipts")

    def test_mission_passes_its_own_approval_gate(self):
        """The objective mentions production, so the kernel escalates it."""
        rt = _runtime(_tmp_db())
        try:
            r = run_long_horizon_mission(rt, creation_idempotency_key="lh-approval")
            mission = rt.get_mission(r["mission_id"])
            approvals = rt.store.list_approvals(r["mission_id"])
        finally:
            rt.close()
        self.assertTrue(mission.authority_requirements[0].requires_approval)
        self.assertTrue(approvals, "an approval-gated mission ran with no recorded approval")


# ================================================= restart / no duplicate work
class RestartResumeTests(unittest.TestCase):
    def test_resume_executes_only_the_remaining_stages(self):
        path = _tmp_db()
        rt = _runtime(path)
        first = run_long_horizon_mission(rt, creation_idempotency_key="lh-resume",
                                         stop_after_stage="compile_plan")
        mid = first["mission_id"]
        rt.close()  # process loss

        rt2 = _runtime(path, now="2026-08-16T09:00:00.000Z")
        try:
            second = run_long_horizon_mission(rt2, creation_idempotency_key="lh-resume")
            steps = [x.step_id for x in rt2.store.list_receipts(mid)]
        finally:
            rt2.close()

        self.assertEqual(first["status"], "suspended")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["mission_id"], mid, "resume must continue the same mission")
        overlap = set(first["executed_this_run"]) & set(second["executed_this_run"])
        self.assertEqual(overlap, set(), f"stages re-executed after restart: {overlap}")
        self.assertEqual(sorted(steps), sorted(LONG_HORIZON_STAGES))
        self.assertEqual(len(steps), len(set(steps)), "duplicate execution produced a second receipt")

    def test_recovery_names_the_next_stage_and_what_not_to_rerun(self):
        path = _tmp_db()
        rt = _runtime(path)
        first = run_long_horizon_mission(rt, creation_idempotency_key="lh-rec",
                                         stop_after_stage="read_system_state")
        mid = first["mission_id"]
        rt.close()

        rt2 = _runtime(path, now="2026-08-16T09:00:00.000Z")
        try:
            rec = rt2.recover(mid)
        finally:
            rt2.close()
        self.assertEqual(rec["status"], RecoveryStatus.SAFE_TO_RESUME.value)
        self.assertEqual(rec["next_step"], "generate_strategies")
        self.assertIn("resolve_context", rec["do_not_rerun"])
        self.assertIn("read_system_state", rec["do_not_rerun"])
        # Recovery plans; it never authorizes execution.
        self.assertFalse(rec["execution_resume_authorized"])

    def test_replay_is_stable_across_the_restart(self):
        path = _tmp_db()
        rt = _runtime(path)
        first = run_long_horizon_mission(rt, creation_idempotency_key="lh-replay",
                                         stop_after_stage="compile_plan")
        mid = first["mission_id"]
        rt.close()
        rt2 = _runtime(path, now="2026-08-16T09:00:00.000Z")
        try:
            run_long_horizon_mission(rt2, creation_idempotency_key="lh-replay")
            a = rt2.replay(mid)
        finally:
            rt2.close()
        rt3 = _runtime(path, now="2026-08-16T10:00:00.000Z")
        try:
            b = rt3.replay(mid)
        finally:
            rt3.close()
        self.assertEqual(a["projection_hash"], b["projection_hash"])
        self.assertEqual(a["event_count"], b["event_count"])
        self.assertEqual(a["final_state"], "COMPLETED")


# ================================================= replanning
class ReplanTests(unittest.TestCase):
    def test_mission_replans_and_supersedes_rather_than_overwrites(self):
        rt = _runtime(_tmp_db())
        try:
            r = run_long_horizon_mission(rt, creation_idempotency_key="lh-replan")
            lineage = rt.store.list_plans(r["mission_id"])
        finally:
            rt.close()
        self.assertTrue(r["replanned"], "evidence changed but the plan did not")
        self.assertTrue(r["replan_reason"])
        self.assertGreaterEqual(len(lineage), 2)
        self.assertEqual(lineage[0]["plan_version"], 1)
        self.assertIsNone(lineage[0]["supersedes"])
        self.assertEqual(lineage[1]["supersedes"], lineage[0]["plan_id"])
        # A superseded plan is retained, not overwritten.
        self.assertNotEqual(lineage[0]["plan_id"], lineage[1]["plan_id"])
        self.assertNotEqual(lineage[0]["strategy_id"], lineage[1]["strategy_id"])

    def test_replan_is_bound_to_authority_and_evidence(self):
        rt = _runtime(_tmp_db())
        try:
            r = run_long_horizon_mission(rt, creation_idempotency_key="lh-bind")
            latest = rt.store.latest_plan(r["mission_id"])
        finally:
            rt.close()
        binding = latest["authority_binding"]
        self.assertFalse(binding["approval_confers_execution"])
        self.assertIn("no_production_mutation", binding["constraints"])
        self.assertTrue(binding["non_goals"])
        self.assertTrue(latest["evidence_refs"])
        self.assertEqual(latest["mission_id"], r["mission_id"])


# ================================================= blocked dependency
class BlockedDependencyTests(unittest.TestCase):
    def test_blocked_organ_is_disclosed_never_substituted(self):
        rt = _runtime(_tmp_db())
        reader_organs = {"qdrant": _BlockedQdrant()}
        try:
            r = run_long_horizon_mission(
                rt, creation_idempotency_key="lh-blocked", organs=reader_organs)
        finally:
            rt.close()
        self.assertEqual(r["status"], "completed")
        gaps = r["evidence_gaps"]
        self.assertTrue(any(g.startswith("qdrant:") for g in gaps),
                        f"a blocked organ was not disclosed: {gaps}")
        # The claim the blocked organ would have supported must NOT appear.
        self.assertNotIn("claim_history", r["claim_ids"])

    def test_blocked_organ_produces_no_claim(self):
        blocked = SourceObservation("qdrant", "live", "blocked", "DEPENDENCY_UNHEALTHY")
        claims, evidence, established = build_claims([blocked], observed_at="2026-08-16T00:00:00.000Z")
        self.assertEqual(claims, [])
        self.assertEqual(evidence, [])
        self.assertEqual(established, [])

    def test_a_gap_lowers_coverage_rather_than_being_assumed(self):
        rt = _runtime(_tmp_db())
        try:
            r = run_long_horizon_mission(
                rt, creation_idempotency_key="lh-cov", organs={"qdrant": _BlockedQdrant()})
        finally:
            rt.close()
        self.assertNotIn("claim_history", r["claim_ids"])
        self.assertIn("claim_overall_health", r["claim_ids"])


# ================================================= mode discipline
class ModeDisciplineTests(unittest.TestCase):
    def test_live_mode_requires_injected_organs(self):
        with self.assertRaises(LongHorizonError):
            OrganReader("live")

    def test_live_mode_requires_an_executor(self):
        with self.assertRaises(LongHorizonError):
            OrganReader("live", organs={"mother": object(), "github": object(),
                                        "vault": object(), "qdrant": object()})

    def test_incomplete_live_organ_set_is_refused(self):
        with self.assertRaises(LongHorizonError):
            OrganReader("live", organs={"mother": object()}, executor=object())

    def test_unknown_mode_fails_closed(self):
        with self.assertRaises(LongHorizonError):
            OrganReader("pretend")

    def test_founder_runtime_does_not_import_live_adapters(self):
        """The dependency is one-way; the confinement scan depends on it.

        Checked over the whole founder_runtime package, not just this module:
        one lazy import anywhere would pull sockets, subprocess and filesystem
        I/O into the scanned reachable graph.
        """
        import ast

        pkg = ROOT / "recipe_engine" / "founder_runtime"
        offenders: list[str] = []
        for path in sorted(pkg.rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name.startswith("recipe_engine.live_adapters"):
                            offenders.append(f"{path.name}:{a.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("recipe_engine.live_adapters"):
                        offenders.append(f"{path.name}:{node.module}")
        self.assertEqual(offenders, [], f"founder_runtime imports live_adapters: {offenders}")

    def test_every_observation_records_which_world_it_came_from(self):
        rt = _runtime(_tmp_db())
        try:
            r = run_long_horizon_mission(rt, creation_idempotency_key="lh-mode")
        finally:
            rt.close()
        self.assertTrue(r["observations"])
        for obs in r["observations"]:
            self.assertEqual(obs["mode"], "fixture")


# ================================================= live receipt validation
class LiveReceiptValidationTests(unittest.TestCase):
    """The fixture validator cannot validate live evidence; this one can."""

    def _receipt(self, **over):
        output = over.pop("outcome_data", {"observed": True})
        base = dict(
            schema_version=SCHEMA_RECEIPT, receipt_id="rcpt_live_1",
            mission_id="m1", plan_id="p1", step_id="s1",
            executor="live_axon", capability="live.read_system_state",
            started_at="2026-08-16T00:00:00.000Z", ended_at="2026-08-16T00:00:01.000Z",
            status=ReceiptStatus.SUCCESS, input_hash="in1", output_hash=hash_payload(output),
            repository_metadata={"mode": "live", "mutation": False},
            files_changed=[], tests=[TestResult(name="live_read", passed=True)],
            artifacts=[], error=None, authority_reference="not_claimed_by_governance",
            idempotency_key="live_step:m1:p1:s1",
            evidence_class="live_observed", trust_level="live_observed",
            producer_identity="live_axon", outcome_data=output, mutation_authorized=False,
        )
        base.update(over)
        r = ExecutionReceipt(**base)
        r.action_binding = f"{r.capability}:{r.input_hash}:{r.output_hash}"
        r.compute_hash()
        return r

    def _errs(self, r):
        return validate_live_receipt(r, expected_mission_id="m1", expected_plan_id="p1",
                                     expected_step_id="s1", expected_input_hash="in1")

    def test_a_well_formed_live_receipt_validates(self):
        self.assertEqual(self._errs(self._receipt()), [])

    def test_a_live_receipt_claiming_mutation_is_refused(self):
        r = self._receipt()
        r.mutation_authorized = True
        r.compute_hash()
        self.assertIn("live_mutation_forbidden", self._errs(r))

    def test_a_live_receipt_reporting_changed_files_is_refused(self):
        r = self._receipt()
        r.files_changed = ["README.md"]
        r.compute_hash()
        self.assertIn("live_mutation_forbidden", self._errs(r))

    def test_a_receipt_without_live_trust_markers_is_refused(self):
        r = self._receipt(evidence_class="fixture", trust_level="fixture_only")
        self.assertIn("invalid_live_trust", self._errs(r))

    def test_a_tampered_live_receipt_is_refused(self):
        r = self._receipt()
        r.outcome_data = {"observed": "something else"}
        self.assertTrue(self._errs(r))

    def test_an_unsafe_capability_is_refused(self):
        r = self._receipt(capability="live.file_write")
        self.assertIn("unsafe_live_capability", self._errs(r))


if __name__ == "__main__":
    unittest.main()
