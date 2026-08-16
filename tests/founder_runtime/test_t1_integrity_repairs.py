"""T1 integrity repairs: adapter honesty, durability, checkpoint verification.

Each test here fails against the pre-repair tree.  They prove behaviours that
were either absent (checkpoint verification, parked-state recovery, FULL
synchronous) or actively wrong (a mutation probe that wrote to a real path and
then attested it had attempted nothing; four adapters that declared a clamped
timeout while using an unclamped one on the wire).
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.contracts.checkpoint import Checkpoint
from recipe_engine.founder_runtime.errors import PersistenceConflict
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.persistence.sqlite import SqliteStore
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.runtime.recovery import (
    RecoveryStatus,
    verify_checkpoint,
)
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.live_adapters.live_axon import (
    MUTATION_PROBE_SENTINEL,
    LiveAxonReadonlyAdapter,
)
from recipe_engine.live_adapters.live_governance import LiveGovernanceAdapter
from recipe_engine.live_adapters.live_iar import LiveIARAdapter
from recipe_engine.live_adapters.live_mother import LiveMotherAdapter
from recipe_engine.live_adapters.transport import LoopbackHttpClient

from tests.founder_runtime import p2b_harness as H

META = AdapterRequestMeta(correlation_id="t1", observed_at="2026-08-16T00:00:00.000Z")


def _tmp_db() -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    path = tmp.name
    tmp.close()
    return path


class _RecordingClient(LoopbackHttpClient):
    """Loopback client that records the timeout each call actually requested."""

    def __init__(self, *endpoints):
        super().__init__({e.service_id: e for e in endpoints})
        self.timeouts: list[int] = []
        self.bodies: list[object] = []

    def request(self, service, path, *, timeout_ms, method="GET", json_body=None, **kw):
        self.timeouts.append(timeout_ms)
        self.bodies.append(json_body)
        return super().request(service, path, timeout_ms=timeout_ms, method=method,
                               json_body=json_body, **kw)


# ============================================== adapter honesty (Phase 3)
class MutationProbeHonestyTests(unittest.TestCase):
    """The one read-only operation that deliberately issues a mutation."""

    def _probe(self):
        routes = {("POST", "/api/tasks/execute"): (403, {"error": "MUTATION_NOT_AUTHORIZED"})}
        with H.mock_service(routes) as (port, _srv):
            ep = H.endpoint("axon", port)
            client = _RecordingClient(ep)
            adapter = LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,)), http=client)
            return adapter.mutation_probe(META), client

    def test_probe_declares_the_attempt_it_made(self):
        res, _ = self._probe()
        self.assertEqual(res.status, "denied")
        self.assertEqual(res.error_code, "MUTATION_NOT_AUTHORIZED")
        # The receipt must not claim nothing was attempted while a file_write
        # was on the wire.  That is a fabricated attestation.
        self.assertTrue(
            res.mutation_attempted,
            "mutation_probe issues a file_write; the envelope must declare it",
        )

    def test_probe_targets_no_real_repository_path(self):
        _, client = self._probe()
        writes = [b for b in client.bodies if isinstance(b, dict) and b.get("task") == "mutation_probe"]
        self.assertEqual(len(writes), 1)
        action = writes[0]["action"]
        self.assertEqual(action["tool"], "file_write")
        self.assertEqual(action["path"], MUTATION_PROBE_SENTINEL)
        # A misconfigured gateway must have nothing real to damage.
        self.assertFalse((ROOT / action["path"]).exists())
        self.assertNotIn("README", action["path"])
        self.assertEqual(action["content"], "")

    def test_non_probe_operations_still_declare_no_mutation(self):
        routes = {("GET", "/health"): (200, H.axon_identity_body())}
        with H.mock_service(routes) as (port, _srv):
            ep = H.endpoint("axon", port)
            res = LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,)), http=H.http_client(ep)).identity(META)
        self.assertEqual(res.status, "success")
        self.assertFalse(res.mutation_attempted)


class DeclaredTimeoutIsTheWireTimeoutTests(unittest.TestCase):
    """declared_timeout_ms must be the timeout the call actually used.

    op_timeout() documents that it is used for BOTH the transport call and the
    declared bound "so the two can never disagree".  Mother, IAR, Governance and
    AXON used raw meta.timeout_ms on the wire while declaring the clamped value,
    so a caller passing 300_000 got a 300-second live call stamped 5000.
    """

    OUTRAGEOUS = 300_000

    def _meta(self):
        return AdapterRequestMeta(correlation_id="t1", observed_at="2026-08-16T00:00:00.000Z",
                                  timeout_ms=self.OUTRAGEOUS)

    def test_mother_clamps_the_wire_call(self):
        with H.mock_service({("GET", "/health"): (200, {"ok": True, "status": "green"})}) as (port, _s):
            ep = H.endpoint("mother", port)
            client = _RecordingClient(ep)
            LiveMotherAdapter(H.settings_with(endpoints=(ep,)), http=client).health_result(
                observed_at=META.observed_at, meta=self._meta())
        self.assertTrue(client.timeouts)
        for t in client.timeouts:
            self.assertLess(t, self.OUTRAGEOUS)
            self.assertLessEqual(t, 5_000)

    def test_governance_clamps_the_wire_call(self):
        with H.mock_service({("GET", "/health"): (200, {"ok": True, "service": "autonomous-governance-core"})}) as (port, _s):
            ep = H.endpoint("governance", port)
            client = _RecordingClient(ep)
            LiveGovernanceAdapter(H.settings_with(endpoints=(ep,)), http=client).health(self._meta())
        self.assertTrue(client.timeouts)
        for t in client.timeouts:
            self.assertLessEqual(t, 4_000)

    def test_iar_clamps_the_wire_call(self):
        with H.mock_service({("GET", "/health"): (200, H.iar_health_body())}) as (port, _s):
            ep = H.endpoint("iar", port)
            client = _RecordingClient(ep)
            LiveIARAdapter(H.settings_with(endpoints=(ep,)), http=client).health(self._meta())
        self.assertTrue(client.timeouts)
        for t in client.timeouts:
            self.assertLessEqual(t, 6_000)

    def test_axon_clamps_the_wire_call(self):
        with H.mock_service({("GET", "/health"): (200, H.axon_identity_body())}) as (port, _s):
            ep = H.endpoint("axon", port)
            client = _RecordingClient(ep)
            res = LiveAxonReadonlyAdapter(H.settings_with(endpoints=(ep,)), http=client).identity(self._meta())
        self.assertTrue(client.timeouts)
        for t in client.timeouts:
            self.assertLessEqual(t, 5_000)
        # and the declared bound agrees with what was used
        self.assertEqual(res.declared_timeout_ms, client.timeouts[0])


# ============================================== durability (Phase 2)
class DurabilityPragmaTests(unittest.TestCase):
    def test_store_defaults_to_full_synchronous(self):
        store = SqliteStore(_tmp_db())
        store.initialize()
        try:
            level = store._c().execute("PRAGMA synchronous").fetchone()[0]
        finally:
            store.close()
        # 2 == FULL.  WAL's default of 1 (NORMAL) can lose committed
        # transactions on host power loss, which the power-loss claim forbids.
        self.assertEqual(level, 2)

    def test_explicit_level_is_honored(self):
        store = SqliteStore(_tmp_db(), synchronous="NORMAL")
        store.initialize()
        try:
            self.assertEqual(store._c().execute("PRAGMA synchronous").fetchone()[0], 1)
        finally:
            store.close()

    def test_unknown_level_fails_closed(self):
        store = SqliteStore(_tmp_db(), synchronous="FULL; DROP TABLE missions")
        with self.assertRaises(PersistenceConflict):
            store.initialize()


# ============================================== checkpoint verification (Phase 2)
class CheckpointVerificationTests(unittest.TestCase):
    """state_hash and projection_hash were written and never read back."""

    def setUp(self):
        self.path = _tmp_db()
        self.rt = MissionRuntime(self.path, clock=SteppingClock(), adapter_mode="fixture")
        self.mission = self.rt.create_mission_from_text(
            "Tell me where the organism is and what is blocked",
            creation_idempotency_key="t1-ckpt",
        )
        self.mid = self.mission.mission_id
        self.rt.checkpoint(self.mid, completed_steps=["a"], pending_steps=["b", "c"],
                           active_step=None, last_receipt=None, kind="after_step")
        self.ckpt = self.rt.store.latest_checkpoint(self.mid)

    def tearDown(self):
        self.rt.close()

    def test_clean_checkpoint_verifies(self):
        self.assertEqual(verify_checkpoint(self.rt.store, self.ckpt, self.rt.get_mission(self.mid)), [])

    def test_cross_mission_checkpoint_detected(self):
        other = self.rt.create_mission_from_text("status", creation_idempotency_key="t1-other")
        issues = verify_checkpoint(self.rt.store, self.ckpt, other)
        self.assertIn("checkpoint_mission_mismatch", issues)

    def test_checkpoint_ahead_of_projection_detected(self):
        self.ckpt.mission_version = self.rt.get_mission(self.mid).version + 5
        self.assertIn("checkpoint_ahead_of_projection",
                      verify_checkpoint(self.rt.store, self.ckpt, self.rt.get_mission(self.mid)))

    def test_projection_hash_tamper_detected(self):
        self.ckpt.projection_hash = hash_payload({"not": "this mission"})
        self.assertIn("checkpoint_diverges_from_event_log",
                      verify_checkpoint(self.rt.store, self.ckpt, self.rt.get_mission(self.mid)))

    def test_divergence_from_the_event_log_detected(self):
        # Rewrite the step set but keep everything else: exactly the stale
        # checkpoint that was previously trusted verbatim as a resume boundary.
        self.ckpt.completed_steps = ["a", "b", "c"]
        issues = verify_checkpoint(self.rt.store, self.ckpt, self.rt.get_mission(self.mid))
        self.assertIn("checkpoint_diverges_from_event_log", issues)

    def test_unlogged_checkpoint_detected(self):
        self.ckpt.checkpoint_id = "ckpt_never_logged"
        issues = verify_checkpoint(self.rt.store, self.ckpt, self.rt.get_mission(self.mid))
        self.assertIn("checkpoint_not_logged", issues)

    def test_active_step_already_completed_detected(self):
        self.ckpt.active_step = "a"
        self.assertIn("checkpoint_active_step_already_completed",
                      verify_checkpoint(self.rt.store, self.ckpt, self.rt.get_mission(self.mid)))

    def test_not_resumable_is_refused(self):
        self.ckpt.resumable = False
        self.assertIn("checkpoint_marked_not_resumable",
                      verify_checkpoint(self.rt.store, self.ckpt, self.rt.get_mission(self.mid)))

    def test_recovery_refuses_a_tampered_checkpoint(self):
        # Tamper the stored row directly, the way a second connection could.
        con = sqlite3.connect(self.path)
        row = con.execute(
            "SELECT checkpoint_id, body_json FROM checkpoints WHERE mission_id=?",
            (self.mid,)).fetchone()
        body = json.loads(row[1])
        body["completed_steps"] = ["a", "b", "c", "d"]
        con.execute("UPDATE checkpoints SET body_json=? WHERE checkpoint_id=?", (json.dumps(body), row[0]))
        con.commit()
        con.close()

        rec = self.rt.recover(self.mid)
        self.assertEqual(rec["status"], RecoveryStatus.INVALID_CHECKPOINT.value)
        self.assertIn("checkpoint_diverges_from_event_log", rec["issues"])
        self.assertIsNone(rec.get("next_step"))


# ============================================== parked-state recovery (Phase 2)
class ParkedStateRecoveryTests(unittest.TestCase):
    """APPROVAL_REQUIRED and BLOCKED_DEPENDENCY were unreachable enum members."""

    def _runtime(self):
        return MissionRuntime(_tmp_db(), clock=SteppingClock(), adapter_mode="fixture")

    def _mission_at(self, rt, *states):
        m = rt.create_mission_from_text(
            "Tell me where the organism is and what is blocked",
            creation_idempotency_key="t1-park",
        )
        for s in states:
            rt.transition(m.mission_id, s, reason="t1_park")
        return m.mission_id

    def test_awaiting_approval_reports_approval_required(self):
        rt = self._runtime()
        try:
            mid = self._mission_at(
                rt, MissionState.INTAKE, MissionState.CONTEXT_RESOLUTION,
                MissionState.PLANNING, MissionState.AWAITING_APPROVAL,
            )
            rec = rt.recover(mid)
            self.assertEqual(rec["status"], RecoveryStatus.APPROVAL_REQUIRED.value)
            self.assertIsNone(rec["next_step"])
            self.assertFalse(rec["execution_resume_authorized"])
        finally:
            rt.close()

    def test_blocked_dependency_reports_blocked_dependency(self):
        rt = self._runtime()
        try:
            mid = self._mission_at(
                rt, MissionState.INTAKE, MissionState.CONTEXT_RESOLUTION,
                MissionState.BLOCKED_DEPENDENCY,
            )
            rec = rt.recover(mid)
            self.assertEqual(rec["status"], RecoveryStatus.BLOCKED_DEPENDENCY.value)
            self.assertIsNone(rec["next_step"])
        finally:
            rt.close()

    def test_approved_no_execution_never_reports_safe_to_resume(self):
        rt = self._runtime()
        try:
            mid = self._mission_at(
                rt, MissionState.INTAKE, MissionState.CONTEXT_RESOLUTION,
                MissionState.PLANNING, MissionState.AWAITING_APPROVAL,
                MissionState.APPROVED_NO_EXECUTION,
            )
            rec = rt.recover(mid)
            self.assertEqual(rec["status"], RecoveryStatus.APPROVAL_REQUIRED.value)
            self.assertFalse(rec["execution_resume_authorized"])
        finally:
            rt.close()

    def test_ordinary_nonterminal_mission_still_resumes(self):
        rt = self._runtime()
        try:
            mid = self._mission_at(rt, MissionState.INTAKE, MissionState.CONTEXT_RESOLUTION)
            self.assertEqual(rt.recover(mid)["status"], RecoveryStatus.SAFE_TO_RESUME.value)
        finally:
            rt.close()


if __name__ == "__main__":
    unittest.main()
