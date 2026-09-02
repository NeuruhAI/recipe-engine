"""The approval resolver: AWAITING_APPROVAL -> consequence -> Governance -> transition.

Two systemic parking defects, and the properties that close them without
opening anything else:

  1. A safe, reversible, internal mission no longer waits for a human. It is
     QUEUED on Governance's ALLOW, spending an approval whose decider IS
     Governance (named by authorization id, backed by its receipt). Nobody
     types ``caller_approved=true``; nothing is signed; the class is never
     lowered.
  2. An irreversible / external / spend / destructive mission stays parked,
     and the exact founder gate it waits on is surfaced: Governance's one
     concise ``founder_prompt`` plus the published scope/source-SHA/tier the
     founder's signature must bind to.

Governance here is a stand-in that mirrors ``authorize.mjs`` tier semantics
for the resolver's facts; the real surface is pinned by the node court in
autonomous-governance-core (tests/approval-promotion-seams.test.mjs).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import threading
import time

import pytest

from recipe_engine.founder_runtime.adapters.governance_authorization import (
    approval_signing_payload,
    canonicalize,
    hash_canonical,
    scope_key_for,
)
from recipe_engine.founder_runtime.api.founder_signing import FounderApprovalSigner
from recipe_engine.founder_runtime.api.service import ApiError, MissionService
from recipe_engine.founder_runtime.contracts.approval import ApprovalDecision
from recipe_engine.founder_runtime.contracts.authority import AuthorityClass, AuthorityRequirement
from recipe_engine.founder_runtime.runtime import approval_resolver as resolver
from recipe_engine.founder_runtime.runtime.approvals import gated_action_for
from recipe_engine.founder_runtime.state.states import MissionState

SHA = "38c359fc6c5eb6efb6398b7426d8a85cda046d42"
SECRET = "resolver-court-founder-secret-0123456789"  # gitleaks:allow — inert test vector input
GATE_ACTION, GATE_SCOPE = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)

TIER = {
    "status": "R0", "read": "R0", "query": "R0", "health": "R0", "retrieval": "R0", "git_inspect": "R0",
    "artifact_write": "R1", "report_write": "R1",
    "test_run": "R2", "git_commit": "R2", "repo_edit": "R2", "build": "R2", "git_push": "R2",
    "merge": "R3", "deploy": "R3",
    "publish": "R4", "external_message": "R4", "spend": "R4",
    "broad_delete": "R5", "force_push": "R5",
}
RANK = {t: i for i, t in enumerate(["R0", "R1", "R2", "R3", "R4", "R5"])}


class _Result:
    def __init__(self, status, payload=None, error_code=None):
        self.status = status
        self.payload = payload or {}
        self.error_code = error_code
        self.evidence_refs = []


class _Governance:
    """Mirrors autonomous-governance-core authorize.mjs for the resolver's facts.

    R0 auto-allows; R1/R2 allow only inside a valid grant for the lane; R3 asks
    unless qualified; R4/R5 always ask (a grant never satisfies them); a
    presented founder approval is verified against these exact facts and, when
    valid, allows once. Nonces are one-time. Everything is recorded.
    """

    def __init__(self, *, deny_classes=(), unreachable=False, pinned_sha=SHA,
                 founder_secret=SECRET, adapter_failure=False):
        self.calls: list[dict] = []
        self.deny_classes = set(deny_classes)
        self.unreachable = unreachable
        self.pinned_sha = pinned_sha
        self.founder_secret = founder_secret
        self.adapter_failure = adapter_failure
        self.seen_nonces: set[str] = set()
        self.seq = 0

    def _env(self, facts, decision, reason_code, *, tier=None, prompt=None, approvers=()):
        self.seq += 1
        auth_id = f"auth_{self.seq:04d}"
        env = {
            "schema_version": "gov.authorization.v1",
            "authorization_id": auth_id,
            "action_id": facts["action_id"], "lane": facts["lane"], "component": facts["component"],
            "action_class": facts["action_class"], "target": facts["target"],
            "repository": facts.get("repository"), "branch": facts.get("branch"),
            "summary": facts["summary"],
            "risk_tier": tier, "risk_factors": [f"action class {facts['action_class']} is baseline {tier}."],
            "risk_model_version": "rm_test",
            "decision": decision, "reason_code": reason_code, "reasons": [reason_code],
            "grant_id": None, "approver_ids": list(approvers), "session_scope_used": False,
            "scope_key": scope_key_for(facts), "founder_prompt": prompt,
            "canonical_facts_hash": hash_canonical(facts), "facts_nonce": facts["nonce"],
            "issued_at": facts["issued_at"], "expires_at": "2099-01-01T00:00:00.000Z",
            "evidence_refs": [], "execution": "NOT_CLAIMED_BY_GOVERNANCE",
            "decision_hash": hashlib.sha256(auth_id.encode()).hexdigest(),
            "decision_receipt_hash": hashlib.sha256(("r" + auth_id).encode()).hexdigest(),
            "receipt_seq": self.seq,
        }
        if decision != "founder_required":
            self.seen_nonces.add(facts["nonce"])
        return _Result("success", env)

    def _prompt(self, facts, tier, why):
        return {"prompt_id": f"prompt_{facts['nonce'][-6:]}", "already_prompted": False,
                "action": f"{facts['action_class']}: {facts['summary']}",
                "target": f"{facts['target']['type']}:{facts['target']['id']}",
                "risk": tier, "why_human_required": why,
                "rollback": facts.get("rollback_plan") or facts["reversibility"],
                "spend_usd": facts["spend_usd"], "contact": facts["externality"],
                "production_effect": facts["production_effect"], "grant_expiry": None,
                "approve_once": True, "approve_scope_session": RANK[tier] <= 3,
                "scope_key": scope_key_for(facts)}

    def authorize(self, facts, *, founder_approval, meta, grant=None):
        self.calls.append({"facts": dict(facts), "founder_approval": founder_approval, "grant": grant,
                           "meta": meta})
        if self.unreachable:
            raise ConnectionRefusedError("governance down")
        if self.adapter_failure:
            return _Result("failure", {}, error_code="TRANSPORT_MALFORMED")
        if facts["nonce"] in self.seen_nonces:
            return self._env(facts, "deny", "NONCE_REPLAYED")
        if facts["source_sha"] != self.pinned_sha:
            return self._env(facts, "deny", "SOURCE_SHA_MISMATCH")
        tier = TIER[facts["action_class"]]
        if facts["action_class"] in self.deny_classes:
            return self._env(facts, "deny", "POLICY_BLOCKED", tier=tier)
        if founder_approval is not None:
            bound = founder_approval.get("bound") or {}
            sig = hmac.new(self.founder_secret.encode(), canonicalize(approval_signing_payload(founder_approval)).encode(),
                           hashlib.sha256).hexdigest()
            if founder_approval.get("signature") != sig:
                return self._env(facts, "deny", "APPROVAL_SIGNATURE_INVALID", tier=tier)
            if bound.get("scope_key") != scope_key_for(facts):
                return self._env(facts, "deny", "APPROVAL_CONTENT_MISMATCH", tier=tier)
            if bound.get("source_sha") != facts["source_sha"]:
                return self._env(facts, "deny", "APPROVAL_SOURCE_MISMATCH", tier=tier)
            if bound.get("risk_tier") != tier:
                return self._env(facts, "deny", "APPROVAL_RISK_MISMATCH", tier=tier)
            return self._env(facts, "allow", "FOUNDER_APPROVED_SCOPE_ONCE", tier=tier, approvers=("founder",))
        if tier == "R0":
            return self._env(facts, "allow", "AUTO_ALLOW_OBSERVE", tier=tier)
        if grant is not None and RANK[tier] <= 2:
            if grant["subject"]["lane"] != facts["lane"]:
                return self._env(facts, "deny", "GRANT_LANE_MISMATCH", tier=tier)
            if facts["action_class"] in grant["allowed_action_classes"]:
                code = "GRANT_BOUNDED_LOCAL" if tier == "R1" else "GRANT_BOUNDED_DEVELOPMENT"
                return self._env(facts, "allow", code, tier=tier, approvers=(grant["issuer"],))
        if tier == "R5":
            return self._env(facts, "founder_required", "FOUNDER_REQUIRED_DESTRUCTIVE", tier=tier,
                             prompt=self._prompt(facts, tier, "Destructive or privileged action requires interactive founder approval every time."))
        if tier == "R4":
            return self._env(facts, "founder_required", "FOUNDER_REQUIRED_EXTERNAL", tier=tier,
                             prompt=self._prompt(facts, tier, "External consequence always requires the founder."))
        return self._env(facts, "founder_required", "FOUNDER_REQUIRED_NO_GRANT", tier=tier,
                         prompt=self._prompt(facts, tier, "No valid FounderGrant covers this bounded action; the founder is asked once for this exact scope."))


def _grant(lane=resolver.RESOLVER_LANE, classes=("artifact_write", "test_run", "git_commit")):
    return {"schema_version": "gov.grant.v1", "grant_id": "grant_resolver_test", "issuer": "founder",
            "issued_at": "2026-09-01T00:00:00.000Z", "expires_at": "2026-09-30T00:00:00.000Z",
            "subject": {"lane": lane}, "allowed_repos": ["NeuruhAI/recipe-engine"], "allowed_paths": [],
            "allowed_action_classes": list(classes), "denied_action_classes": [], "allowed_services": [],
            "production_permission": False, "external_contact_permission": False, "spend_ceiling_usd": 0,
            "destructive_permission": False, "rollback_required": False, "receipt_required": True,
            "signature": "f" * 64}


def _service(tmp_path, name, **kw):
    kw.setdefault("governance", _Governance())
    kw.setdefault("attested_source_sha", SHA)
    svc = MissionService(str(tmp_path / f"{name}.sqlite"), **kw)
    return svc


def _create(svc, objective, key="k1"):
    return svc.create_mission(objective=objective, idempotency_key=key)


def _approvals(svc, mid):
    return svc.runtime.store.list_approvals(mid)


# ------------------------------------------------- consequence classification --
def test_consequence_map_is_closed_and_monotone():
    """Every trusted class maps to a Governance class at least as consequential."""
    order = ["status", "read", "artifact_write", "test_run", "git_commit", "publish", "broad_delete"]
    seen = []
    for cls in AuthorityClass:
        req = AuthorityRequirement(schema_version="fr.authority_req.v2", authority_class=cls, action="x",
                                   scope="s", reason="r", requires_approval=cls.value >= "A2")
        facts = resolver.resolver_facts("msn_x", req, action=GATE_ACTION, scope=GATE_SCOPE,
                                        source_sha=SHA, nonce="n" * 24, issued_at="2026-09-02T00:00:00.000Z")
        seen.append(facts["action_class"])
        assert facts["schema_version"] == "gov.action.facts.v1"
        assert facts["component"] == "founder_runtime"
        assert facts["lane"] == resolver.RESOLVER_LANE
        assert facts["source_sha"] == SHA
        assert facts["spend_usd"] == 0
        assert "*" not in facts["target"]["id"]
    assert seen == order
    tiers = [RANK[TIER[c]] for c in seen]
    assert tiers == sorted(tiers), "classes must never de-escalate"
    assert TIER[seen[-2]] == "R4" and TIER[seen[-1]] == "R5"


def test_resolver_facts_refuse_missing_source_sha():
    req = AuthorityRequirement(schema_version="fr.authority_req.v2", authority_class=AuthorityClass.A0,
                               action="x", scope="s", reason="r", requires_approval=False)
    with pytest.raises(ValueError):
        resolver.resolver_facts("m", req, action=GATE_ACTION, scope=GATE_SCOPE, source_sha="",
                                nonce="n" * 24, issued_at="2026-09-02T00:00:00.000Z")


# ----------------------------------------------------------- defect 1 closed --
def test_safe_read_only_mission_reaches_queued_without_anyone(tmp_path):
    gov = _Governance()
    svc = _service(tmp_path, "safe", governance=gov)
    res = _create(svc, "status check of the organism", key="safe-1")
    env = res["envelope"]
    assert env["state"] == "QUEUED"
    assert res["authority_resolution"]["outcome"] == resolver.RESOLVED_QUEUED
    assert res["authority_resolution"]["reason_code"] == "AUTO_ALLOW_OBSERVE"
    assert res["authority_resolution"]["risk_tier"] == "R0"
    # Governance was asked exactly once, with this runtime's identity, and no
    # human artifact was fabricated for it.
    assert len(gov.calls) == 1
    call = gov.calls[0]
    assert call["facts"]["source_sha"] == SHA
    assert call["facts"]["action_class"] == "read"
    assert call["founder_approval"] is None
    assert call["grant"] is None
    # The approval that opened the gate is Governance's, spent exactly once.
    approvals = _approvals(svc, env["mission_id"])
    assert len(approvals) == 1
    a = approvals[0]
    assert a.requested_by == resolver.RESOLVER_ACTOR
    assert a.decided_by == "governance:auth_0001"
    assert a.decision == ApprovalDecision.CONSUMED
    assert a.exact_requested_action == GATE_ACTION and a.scope == GATE_SCOPE
    assert any(r.startswith("governance:receipt:") for r in a.evidence_refs)
    assert env["authority"]["state"] == "APPROVED"
    assert env["authority"]["executed"] is False


def test_resolver_never_writes_a_human_decision(tmp_path):
    svc = _service(tmp_path, "nohuman")
    res = _create(svc, "status check", key="nh-1")
    for a in _approvals(svc, res["envelope"]["mission_id"]):
        assert a.decided_by not in ("jeramie", "founder", None, "")
        assert a.decided_by.startswith(resolver.GOVERNANCE_DECIDER_PREFIX)
        assert a.requested_by != "founder"


def test_resolver_holds_no_signing_path(tmp_path, monkeypatch):
    """Even a configured signer is never touched by the resolver."""
    signer = FounderApprovalSigner(SECRET)
    calls = []

    def _boom(*a, **k):
        calls.append(1)
        raise AssertionError("resolver must never sign")

    monkeypatch.setattr(signer, "sign_scope_once", _boom)
    svc = _service(tmp_path, "nosign", signer=signer)
    res = _create(svc, "status check", key="ns-1")
    assert res["envelope"]["state"] == "QUEUED"
    assert calls == []


def test_local_mutation_auto_allows_under_founder_grant(tmp_path):
    gov = _Governance()
    svc = _service(tmp_path, "grant", governance=gov, founder_grant=_grant())
    res = _create(svc, "edit the README draft locally", key="g-1")
    assert res["envelope"]["state"] == "QUEUED"
    r = res["authority_resolution"]
    assert r["reason_code"] == "GRANT_BOUNDED_LOCAL" and r["risk_tier"] == "R1"
    assert gov.calls[0]["grant"]["grant_id"] == "grant_resolver_test"
    assert gov.calls[0]["facts"]["action_class"] == "artifact_write"


# -------------------------------------------------- founder gate stays exact --
def test_local_mutation_parks_with_exact_founder_prompt_without_grant(tmp_path):
    gov = _Governance()
    svc = _service(tmp_path, "nogrant", governance=gov)
    res = _create(svc, "edit the README draft locally", key="ng-1")
    env = res["envelope"]
    assert env["state"] == "AWAITING_APPROVAL"
    r = res["authority_resolution"]
    assert r["outcome"] == resolver.FOUNDER_REQUIRED
    assert r["reason_code"] == "FOUNDER_REQUIRED_NO_GRANT"
    assert r["founder_prompt"]["risk"] == "R1"
    assert r["founder_prompt"]["why_human_required"]
    assert r["founder_gate"]["approve_route"].endswith("/gate/approve")
    assert "approve" in env["allowed_next_actions"] and "resolve" in env["allowed_next_actions"]
    assert _approvals(svc, env["mission_id"]) == []
    # What the founder's signature must bind to is published, once.
    published = svc.runtime.published_authorization_request(env["mission_id"])
    assert published["scope_key"] == r["scope_key"]
    assert published["source_sha"] == SHA and published["risk_tier"] == "R1"
    assert published["action"] == GATE_ACTION and published["scope"] == GATE_SCOPE


@pytest.mark.parametrize("objective,tier,code", [
    ("send the email to the buyer", "R4", "FOUNDER_REQUIRED_EXTERNAL"),
    ("delete all old records permanently", "R5", "FOUNDER_REQUIRED_DESTRUCTIVE"),
])
def test_consequential_missions_stay_gated_even_with_a_grant(tmp_path, objective, tier, code):
    gov = _Governance()
    svc = _service(tmp_path, "gated" + tier, governance=gov, founder_grant=_grant())
    res = _create(svc, objective, key="gt-1")
    assert res["envelope"]["state"] == "AWAITING_APPROVAL"
    r = res["authority_resolution"]
    assert r["outcome"] == resolver.FOUNDER_REQUIRED
    assert r["reason_code"] == code and r["risk_tier"] == tier
    assert r["founder_prompt"]["approve_once"] is True
    assert r["founder_prompt"]["approve_scope_session"] is False
    assert _approvals(svc, res["envelope"]["mission_id"]) == []
    # The resolver does not keep asking on its own: resolve again => same ask, no allow.
    again = svc.resolve_authority(res["envelope"]["mission_id"], idempotency_key="gt-2",
                                  expected_version=res["envelope"]["version"])
    assert again["authority_state"] == resolver.FOUNDER_REQUIRED
    assert svc.get_mission(res["envelope"]["mission_id"])["envelope"]["state"] == "AWAITING_APPROVAL"


def test_founder_gate_approval_is_presented_to_governance_not_spent_locally(tmp_path):
    """The loop for a consequential mission: park -> founder signs -> resolver presents -> allow."""
    gov = _Governance()
    svc = _service(tmp_path, "loop", governance=gov, signer=FounderApprovalSigner(SECRET))
    res = _create(svc, "edit the README draft locally", key="lp-1")
    mid = res["envelope"]["mission_id"]
    assert res["envelope"]["state"] == "AWAITING_APPROVAL"
    version = svc.get_mission(mid)["envelope"]["version"]
    human = svc.approve_mission_gate(mid, reason="ok", idempotency_key="lp-approve", expected_version=version)
    assert human["approval"]["decided_by"] == "jeramie"
    assert human["envelope"]["state"] == "AWAITING_APPROVAL", "a gate ALLOW records authority; it never advances"
    signed = [r for r in human["approval"]["evidence_refs"] if r.startswith("gov.founder.approval.v1=")]
    assert len(signed) == 1
    artifact = json.loads(signed[0].split("=", 1)[1])
    assert artifact["bound"]["source_sha"] == SHA and artifact["bound"]["risk_tier"] == "R1"

    version = svc.get_mission(mid)["envelope"]["version"]
    out = svc.resolve_authority(mid, idempotency_key="lp-resolve", expected_version=version)
    assert out["authority_state"] == resolver.RESOLVED_QUEUED
    assert out["resolution"]["reason_code"] == "FOUNDER_APPROVED_SCOPE_ONCE"
    assert out["resolution"]["founder_approval_presented"] is True
    assert out["envelope"]["state"] == "QUEUED"
    presented = gov.calls[-1]["founder_approval"]
    assert presented["signature"] == artifact["signature"]
    # The human's approval is the one spent; the resolver recorded no second one.
    approvals = _approvals(svc, mid)
    assert [a.decided_by for a in approvals] == ["jeramie"]
    assert approvals[0].decision == ApprovalDecision.CONSUMED


def test_unsigned_human_approval_is_not_authority(tmp_path):
    """No signer in the channel: the human record exists, Governance is still asked, and the mission stays parked."""
    gov = _Governance()
    svc = _service(tmp_path, "unsigned", governance=gov)   # default signer: no secret configured
    res = _create(svc, "edit the README draft locally", key="us-1")
    mid = res["envelope"]["mission_id"]
    version = svc.get_mission(mid)["envelope"]["version"]
    svc.approve_mission_gate(mid, reason="ok", idempotency_key="us-approve", expected_version=version)
    version = svc.get_mission(mid)["envelope"]["version"]
    out = svc.resolve_authority(mid, idempotency_key="us-resolve", expected_version=version)
    assert out["authority_state"] == resolver.FOUNDER_REQUIRED
    assert out["resolution"]["founder_approval_presented"] is False
    assert out["envelope"]["state"] == "AWAITING_APPROVAL"


# ------------------------------------------------------- fail-closed parking --
def test_governance_deny_blocks_on_policy(tmp_path):
    svc = _service(tmp_path, "deny", governance=_Governance(deny_classes={"read"}))
    res = _create(svc, "status check", key="d-1")
    assert res["envelope"]["state"] == "BLOCKED_POLICY"
    assert res["authority_resolution"]["outcome"] == resolver.BLOCKED_POLICY
    assert res["authority_resolution"]["reason_code"] == "POLICY_BLOCKED"
    assert _approvals(svc, res["envelope"]["mission_id"]) == []


def test_identity_denial_parks_instead_of_blocking(tmp_path):
    """A superseded/unqualified runtime is a configuration fact, not a verdict on the mission."""
    gov = _Governance(pinned_sha="520ae4381def8be52486935e1b9c53da31e52202")
    svc = _service(tmp_path, "identity", governance=gov, attested_source_sha=SHA)
    res = _create(svc, "status check", key="id-1")
    assert res["envelope"]["state"] == "AWAITING_APPROVAL"
    r = res["authority_resolution"]
    assert r["outcome"] == resolver.UNRESOLVED and r["reason_code"] == "SOURCE_SHA_MISMATCH"
    assert r["detail"]["infrastructure"] is True
    assert _approvals(svc, res["envelope"]["mission_id"]) == []


def test_governance_unreachable_parks_fail_closed(tmp_path):
    svc = _service(tmp_path, "down", governance=_Governance(unreachable=True))
    res = _create(svc, "status check", key="u-1")
    assert res["envelope"]["state"] == "AWAITING_APPROVAL"
    assert res["authority_resolution"]["outcome"] == resolver.UNRESOLVED
    assert res["authority_resolution"]["reason_code"] == "GOVERNANCE_UNREACHABLE"
    assert _approvals(svc, res["envelope"]["mission_id"]) == []


def test_adapter_failure_parks_fail_closed(tmp_path):
    svc = _service(tmp_path, "adapterfail", governance=_Governance(adapter_failure=True))
    res = _create(svc, "status check", key="af-1")
    assert res["envelope"]["state"] == "AWAITING_APPROVAL"
    assert res["authority_resolution"]["reason_code"] == "GOVERNANCE_ADAPTER_TRANSPORT_MALFORMED"


def test_no_port_and_no_source_sha_park_without_asking(tmp_path):
    gov = _Governance()
    svc = _service(tmp_path, "noport", governance=None)
    res = _create(svc, "status check", key="np-1")
    assert res["envelope"]["state"] == "AWAITING_APPROVAL"
    assert res["authority_resolution"]["reason_code"] == "RESOLVER_GOVERNANCE_PORT_ABSENT"
    svc2 = _service(tmp_path, "nosha", governance=gov, attested_source_sha="")
    res2 = _create(svc2, "status check", key="np-2")
    assert res2["envelope"]["state"] == "AWAITING_APPROVAL"
    assert res2["authority_resolution"]["reason_code"] == "SOURCE_SHA_UNATTESTED"
    assert gov.calls == []


def test_auto_resolve_can_be_disabled_and_resolved_explicitly(tmp_path):
    gov = _Governance()
    svc = _service(tmp_path, "explicit", governance=gov, auto_resolve=False)
    res = _create(svc, "status check", key="ex-1")
    mid = res["envelope"]["mission_id"]
    assert res["envelope"]["state"] == "AWAITING_APPROVAL" and res["authority_resolution"] is None
    assert gov.calls == []
    with pytest.raises(ApiError) as exc:
        svc.resolve_authority(mid, idempotency_key="ex-2", expected_version=None)
    assert exc.value.code == "EXPECTED_VERSION_REQUIRED"
    version = svc.get_mission(mid)["envelope"]["version"]
    out = svc.resolve_authority(mid, idempotency_key="ex-3", expected_version=version)
    assert out["envelope"]["state"] == "QUEUED" and len(gov.calls) == 1


# ----------------------------------------------------- replay / idempotency --
def test_resolve_is_idempotent_and_replay_safe(tmp_path):
    gov = _Governance()
    svc = _service(tmp_path, "replay", governance=gov, auto_resolve=False)
    res = _create(svc, "status check", key="rp-1")
    mid = res["envelope"]["mission_id"]
    version = svc.get_mission(mid)["envelope"]["version"]
    first = svc.resolve_authority(mid, idempotency_key="rp-resolve", expected_version=version)
    second = svc.resolve_authority(mid, idempotency_key="rp-resolve", expected_version=version)
    assert second["idempotent_replay"] is True
    assert second["resolution"]["authorization_id"] == first["resolution"]["authorization_id"]
    assert len(gov.calls) == 1, "an exact replay never re-asks Governance"
    # A fresh request against a mission that already left the gate is a state error.
    with pytest.raises(ApiError) as exc:
        svc.resolve_authority(mid, idempotency_key="rp-other", expected_version=first["envelope"]["version"])
    assert exc.value.code == "NOT_AWAITING_APPROVAL"
    assert len(gov.calls) == 1
    # Nonces are fresh per ask, never reused across missions or attempts.
    svc2 = _service(tmp_path, "replay2", governance=gov)
    _create(svc2, "status check", key="rp-2")
    nonces = [c["facts"]["nonce"] for c in gov.calls]
    assert len(nonces) == len(set(nonces)) == 2


def test_stale_version_is_refused_before_governance_is_asked(tmp_path):
    gov = _Governance()
    svc = _service(tmp_path, "version", governance=gov, auto_resolve=False)
    res = _create(svc, "status check", key="v-1")
    mid = res["envelope"]["mission_id"]
    with pytest.raises(ApiError) as exc:
        svc.resolve_authority(mid, idempotency_key="v-2", expected_version=999)
    assert exc.value.code == "VERSION_CONFLICT"
    assert gov.calls == []


def test_recorded_but_unspent_governance_approval_is_spent_on_retry_without_reasking(tmp_path):
    """Crash between record and transition: the next resolve spends the record and asks nothing."""
    gov = _Governance()
    svc = _service(tmp_path, "crash", governance=gov, auto_resolve=False)
    res = _create(svc, "status check", key="cr-1")
    mid = res["envelope"]["mission_id"]
    mission = svc.runtime.get_mission(mid)
    req = mission.authority_requirements[0]
    facts = resolver.resolver_facts(mid, req, action=GATE_ACTION, scope=GATE_SCOPE, source_sha=SHA,
                                    nonce="rn_" + "a" * 29, issued_at=svc.clock.now_iso())
    envelope = gov.authorize(facts, founder_approval=None, meta=None).payload
    approval = resolver.governance_decided_approval(envelope, mission_id=mid, requirement=req,
                                                    action=GATE_ACTION, scope=GATE_SCOPE, now_iso=svc.clock.now_iso())
    svc.runtime.record_approval(approval)
    version = svc.get_mission(mid)["envelope"]["version"]
    out = svc.resolve_authority(mid, idempotency_key="cr-2", expected_version=version)
    assert out["authority_state"] == resolver.RESOLVED_QUEUED
    assert out["resolution"]["reason_code"] == "RESOLVER_REPLAY"
    assert out["resolution"]["detail"]["spent_approval"] == approval.approval_id
    assert len(gov.calls) == 1, "only the seeding call; the retry asked nothing"


def test_governance_answer_is_on_the_mission_chain(tmp_path):
    svc = _service(tmp_path, "chain")
    res = _create(svc, "status check", key="ch-1")
    mid = res["envelope"]["mission_id"]
    events = svc.events(mid)["events"]
    recorded = [e for e in events if e.get("reason", "").startswith("approval_resolver:")]
    assert len(recorded) == 1
    payload = recorded[0]["payload"]["approval_resolver"]
    assert payload["outcome"] == resolver.RESOLVED_QUEUED
    assert payload["source_sha"] == SHA
    assert payload["founder_approval_presented"] is False
    assert payload["decision_receipt_hash"]


# -------------------------------------------------------------- HTTP route --
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_resolve_route_over_http(tmp_path):
    pytest.importorskip("fastapi", reason="HTTP transport requires fastapi")
    uvicorn = pytest.importorskip("uvicorn", reason="HTTP transport requires uvicorn")
    from recipe_engine.founder_runtime.api.app import create_app
    from recipe_engine.founder_runtime.api.auth import generate_token
    from recipe_engine.founder_runtime.api.client import FounderRuntimeClient

    token = generate_token()
    os.environ["FR_API_OPERATOR_TOKEN"] = token
    gov = _Governance()
    app = create_app(str(tmp_path / "http.sqlite"), probe_live=False, governance=gov,
                     attested_source_sha=SHA, auto_resolve=False)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    client = FounderRuntimeClient(token, port=port)
    for _ in range(100):
        try:
            client.health()
            break
        except Exception:
            time.sleep(0.05)
    try:
        created = client.create_mission("status check", idempotency_key="http-1")
        mid = created["envelope"]["mission_id"]
        assert created["envelope"]["state"] == "AWAITING_APPROVAL"
        version = int(client.get_mission(mid)["envelope"]["version"])
        out = client.resolve_gate(mid, expected_version=version)
        assert out["authority_state"] == resolver.RESOLVED_QUEUED
        assert out["envelope"]["state"] == "QUEUED"
        assert out["executed"] is False
        assert len(gov.calls) == 1
    finally:
        server.should_exit = True
        time.sleep(0.1)
