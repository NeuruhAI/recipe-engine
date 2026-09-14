"""Defect proof (FINAL ACTIVATION 2026-09-14, mission msn_2d6e71c5…):

A gate ALLOW recorded while Governance was parking the mission as *unresolved*
(expired mutation ratification → deny MUTATION_NOT_RATIFIED, which publishes no
authorization request) carries no signed Governance artifact. Once the
ratification is renewed, Governance answers founder_required and the mission
publishes its scope_key / source_sha / tier — but the single-decision latch
replays the artifact-less record forever, so the founder's decision exists and
can never be presented. The mission is stuck at AWAITING_APPROVAL.

Expected behaviour (the late-binding repair): a repeated gate ALLOW after the
request is published mints the missing artifact for the SAME decision, exactly
once; a conflicting decision is still refused; a third ALLOW replays the bound
record; the resolver presents the bound record and Governance allows once.
"""

from __future__ import annotations

import json

import pytest

from recipe_engine.founder_runtime.api.founder_signing import FounderApprovalSigner
from recipe_engine.founder_runtime.api.service import ApiError
from recipe_engine.founder_runtime.contracts.approval import ApprovalDecision
from recipe_engine.founder_runtime.runtime import approval_resolver as resolver

from tests.founder_runtime.api.test_approval_resolver import (
    SECRET, SHA, _Governance, _approvals, _create, _service,
)

ARTIFACT_PREFIX = "gov.founder.approval.v1="


class _UnratifiedThenRenewed(_Governance):
    """Live seq 228 → seq 229: deny MUTATION_NOT_RATIFIED until the founder renews."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.ratified = False

    def authorize(self, facts, *, founder_approval, meta, grant=None):
        if not self.ratified:
            self.calls.append({"facts": dict(facts), "founder_approval": founder_approval,
                               "grant": grant, "meta": meta})
            return self._env(facts, "deny", "MUTATION_NOT_RATIFIED", tier="R1")
        return super().authorize(facts, founder_approval=founder_approval, meta=meta, grant=grant)


def _signed_refs(approval_dict):
    return [r for r in approval_dict["evidence_refs"] if r.startswith(ARTIFACT_PREFIX)]


def _park_then_approve_before_publish(tmp_path):
    gov = _UnratifiedThenRenewed()
    svc = _service(tmp_path, "latebind", governance=gov, signer=FounderApprovalSigner(SECRET))
    res = _create(svc, "edit the README draft locally", key="lb-1")
    mid = res["envelope"]["mission_id"]
    assert res["envelope"]["state"] == "AWAITING_APPROVAL"
    assert res["authority_resolution"]["outcome"] == resolver.UNRESOLVED
    assert res["authority_resolution"]["reason_code"] == "MUTATION_NOT_RATIFIED"
    assert svc.runtime.published_authorization_request(mid) is None
    version = svc.get_mission(mid)["envelope"]["version"]
    first = svc.approve_mission_gate(mid, reason="ok", idempotency_key="lb-approve-1",
                                     expected_version=version)
    assert first["approval"]["decision"] == "allow"
    assert _signed_refs(first["approval"]) == [], "nothing to bind to before publication"
    # The founder renews the ratification (G1); the next resolve publishes the request.
    gov.ratified = True
    version = svc.get_mission(mid)["envelope"]["version"]
    out = svc.resolve_authority(mid, idempotency_key="lb-resolve-1", expected_version=version)
    assert out["authority_state"] == resolver.FOUNDER_REQUIRED
    assert out["resolution"]["founder_approval_presented"] is False
    published = svc.runtime.published_authorization_request(mid)
    assert published and published["scope_key"] == out["resolution"]["scope_key"]
    return gov, svc, mid, first


def test_repeated_gate_allow_late_binds_the_governance_artifact_once(tmp_path):
    gov, svc, mid, first = _park_then_approve_before_publish(tmp_path)
    version = svc.get_mission(mid)["envelope"]["version"]
    second = svc.approve_mission_gate(mid, reason="ok again", idempotency_key="lb-approve-2",
                                      expected_version=version)
    assert second["approval"]["decision"] == "allow"
    signed = _signed_refs(second["approval"])
    assert len(signed) == 1, "the repeated ALLOW must carry the late-bound artifact"
    artifact = json.loads(signed[0][len(ARTIFACT_PREFIX):])
    assert artifact["bound"]["source_sha"] == SHA and artifact["bound"]["risk_tier"] == "R1"
    assert artifact["bound"]["scope_key"] == svc.runtime.published_authorization_request(mid)["scope_key"]
    assert second["approval"]["approval_id"] != first["approval"]["approval_id"]
    assert any(c.startswith("governance_artifact_late_bound_for:" + first["approval"]["approval_id"])
               for c in second["approval"]["conditions"])
    # Exactly one decision still: a third ALLOW replays the bound record, mints nothing.
    version = svc.get_mission(mid)["envelope"]["version"]
    third = svc.approve_mission_gate(mid, reason="ok thrice", idempotency_key="lb-approve-3",
                                     expected_version=version)
    assert third.get("idempotent_replay") is True
    assert third["approval"]["approval_id"] == second["approval"]["approval_id"]
    allows = [a for a in _approvals(svc, mid) if a.decision == ApprovalDecision.ALLOW]
    assert len(allows) == 2
    # A conflicting decision is refused exactly as before.
    with pytest.raises(ApiError) as ei:
        svc.reject_mission_gate(mid, reason="no", idempotency_key="lb-reject",
                                expected_version=svc.get_mission(mid)["envelope"]["version"])
    assert ei.value.status == 409 and ei.value.code == "DECISION_CONFLICT"


def test_resolver_presents_the_bound_record_and_governance_allows_once(tmp_path):
    gov, svc, mid, first = _park_then_approve_before_publish(tmp_path)
    version = svc.get_mission(mid)["envelope"]["version"]
    second = svc.approve_mission_gate(mid, reason="ok again", idempotency_key="lb-approve-2",
                                      expected_version=version)
    artifact = json.loads(_signed_refs(second["approval"])[0][len(ARTIFACT_PREFIX):])
    version = svc.get_mission(mid)["envelope"]["version"]
    out = svc.resolve_authority(mid, idempotency_key="lb-resolve-2", expected_version=version)
    assert out["authority_state"] == resolver.RESOLVED_QUEUED
    assert out["resolution"]["reason_code"] == "FOUNDER_APPROVED_SCOPE_ONCE"
    assert out["resolution"]["founder_approval_presented"] is True
    assert out["envelope"]["state"] == "QUEUED"
    assert gov.calls[-1]["founder_approval"]["signature"] == artifact["signature"]
    by_id = {a.approval_id: a for a in _approvals(svc, mid)}
    assert by_id[second["approval"]["approval_id"]].decision == ApprovalDecision.CONSUMED
    assert by_id[first["approval"]["approval_id"]].decision == ApprovalDecision.ALLOW, \
        "the earlier artifact-less record is left as it was; nothing is rewritten"


def test_without_a_signer_the_repeated_allow_is_a_plain_replay(tmp_path):
    gov = _UnratifiedThenRenewed()
    svc = _service(tmp_path, "nosigner", governance=gov)   # no secret: signer not configured
    res = _create(svc, "edit the README draft locally", key="ns-1")
    mid = res["envelope"]["mission_id"]
    version = svc.get_mission(mid)["envelope"]["version"]
    first = svc.approve_mission_gate(mid, reason="ok", idempotency_key="ns-approve-1",
                                     expected_version=version)
    gov.ratified = True
    version = svc.get_mission(mid)["envelope"]["version"]
    svc.resolve_authority(mid, idempotency_key="ns-resolve-1", expected_version=version)
    version = svc.get_mission(mid)["envelope"]["version"]
    second = svc.approve_mission_gate(mid, reason="ok", idempotency_key="ns-approve-2",
                                      expected_version=version)
    assert second.get("idempotent_replay") is True
    assert second["approval"]["approval_id"] == first["approval"]["approval_id"]
    assert len(_approvals(svc, mid)) == 1


def test_pin_move_republishes_and_the_late_bound_artifact_names_the_new_sha(tmp_path):
    """Live 2026-09-14: the supervised FR pin moved 7b1431b4 -> b109810 under the parked
    mission; the request published for the old SHA must not be what the founder signs."""
    NEW = "b109810a4b7936d21f337017bc860e8d58092482"
    gov, svc, mid, first = _park_then_approve_before_publish(tmp_path)
    assert svc.runtime.published_authorization_request(mid)["source_sha"] == SHA
    svc.attested_source_sha = NEW            # the restart at the new pin
    gov.pinned_sha = NEW                     # G3: Governance qualifies the new build
    version = svc.get_mission(mid)["envelope"]["version"]
    out = svc.resolve_authority(mid, idempotency_key="pm-resolve-1", expected_version=version)
    assert out["authority_state"] == resolver.FOUNDER_REQUIRED
    assert svc.runtime.published_authorization_request(mid)["source_sha"] == NEW
    version = svc.get_mission(mid)["envelope"]["version"]
    second = svc.approve_mission_gate(mid, reason="ok again", idempotency_key="pm-approve-2",
                                      expected_version=version)
    artifact = json.loads(_signed_refs(second["approval"])[0][len(ARTIFACT_PREFIX):])
    assert artifact["bound"]["source_sha"] == NEW
    version = svc.get_mission(mid)["envelope"]["version"]
    out = svc.resolve_authority(mid, idempotency_key="pm-resolve-2", expected_version=version)
    assert out["authority_state"] == resolver.RESOLVED_QUEUED
    assert out["resolution"]["reason_code"] == "FOUNDER_APPROVED_SCOPE_ONCE"
