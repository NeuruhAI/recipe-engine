"""T31B: the founder's decision, made verifiable by someone other than its beneficiary.

T31A closed the obvious hole — a live mission could no longer mint its own
ALLOW.  What it left was subtler.  The approval the mission looked for was a row
in this runtime's own SQLite store, written by this runtime's own code.  It was
external by convention: a different function, running in a different process,
had put it there.  Nothing in the artifact itself said so, and nothing outside
this runtime could tell an externally decided approval from one the mission's
own process had written.

So this lane makes the decision carry its own proof.  The external channel signs
it with a key the mission role does not hold and refuses to run while holding;
Governance verifies that signature against the runtime version the mission
attests, spends it exactly once on its own ledger, and answers.  A mission that
cannot produce a verified authorization parks, the same way it parks with no
decision at all.

Three things have to be true for that to be worth anything, and each has its own
articles below:

  * the two implementations of the canonical form must agree BYTE FOR BYTE, or
    every honest signature is rejected and every rejection looks like an attack;
  * the mission role must not be able to sign, and must fail closed rather than
    proceed if it finds it could;
  * every way the authorization can fail must close the gate, including the ways
    that look like answers.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.governance_authorization import (
    CanonicalizationError,
    approval_signing_payload,
    authorization_facts,
    canonicalize,
    hash_canonical,
    scope_key_for,
    sign_founder_approval,
    unsigned_scope_once_approval,
)
from recipe_engine.founder_runtime.api.founder_signing import (
    ENV_FOUNDER_SECRET,
    ENV_FOUNDER_SECRET_FILE,
    FounderApprovalSigner,
    secret_is_present,
)
from recipe_engine.founder_runtime.api.service import MissionService
from recipe_engine.founder_runtime.clock import FixedClock
from recipe_engine.founder_runtime.contracts.approval import ApprovalDecision
from recipe_engine.founder_runtime.errors import AuthorityDenied
from recipe_engine.founder_runtime.missions.long_horizon import (
    GOV_APPROVAL_REF_PREFIX,
    authorization_scope_key,
    governance_approval_from,
    run_long_horizon_mission,
)
from recipe_engine.founder_runtime.runtime.approvals import gated_action_for
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.live_adapters.composition import (
    FounderSecretVisibleToMission,
    assert_mission_cannot_sign,
)

GATE_ACTION, GATE_SCOPE = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
SOURCE_SHA = "949a8b8c99f2b111edf8c734f2cfcad89c47bdf2"
OTHER_SHA = "e64c545adccf47394b1e45e860751816753617b4"
HARNESS_SIGNING_KEY = "t31b-harness-signing-key-not-a-credential"

LIVE_ADAPTERS = ROOT / "recipe_engine" / "live_adapters"
GOV_REPO = Path(os.path.expanduser("~")) / "neuruh-repos" / "NeuruhAI" / "autonomous-governance-core"


# --------------------------------------------------------------- stub organs
class _Result:
    def __init__(self, status, payload=None, error_code=None):
        self.status = status
        self.payload = payload or {}
        self.error_code = error_code
        self.evidence_refs = []


class _Governance:
    """Stands in for the live Governance organ across both of its surfaces."""

    def __init__(self, behaviour="allow"):
        self.behaviour = behaviour
        self.calls = []
        self.authorizations = []

    def decide(self, action, *, trusted_authority_class, trusted_policy_version, meta):
        self.calls.append(action)
        return _Result("success", {"audit_id": "audit_ok", "decision": "allow"})

    def authorize(self, facts, *, founder_approval, meta):
        self.authorizations.append({"facts": dict(facts), "founder_approval": founder_approval})
        if self.behaviour == "down":
            raise ConnectionRefusedError("governance unreachable")
        if self.behaviour == "timeout":
            return _Result("timeout", error_code="TIMEOUT")
        if self.behaviour == "malformed":
            return _Result("success", {"unexpected": True})
        if self.behaviour == "deny":
            return _Result("success", {"authorization_id": "auth_x", "decision": "deny",
                                       "reason_code": "APPROVAL_SOURCE_MISMATCH"})
        if self.behaviour == "founder_required":
            return _Result("success", {"authorization_id": "auth_f", "decision": "founder_required",
                                       "reason_code": "FOUNDER_REQUIRED_NO_GRANT"})
        if self.behaviour == "no_reference":
            return _Result("success", {"decision": "allow"})
        return _Result("success", {"authorization_id": "auth_ok", "decision": "allow",
                                   "reason_code": "FOUNDER_APPROVED_SCOPE_ONCE", "risk_tier": "R0"})


def _organs(behaviour="allow"):
    gov = _Governance(behaviour)
    return {"mother": object(), "github": object(), "vault": object(),
            "qdrant": object(), "governance": gov}, gov


def _run(rt, organs, *, key, max_stages=0, source_sha=SOURCE_SHA):
    return run_long_horizon_mission(
        rt, mode="live", organs=organs, executor=object(),
        creation_idempotency_key=key, max_stages=max_stages, source_sha=source_sha,
    )


# ============================================================ the two forms ==
class CanonicalFormAgreement(unittest.TestCase):
    """The Python and Node canonical forms must be the same bytes.

    This is a second implementation of somebody else's contract. If it drifts,
    every honest signature is rejected and every rejection looks like an attack
    — so the agreement is pinned to a frozen vector that a drift breaks, and
    re-derived live from the Node source whenever it is on the machine.
    """

    # Produced by autonomous-governance-core's own canonical.mjs / authorize.mjs
    # over the fixed inputs in _vector(). These are outputs of a public hash over
    # public inputs and a throwaway key that authorizes nothing anywhere — the
    # secret-shaped thing here is deliberate and inert, and it has to be a
    # literal, because a vector the test derives is a vector that drifts with the
    # code it is supposed to pin.
    FROZEN_SECRET = "the-shared-probe-secret-value-0123"  # gitleaks:allow — inert test vector input
    FROZEN_FACTS_HASH = "4d65879bc352fe73cad95c09ae7cd5026178bd58c749db64c64a3415387f57e4"  # gitleaks:allow — sha256 output
    FROZEN_SCOPE_KEY = "1c30b887efc89b72801c9fac18b9482431794bcfcc5589a56f60eec78e97fd05"  # gitleaks:allow — sha256 output
    FROZEN_SIGNATURE = "dfbbdf0d81485c19a2b2f6d01aabd47397ed0f74b91d563fd6952690e87ca096"  # gitleaks:allow — hmac over the above

    def _vector(self):
        facts = authorization_facts(
            lane="founder-runtime-lane", action_id="act_t31b_probe",
            summary="Authorize the read-only long-horizon mission gate.",
            source_sha=SOURCE_SHA,
            nonce="t31bnonce0123456789abcdef", issued_at="2026-08-20T15:00:00.000Z")
        unsigned = unsigned_scope_once_approval(
            approval_id="faid_probe", approver_id="founder",
            scope_key=scope_key_for(facts), source_sha=facts["source_sha"],
            nonce="fapprv0123456789abcdef",
            issued_at="2026-08-20T15:00:00.000Z", expires_at="2026-08-20T15:10:00.000Z")
        return facts, unsigned

    def test_frozen_vector_still_holds(self):
        facts, unsigned = self._vector()
        self.assertEqual(hash_canonical(facts), self.FROZEN_FACTS_HASH)
        self.assertEqual(scope_key_for(facts), self.FROZEN_SCOPE_KEY)
        self.assertEqual(
            sign_founder_approval(unsigned, self.FROZEN_SECRET)["signature"],
            self.FROZEN_SIGNATURE,
        )

    def test_node_derives_the_same_bytes(self):
        node = shutil.which("node")
        if node is None or not (GOV_REPO / "src" / "authorize.mjs").exists():
            self.skipTest("Governance source or node unavailable; frozen vector still pinned above")
        facts, unsigned = self._vector()
        script = (
            f'import {{ canonicalize, hashCanonical }} from "{GOV_REPO}/src/canonical.mjs";\n'
            f'import {{ scopeKeyFor, signFounderApproval }} from "{GOV_REPO}/src/authorize.mjs";\n'
            'const facts = JSON.parse(process.argv[1]);\n'
            'const unsigned = JSON.parse(process.argv[2]);\n'
            'const payload = { schema_version: unsigned.schema_version, approval_id: unsigned.approval_id,'
            ' approver_id: unsigned.approver_id, mode: unsigned.mode, bound: unsigned.bound };\n'
            'console.log(JSON.stringify({ facts: canonicalize(facts), facts_hash: hashCanonical(facts),'
            ' payload: canonicalize(payload), scope_key: scopeKeyFor(facts),'
            f' signature: signFounderApproval(unsigned, {json.dumps(self.FROZEN_SECRET)}).signature }}));\n'
        )
        out = subprocess.run(
            [node, "--input-type=module", "-e", script, "--", json.dumps(facts), json.dumps(unsigned)],
            capture_output=True, text=True,
        )
        self.assertEqual(out.returncode, 0, out.stderr[:600])
        js = json.loads(out.stdout)
        self.assertEqual(canonicalize(facts), js["facts"])
        self.assertEqual(hash_canonical(facts), js["facts_hash"])
        self.assertEqual(canonicalize(approval_signing_payload(unsigned)), js["payload"])
        self.assertEqual(scope_key_for(facts), js["scope_key"])
        self.assertEqual(sign_founder_approval(unsigned, self.FROZEN_SECRET)["signature"], js["signature"])

    def test_ambiguous_values_are_refused_rather_than_normalized(self):
        for value, label in (
            ({"a": 1.5}, "float"),
            ({"a": "de" + chr(0x301) + "composed"}, "non-NFC"),
            ({"a": "has" + chr(0) + "nul"}, "NUL"),
            ({"a": {"__proto__": "x"}}, "forbidden key"),
        ):
            with self.subTest(value=label):
                with self.assertRaises(CanonicalizationError):
                    canonicalize(value)


# ================================================== the secret and its side ==
class SecretSeparation(unittest.TestCase):
    """The mission role never holds the key, and refuses to run if it could."""

    def test_only_the_api_boundary_looks_for_the_signing_secret(self):
        """Static article: exactly one module reads the founder secret."""
        package = ROOT / "recipe_engine"
        readers = []
        for path in package.rglob("*.py"):
            if ENV_FOUNDER_SECRET in path.read_text():
                readers.append(str(path.relative_to(package)))
        self.assertEqual(
            sorted(readers),
            ["founder_runtime/api/founder_signing.py"],
            "the founder signing secret is named outside the decision channel; the "
            "mission-side guard imports the name rather than restating it, so there is "
            "exactly one place that knows what to look for",
        )

    def test_the_mission_side_holds_no_signing_capability(self):
        """Static article: nothing under live_adapters can sign anything.

        The adapter SUBMITS an approval. Behavioural tests can only show that the
        paths they walked did not sign; this walks the source, so a future edit
        that imports a signer has to come past this test.
        """
        offenders = []
        for path in LIVE_ADAPTERS.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.ImportFrom):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                for name in names:
                    if "sign" in name or name in ("hmac",):
                        offenders.append(f"{path.name}:{name}")
        self.assertEqual(offenders, [])

    def test_composition_refuses_a_mission_process_that_can_sign(self):
        assert_mission_cannot_sign({})
        for env in ({ENV_FOUNDER_SECRET: "x" * 32}, {ENV_FOUNDER_SECRET_FILE: "/tmp/key"}):
            with self.subTest(env=sorted(env)[0]):
                with self.assertRaises(FounderSecretVisibleToMission):
                    assert_mission_cannot_sign(env)

    def test_secret_presence_never_discloses_the_value(self):
        self.assertTrue(secret_is_present({ENV_FOUNDER_SECRET: "x" * 32}))
        self.assertFalse(secret_is_present({}))
        self.assertIs(secret_is_present({ENV_FOUNDER_SECRET: "x" * 32}), True)

    def test_an_unconfigured_channel_cannot_sign_and_says_so(self):
        signer = FounderApprovalSigner("")
        self.assertFalse(signer.configured)
        with self.assertRaises(RuntimeError):
            signer.sign_scope_once(scope_key="a" * 64, source_sha=SOURCE_SHA,
                                   issued_at="2026-08-20T15:00:00.000Z",
                                   expires_at="2026-08-20T15:10:00.000Z")

    def test_a_too_short_secret_is_refused_rather_than_signed_weakly(self):
        self.assertFalse(FounderApprovalSigner("short").configured)

    def test_the_mission_role_is_caught_by_one_or_the_other(self):
        """The dichotomy that makes self-signing unreachable, not just unused.

        A mission process is in exactly one of two states. Either the key is
        absent, in which case the signer it could import is unconfigured and
        raises rather than signing — or the key is present, in which case the
        live organ set refuses to be built at all. There is no third state where
        a mission both runs live and can sign.
        """
        # Key absent: importable, unconfigured, refuses.
        assert_mission_cannot_sign({})
        unconfigured = FounderApprovalSigner("")
        self.assertFalse(unconfigured.configured)
        with self.assertRaises(RuntimeError):
            unconfigured.sign_scope_once(
                scope_key="a" * 64, source_sha=SOURCE_SHA,
                issued_at="2026-08-20T15:00:00.000Z", expires_at="2026-08-20T15:10:00.000Z",
            )
        # Key present: the live mission cannot start.
        with self.assertRaises(FounderSecretVisibleToMission):
            assert_mission_cannot_sign({ENV_FOUNDER_SECRET: "y" * 40})


# ================================================== the channel and the gate ==
class ExternalChannelMintsTheArtifact(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "fr.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def _rt(self, now=None):
        return MissionRuntime(db_path=self.db, adapter_mode="live_readonly",
                              clock=FixedClock(now) if now else None)

    def _service(self, *, configured=True):
        return MissionService(self.db, signer=FounderApprovalSigner(
            HARNESS_SIGNING_KEY if configured else ""))

    def _park(self, key="chain"):
        organs, gov = _organs()
        res = _run(self._rt(), organs, key=key)
        self.assertEqual(res["status"], "awaiting_approval")
        return res["mission_id"], organs, gov

    def _version(self, service, mission_id):
        return service.get_mission(mission_id)["envelope"]["version"]

    def test_the_mission_publishes_exactly_what_the_channel_signs(self):
        mid, _, _ = self._park()
        rt = self._rt()
        published = rt.published_authorization_request(mid)
        self.assertIsNotNone(published, "a parked mission must publish what it will ask for")
        self.assertEqual(published["source_sha"], SOURCE_SHA)
        self.assertEqual(published["action"], GATE_ACTION)
        self.assertEqual(published["scope"], GATE_SCOPE)
        # Both processes derive the same scope from mission coordinates alone.
        self.assertEqual(
            published["scope_key"],
            authorization_scope_key(mid, action=GATE_ACTION, scope=GATE_SCOPE, source_sha=SOURCE_SHA),
        )

    def test_republishing_is_idempotent_across_resumes(self):
        mid, organs, _ = self._park("idem")
        for _ in range(3):
            _run(self._rt(), organs, key="idem")
        events = self._rt().store.list_events(mid)
        published = [e for e in events if "authorization_request" in (e.payload or {})]
        self.assertEqual(len(published), 1, "a resumed mission must not accumulate requests")

    def test_approve_gate_mints_a_bound_signed_artifact(self):
        mid, _, _ = self._park("mint")
        service = self._service()
        result = service.approve_mission_gate(
            mid, reason="read-only observation approved", idempotency_key="idem-mint",
            expected_version=self._version(service, mid),
        )
        service.close()

        approval = result["approval"]
        self.assertEqual(approval["decision"], "allow")
        self.assertEqual(approval["exact_requested_action"], GATE_ACTION)

        refs = [r for r in approval["evidence_refs"] if r.startswith(GOV_APPROVAL_REF_PREFIX)]
        self.assertEqual(len(refs), 1, "a gate ALLOW must carry exactly one signed artifact")
        artifact = json.loads(refs[0][len(GOV_APPROVAL_REF_PREFIX):])

        self.assertEqual(artifact["schema_version"], "gov.founder.approval.v1")
        self.assertEqual(artifact["mode"], "scope_once")
        self.assertEqual(artifact["approver_id"], "founder")
        self.assertEqual(artifact["bound"]["source_sha"], SOURCE_SHA)
        self.assertEqual(
            artifact["bound"]["scope_key"],
            authorization_scope_key(mid, action=GATE_ACTION, scope=GATE_SCOPE, source_sha=SOURCE_SHA),
        )
        self.assertEqual(artifact["bound"]["risk_tier"], "R0")
        self.assertTrue(artifact["signature"])
        # The signature verifies against the channel's key and nothing else.
        self.assertEqual(
            sign_founder_approval(
                {k: v for k, v in artifact.items() if k != "signature"}, HARNESS_SIGNING_KEY
            )["signature"],
            artifact["signature"],
        )

    def test_the_artifact_expires(self):
        mid, _, _ = self._park("ttl")
        service = self._service()
        service.approve_mission_gate(mid, reason="ok", idempotency_key="idem-ttl",
                                     expected_version=self._version(service, mid))
        artifact = governance_approval_from(self._rt().store.list_approvals(mid)[0])
        service.close()
        self.assertLess(artifact["bound"]["issued_at"], artifact["bound"]["expires_at"])

    def test_a_rejection_mints_nothing(self):
        mid, _, _ = self._park("reject")
        service = self._service()
        result = service.reject_mission_gate(
            mid, reason="not now", idempotency_key="idem-reject",
            expected_version=self._version(service, mid),
        )
        service.close()
        self.assertEqual(result["approval"]["decision"], "deny")
        self.assertEqual(
            [r for r in result["approval"]["evidence_refs"] if r.startswith(GOV_APPROVAL_REF_PREFIX)],
            [], "there is no such thing as a signed refusal to spend",
        )

    def test_an_unconfigured_channel_records_the_decision_but_mints_nothing(self):
        mid, organs, _ = self._park("nokey")
        service = self._service(configured=False)
        result = service.approve_mission_gate(
            mid, reason="ok", idempotency_key="idem-nokey",
            expected_version=self._version(service, mid),
        )
        service.close()
        self.assertEqual(result["approval"]["decision"], "allow")
        self.assertEqual(
            [r for r in result["approval"]["evidence_refs"] if r.startswith(GOV_APPROVAL_REF_PREFIX)], [],
        )
        # And the live mission parks on it rather than proceeding unsigned.
        self.assertEqual(_run(self._rt(), organs, key="nokey")["status"], "awaiting_approval")

    def test_the_full_chain_authorizes_once_and_advances(self):
        mid, organs, gov = self._park("full")
        service = self._service()
        service.approve_mission_gate(mid, reason="ok", idempotency_key="idem-full",
                                     expected_version=self._version(service, mid))
        service.close()

        res = _run(self._rt(), organs, key="full")
        self.assertEqual(res["status"], "partial")
        self.assertEqual(self._rt().get_mission(mid).current_state, MissionState.EXECUTING)

        # Exactly one authorization, and it carried the founder's artifact.
        self.assertEqual(len(gov.authorizations), 1)
        submitted = gov.authorizations[0]
        self.assertIsNotNone(submitted["founder_approval"])
        self.assertEqual(submitted["founder_approval"]["mode"], "scope_once")
        self.assertEqual(submitted["facts"]["source_sha"], SOURCE_SHA)
        self.assertEqual(submitted["facts"]["component"], "founder_runtime")
        self.assertEqual(submitted["facts"]["action_class"], "status")
        self.assertEqual(submitted["facts"]["externality"], "none")
        self.assertEqual(submitted["facts"]["spend_usd"], 0)
        self.assertEqual(submitted["facts"]["production_effect"], "read")

        # The approval is spent here too.
        stored = self._rt().store.list_approvals(mid)[0]
        self.assertEqual(stored.decision, ApprovalDecision.CONSUMED)

    def test_resuming_past_the_gate_does_not_authorize_again(self):
        mid, organs, gov = self._park("again")
        service = self._service()
        service.approve_mission_gate(mid, reason="ok", idempotency_key="idem-again",
                                     expected_version=self._version(service, mid))
        service.close()
        _run(self._rt(), organs, key="again")
        self.assertEqual(len(gov.authorizations), 1)
        for _ in range(3):
            _run(self._rt(), organs, key="again")
        self.assertEqual(len(gov.authorizations), 1, "a resumed mission re-authorized itself")
        self.assertEqual(len(self._rt().store.list_approvals(mid)), 1)

    def test_a_source_advance_is_presented_to_governance_not_resolved_locally(self):
        """Version binding is Governance's to enforce, and the mission must let it.

        A founder decision made about one runtime version is not a decision about
        another. When the attested SHA advances, the mission does not quietly
        reuse the old artifact as if it still applied, and it does not silently
        drop it either: it presents both — the new attested identity and the
        approval that was signed for the old one — so the party that can verify
        the signature is the party that refuses it.
        """
        mid, _, _ = self._park("advance")
        service = self._service()
        service.approve_mission_gate(mid, reason="ok", idempotency_key="idem-advance",
                                     expected_version=self._version(service, mid))
        service.close()

        artifact = governance_approval_from(self._rt().store.list_approvals(mid)[0])
        self.assertEqual(artifact["bound"]["source_sha"], SOURCE_SHA)

        advanced, gov = _organs()
        _run(self._rt(), advanced, key="advance", source_sha=OTHER_SHA)

        self.assertEqual(len(gov.authorizations), 1)
        submitted = gov.authorizations[0]
        self.assertEqual(submitted["facts"]["source_sha"], OTHER_SHA)
        self.assertEqual(submitted["founder_approval"]["bound"]["source_sha"], SOURCE_SHA)

        # The SCOPE is unchanged — deliberately. Governance's scope key covers
        # the action coordinates and not the runtime version, which is exactly
        # why version binding needs its own field: without bound.source_sha, an
        # approval for the same lane and target would survive a source advance
        # unnoticed, because the scope alone cannot tell the versions apart.
        self.assertEqual(
            submitted["founder_approval"]["bound"]["scope_key"],
            authorization_scope_key(mid, action=GATE_ACTION, scope=GATE_SCOPE, source_sha=OTHER_SHA),
        )
        self.assertNotEqual(
            submitted["founder_approval"]["bound"]["source_sha"],
            submitted["facts"]["source_sha"],
            "the divergence Governance must refuse was hidden rather than presented",
        )

    def test_an_interruption_before_approval_fabricates_nothing(self):
        mid, organs, gov = self._park("interrupt")
        # Whatever the process did before it died, resuming finds no approval,
        # no signature and no authorization — and asks for none of them.
        for _ in range(2):
            res = _run(self._rt(), organs, key="interrupt")
            self.assertEqual(res["status"], "awaiting_approval")
        self.assertEqual(self._rt().store.list_approvals(mid), [])
        self.assertEqual(gov.authorizations, [], "authorization was attempted without an approval")
        # The published request is unchanged: same mission, same action digest.
        published = self._rt().published_authorization_request(mid)
        self.assertEqual(published["mission_id"], mid)
        self.assertEqual(
            published["scope_key"],
            authorization_scope_key(mid, action=GATE_ACTION, scope=GATE_SCOPE, source_sha=SOURCE_SHA),
        )


# ============================================================== fail closed ==
class AuthorizationFailsClosed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "fr.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def _rt(self):
        return MissionRuntime(db_path=self.db, adapter_mode="live_readonly")

    def _approved(self, key, behaviour):
        organs, gov = _organs(behaviour)
        allowing, _ = _organs()
        mid = _run(self._rt(), allowing, key=key)["mission_id"]
        service = MissionService(self.db, signer=FounderApprovalSigner(HARNESS_SIGNING_KEY))
        service.approve_mission_gate(
            mid, reason="ok", idempotency_key="idem-" + key,
            expected_version=service.get_mission(mid)["envelope"]["version"],
        )
        service.close()
        return mid, organs, gov

    def test_every_authorization_failure_mode_closes_the_gate(self):
        for behaviour in ("deny", "founder_required", "down", "timeout", "malformed", "no_reference"):
            with self.subTest(authorization=behaviour):
                self.tearDown()
                self.setUp()
                mid, organs, _ = self._approved(behaviour, behaviour)
                with self.assertRaises(AuthorityDenied):
                    _run(self._rt(), organs, key=behaviour)
                # Nothing ran, and the approval was NOT spent on a refusal.
                self.assertEqual(self._rt().store.list_receipts(mid), [])
                self.assertEqual(self._rt().get_mission(mid).current_state,
                                 MissionState.AWAITING_APPROVAL)
                self.assertEqual(self._rt().store.list_approvals(mid)[0].decision,
                                 ApprovalDecision.ALLOW)

    def test_a_governance_organ_that_cannot_authorize_is_a_denial(self):
        organs, _ = _organs()
        allowing, _ = _organs()
        mid = _run(self._rt(), allowing, key="noauthz")["mission_id"]
        service = MissionService(self.db, signer=FounderApprovalSigner(HARNESS_SIGNING_KEY))
        service.approve_mission_gate(
            mid, reason="ok", idempotency_key="idem-noauthz",
            expected_version=service.get_mission(mid)["envelope"]["version"],
        )
        service.close()

        class _NoAuthorize:
            def decide(self, action, *, trusted_authority_class, trusted_policy_version, meta):
                return _Result("success", {"audit_id": "a", "decision": "allow"})

        organs["governance"] = _NoAuthorize()
        with self.assertRaises(AuthorityDenied):
            _run(self._rt(), organs, key="noauthz")

    def test_a_malformed_or_missing_artifact_parks_rather_than_being_submitted(self):
        for label, refs in (
            ("absent", []),
            ("not json", [GOV_APPROVAL_REF_PREFIX + "{not json"]),
            ("wrong schema", [GOV_APPROVAL_REF_PREFIX + json.dumps({"schema_version": "other", "mode": "scope_once", "signature": "x"})]),
            ("wrong mode", [GOV_APPROVAL_REF_PREFIX + json.dumps({"schema_version": "gov.founder.approval.v1", "mode": "session_scope", "signature": "x"})]),
            ("unsigned", [GOV_APPROVAL_REF_PREFIX + json.dumps({"schema_version": "gov.founder.approval.v1", "mode": "scope_once"})]),
        ):
            with self.subTest(artifact=label):
                class _Approval:
                    evidence_refs = refs
                self.assertIsNone(governance_approval_from(_Approval()))


if __name__ == "__main__":
    unittest.main()
