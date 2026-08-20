"""T31A: a live mission may not be the origin of the decision that authorizes it.

The long-horizon mission used to construct its own Approval — decision=ALLOW,
decided_by="founder", expiry 2099 — record it, and consume it to open its own
gate.  Every downstream check (mission binding, action binding, scope binding,
expiry, one-shot consumption) then ran against evidence the mission had
manufactured, so all of them passed and none of them meant anything.

These are the articles for the boundary: that no live path can mint an ALLOW,
that an absent decision parks rather than defaults open, that the decision must
come from somewhere else and be bound to this exact action, that Governance is
genuinely consulted and every one of its failure modes closes the gate, and
that neither resume nor restart can manufacture what a run could not.

T31B tightened what "came from somewhere else" has to mean.  A record in this
runtime's own store is evidence only to this runtime — it is exactly the kind of
thing the process that benefits from it could have written.  So a live gate now
also requires the founder's SIGNED artifact, which the external channel mints
and Governance verifies, and submits it for authorization before the transition.
An unsigned external record parks, and the harness below carries a signed one
because that is now what an external decision IS.
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.clock import FixedClock
from recipe_engine.founder_runtime.contracts.approval import (
    Approval,
    ApprovalDecision,
    SCHEMA_APPROVAL,
)
from recipe_engine.founder_runtime.errors import AuthorityDenied, FounderRuntimeError
from recipe_engine.founder_runtime.api.founder_signing import FounderApprovalSigner
from recipe_engine.founder_runtime.missions.long_horizon import (
    GOV_APPROVAL_REF_PREFIX,
    MISSION_ACTOR,
    OrganReader,
    authorization_scope_key,
    run_long_horizon_mission,
)
from recipe_engine.founder_runtime.runtime.approvals import gated_action_for
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.state.states import MissionState

LH = ROOT / "recipe_engine" / "founder_runtime" / "missions" / "long_horizon.py"

# The identity this harness attests. Governance is stubbed here, so what is
# under test is the mission's own conduct: that it attests an identity at all,
# binds the founder's artifact to it, and submits both.
SOURCE_SHA = "949a8b8c99f2b111edf8c734f2cfcad89c47bdf2"
# A signing key for the harness's stand-in decision channel. It is not a
# credential: nothing here verifies it, because signature verification belongs
# to Governance and is proved in that repository and in the live court.
HARNESS_SIGNING_KEY = "t31b-harness-signing-key-not-a-credential"
GATE_ACTION, GATE_SCOPE = gated_action_for(
    MissionState.AWAITING_APPROVAL, MissionState.QUEUED,
)


# ---------------------------------------------------------------- stub organs
class _Result:
    """Shaped like an AdapterResult: what the mission reads off a live organ."""

    def __init__(self, status, payload=None, error_code=None):
        self.status = status
        self.payload = payload or {}
        self.error_code = error_code
        self.evidence_refs = []


class _Governance:
    """A stand-in for the live Governance organ, one behaviour per court."""

    def __init__(self, behaviour="allow"):
        self.behaviour = behaviour
        self.calls = []
        self.authorizations = []

    def authorize(self, facts, *, founder_approval, meta):
        """Stands in for POST /v2/authorize.

        Records what was submitted so a court can assert that the mission
        presented the founder's artifact rather than something of its own.
        """
        self.authorizations.append({"facts": dict(facts), "founder_approval": founder_approval})
        if self.behaviour == "authz_down":
            raise ConnectionRefusedError("governance unreachable")
        if self.behaviour == "authz_deny":
            return _Result("success", {"authorization_id": "auth_x", "decision": "deny",
                                       "reason_code": "SOURCE_SHA_SUPERSEDED"})
        if self.behaviour == "authz_founder_required":
            return _Result("success", {"authorization_id": "auth_f", "decision": "founder_required",
                                       "reason_code": "FOUNDER_REQUIRED_NO_GRANT"})
        if self.behaviour == "authz_malformed":
            return _Result("success", {"unexpected": True})
        return _Result("success", {"authorization_id": "auth_ok", "decision": "allow",
                                   "reason_code": "FOUNDER_APPROVED_SCOPE_ONCE",
                                   "risk_tier": "R0"})

    def decide(self, action, *, trusted_authority_class, trusted_policy_version, meta):
        self.calls.append(action)
        if self.behaviour == "down":
            raise ConnectionRefusedError("governance unreachable")
        if self.behaviour == "timeout":
            return _Result("timeout", error_code="TIMEOUT")
        if self.behaviour == "malformed":
            return _Result("success", {"unexpected": True})       # no audit_id/decision
        if self.behaviour == "deny":
            return _Result("success", {"audit_id": "audit_x", "decision": "deny"})
        return _Result("success", {"audit_id": "audit_ok", "decision": "allow"})


def _organs(behaviour="allow"):
    gov = _Governance(behaviour)
    return {
        "mother": object(), "github": object(), "vault": object(),
        "qdrant": object(), "governance": gov,
    }, gov


def _run(rt, organs, *, key, max_stages=0):
    return run_long_horizon_mission(
        rt, mode="live", organs=organs, executor=object(),
        creation_idempotency_key=key, max_stages=max_stages,
        source_sha=SOURCE_SHA,
    )


def _signed_artifact(rt, mission_id, *, action, scope, source_sha=SOURCE_SHA):
    """What the external channel mints: bound to the scope the MISSION published."""
    signer = FounderApprovalSigner(HARNESS_SIGNING_KEY)
    now = rt.clock.now_iso()
    artifact = signer.sign_scope_once(
        scope_key=authorization_scope_key(
            mission_id, action=action, scope=scope, source_sha=source_sha,
        ),
        source_sha=source_sha,
        issued_at=now,
        expires_at="2026-12-31T00:00:00.000Z",
    )
    return GOV_APPROVAL_REF_PREFIX + json.dumps(artifact, sort_keys=True, separators=(",", ":"))


def _external_allow(rt, mission_id, *, action=GATE_ACTION, scope=GATE_SCOPE,
                    mission=None, expiry=None, decision=ApprovalDecision.ALLOW,
                    requested_by="founder", decided_by="jeramie", key="ext",
                    signed=True):
    """An approval as an out-of-band decider records it. Never by the mission.

    ``signed=True`` is the default because that is what an external decision now
    IS: the channel's signature is the part a party other than the beneficiary
    can check. Pass signed=False to prove that the unsigned record alone still
    parks a live mission.
    """
    refs = []
    if signed and decision == ApprovalDecision.ALLOW:
        refs.append(_signed_artifact(rt, mission or mission_id, action=action, scope=scope))
    approval = Approval(
        schema_version=SCHEMA_APPROVAL,
        approval_id=f"appr_{key}",
        mission_id=mission or mission_id,
        action_class="A5",
        exact_requested_action=action,
        scope=scope,
        requested_by=requested_by,
        decided_by=decided_by,
        decision=decision,
        conditions=["read_only"],
        expiry=expiry,
        evidence_refs=refs,
        created_at=rt.clock.now_iso(),
        decided_at=rt.clock.now_iso(),
        idempotency_key=f"approval:{key}",
    )
    approval.compute_hash()
    # Recorded through the runtime, exactly as the operator API records it, so
    # the canonical APPROVAL_RECORDED event exists and the store's own integrity
    # check accepts the row. A directly-inserted approval is not an approval.
    return rt.record_approval(approval)


class AuthorityBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "fr.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def _rt(self, now=None):
        clock = FixedClock(now) if now else None
        return MissionRuntime(db_path=self.db, adapter_mode="live_readonly", clock=clock)

    # -- no live path can mint an ALLOW ---------------------------------
    def test_no_live_code_path_constructs_an_approval(self):
        """Static article: the only Approval(...) built in the mission is fixture-only.

        A behavioural test can only show that the paths it walked did not
        self-approve. This walks the source instead, so a future edit that adds
        a second construction site has to come past this test.
        """
        tree = ast.parse(LH.read_text())
        sites = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Approval"
        ]
        self.assertEqual(len(sites), 1, "unexpected Approval construction site")
        enclosing = [
            fn.name for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef)
            and any(s is site for site in sites for s in ast.walk(fn))
        ]
        self.assertIn("_record_fixture_approval", enclosing)

    def test_no_approval_parks_and_creates_nothing(self):
        rt = self._rt()
        organs, gov = _organs()
        res = _run(rt, organs, key="park")
        self.assertEqual(res["status"], "awaiting_approval")
        self.assertEqual(res["state"], "AWAITING_APPROVAL")
        self.assertFalse(res["self_approval_created"])
        self.assertEqual(rt.store.list_approvals(res["mission_id"]), [])
        self.assertEqual(rt.store.list_receipts(res["mission_id"]), [])
        # Governance was consulted before the mission parked.
        self.assertEqual(len(gov.calls), 1)

    def test_a_self_requested_approval_is_refused(self):
        """The exact shape the old gate minted, planted in the store, is ignored."""
        rt = self._rt()
        organs, _ = _organs()
        mid = _run(rt, organs, key="self")["mission_id"]
        _external_allow(rt, mid, requested_by=MISSION_ACTOR, decided_by="founder",
                        expiry="2099-01-01T00:00:00.000Z", key="self")
        res = _run(self._rt(), organs, key="self")
        self.assertEqual(res["status"], "awaiting_approval")

    # -- a genuine external decision ------------------------------------
    def test_external_allow_is_usable(self):
        rt = self._rt()
        organs, gov = _organs()
        mid = _run(rt, organs, key="ok")["mission_id"]
        _external_allow(rt, mid, key="ok")
        res = _run(self._rt(), organs, key="ok")
        self.assertEqual(res["status"], "partial")
        self.assertEqual(
            MissionRuntime(db_path=self.db).get_mission(mid).current_state,
            MissionState.EXECUTING,
        )

    def test_external_reject_cannot_continue(self):
        rt = self._rt()
        organs, _ = _organs()
        mid = _run(rt, organs, key="no")["mission_id"]
        _external_allow(rt, mid, decision=ApprovalDecision.DENY, key="no")
        res = _run(self._rt(), organs, key="no")
        self.assertEqual(res["status"], "awaiting_approval")

    # -- binding: every mismatch parks ----------------------------------
    def test_binding_mismatches_are_all_refused(self):
        for label, kwargs in (
            ("scope", {"scope": "mission_transition:SOMETHING:ELSE"}),
            ("action", {"action": "transition:AWAITING_APPROVAL->EXECUTING"}),
            ("mission", {"mission": "msn_someone_else"}),
            ("expired", {"expiry": "2026-07-19T00:10:00.000Z"}),
        ):
            with self.subTest(mismatch=label):
                tmp = tempfile.TemporaryDirectory()
                db = str(Path(tmp.name) / "fr.sqlite")
                t0, t1 = "2026-07-19T00:00:00.000Z", "2026-07-19T01:00:00.000Z"
                rt = MissionRuntime(db_path=db, adapter_mode="live_readonly",
                                    clock=FixedClock(t0))
                organs, _ = _organs()
                mid = _run(rt, organs, key=label)["mission_id"]
                if label != "mission":
                    _external_allow(rt, mid, key=label, **kwargs)
                # The expiry court consumes an hour later: valid when recorded,
                # expired when presented.
                later = MissionRuntime(db_path=db, adapter_mode="live_readonly",
                                       clock=FixedClock(t1))
                res = _run(later, organs, key=label)
                self.assertEqual(res["status"], "awaiting_approval")
                tmp.cleanup()

    def test_consumed_approval_replay_is_denied(self):
        rt = self._rt()
        organs, _ = _organs()
        mid = _run(rt, organs, key="once")["mission_id"]
        _external_allow(rt, mid, key="once")
        self.assertEqual(_run(self._rt(), organs, key="once")["status"], "partial")

        rt2 = self._rt()
        stored = [a for a in rt2.store.list_approvals(mid)]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].decision, ApprovalDecision.CONSUMED)
        self.assertTrue(stored[0].consumed_by)

        # Re-presenting the consumed approval for the same gated transition is
        # refused by the consumption contract, not merely ignored.
        rt2.transition(mid, MissionState.PAUSED, reason="park_for_replay_court")
        rt2.transition(mid, MissionState.AWAITING_APPROVAL, reason="replay_court")
        with self.assertRaises(FounderRuntimeError):
            rt2.transition(mid, MissionState.QUEUED, reason="approved_read_only",
                           approval_id=stored[0].approval_id)

    # -- Governance closes the gate on every failure mode ----------------
    def test_governance_failure_modes_all_fail_closed(self):
        for behaviour in ("deny", "down", "timeout", "malformed"):
            with self.subTest(governance=behaviour):
                tmp = tempfile.TemporaryDirectory()
                db = str(Path(tmp.name) / "fr.sqlite")
                rt = MissionRuntime(db_path=db, adapter_mode="live_readonly")
                organs, _ = _organs(behaviour)
                with self.assertRaises(AuthorityDenied):
                    _run(rt, organs, key=behaviour)
                # Nothing was approved and nothing ran.
                missions = rt.store.list_missions()
                for m in missions:
                    self.assertEqual(rt.store.list_approvals(m.mission_id), [])
                    self.assertEqual(rt.store.list_receipts(m.mission_id), [])
                tmp.cleanup()

    def test_governance_denial_survives_a_valid_external_approval(self):
        """Both authorities are required; a founder ALLOW cannot override policy."""
        rt = self._rt()
        organs, _ = _organs()
        mid = _run(rt, organs, key="both")["mission_id"]
        _external_allow(rt, mid, key="both")
        denying, _ = _organs("deny")
        with self.assertRaises(AuthorityDenied):
            _run(self._rt(), denying, key="both")
        self.assertEqual(
            MissionRuntime(db_path=self.db).get_mission(mid).current_state,
            MissionState.AWAITING_APPROVAL,
        )

    # -- interruption and restart ----------------------------------------
    def test_resume_re_enters_the_gate_and_restart_preserves_provenance(self):
        rt = self._rt()
        organs, gov = _organs()
        first = _run(rt, organs, key="resume")
        mid = first["mission_id"]

        # Repeated resume while parked: still parked, still nothing minted.
        for _ in range(3):
            again = _run(self._rt(), organs, key="resume")
            self.assertEqual(again["mission_id"], mid)
            self.assertEqual(again["status"], "awaiting_approval")
            self.assertEqual(rt.store.list_approvals(mid), [])
        # Each resume re-asked Governance rather than inheriting a pass.
        self.assertEqual(len(gov.calls), 4)

        _external_allow(rt, mid, key="resume")
        after = _run(self._rt(), organs, key="resume")
        self.assertEqual(after["mission_id"], mid)
        self.assertEqual(after["status"], "partial")

        # Restart: the provenance of the decision is durable and still external.
        stored = MissionRuntime(db_path=self.db).store.list_approvals(mid)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].requested_by, "founder")
        self.assertEqual(stored[0].decided_by, "jeramie")
        self.assertNotEqual(stored[0].requested_by, MISSION_ACTOR)

    # -- the live organ set must carry Governance -------------------------
    def test_live_organ_set_without_governance_is_refused(self):
        with self.assertRaises(FounderRuntimeError):
            OrganReader("live", organs={"mother": object(), "github": object(),
                                        "vault": object(), "qdrant": object()},
                        executor=object())


if __name__ == "__main__":
    unittest.main()
