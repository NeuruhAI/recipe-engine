"""Cross-repo court: the consumer accepts what this producer emits.

Runs ``lead_recipe_os.kernel.membership.accept_fixture_eligibility`` (the
Lead Recipe OS intake court sealed at aaee1daef45d021c389dd62eca2928219f1ebe1e)
against receipts produced here, projected onto the intake contract.

Lead Recipe OS is consumer contract evidence, not evaluator authority.  The
court is skipped unless ``LEAD_RECIPE_OS_KERNEL_PATH`` names a checkout of
that commit (its kernel needs pydantic and pydantic-settings, which this
repository does not install).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

from recipe_engine.opportunity_intelligence.eligibility import (
    evaluate_recipes,
    to_intake_receipt,
)

KERNEL_PATH = os.environ.get("LEAD_RECIPE_OS_KERNEL_PATH", "")
pytestmark = pytest.mark.skipif(
    not KERNEL_PATH,
    reason="set LEAD_RECIPE_OS_KERNEL_PATH to a lead-recipe-os checkout @aaee1da",
)
FIXTURE = Path(__file__).parent / "fixtures" / "p1_independent_eligibility.json"


@pytest.fixture(scope="module")
def kernel():
    if KERNEL_PATH not in sys.path:
        sys.path.insert(0, KERNEL_PATH)
    membership = pytest.importorskip("lead_recipe_os.kernel.membership")
    types = pytest.importorskip("lead_recipe_os.kernel.types")
    hashing = pytest.importorskip("lead_recipe_os.hashing")
    return membership, types, hashing


@pytest.fixture(scope="module")
def body():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def receipts(body):
    return {r.recipe_id: r for r in evaluate_recipes(body["context"], body["policies"].values())}


def _candidate(types, context):
    return types.OpportunityCandidate.model_validate({
        "tenant_id": context["tenant_id"],
        "universe_id": context["universe_id"],
        "opportunity_id": context["opportunity_id"],
        "stage": "QUALIFIED_CANDIDATE",
        "entity_id": "owner-fixture",
        "asset_id": context["asset_id"],
        "signal_ids": [s["signal_id"] for s in context["signals"]],
        "evidence_refs": [e["evidence_id"] for e in context["evidence"]],
        "lineage": {"source_id": "synthetic-fixture", "mode": "FIXTURE"},
    })


def _recipe(types, policy):
    return types.RecipeVersion.model_validate({
        "tenant_id": policy["tenant_id"],
        "recipe_id": policy["recipe_id"],
        "recipe_version_id": policy["recipe_version_id"],
        "version": policy["version"],
        "policy_digest": policy["policy_digest"],
        "composition": policy["composition"],
    })


def _evidence(types, context):
    return [
        types.EvidenceReference.model_validate({
            "tenant_id": context["tenant_id"],
            "universe_id": context["universe_id"],
            "evidence_id": item["evidence_id"],
            "media_type": item.get("media_type") or "application/json",
            "content_sha256": item["content_sha256"],
            "source_id": item["source_id"],
            "observed_at": item["observed_at"],
            "ledger_ref": item.get("ledger_ref"),
            "ledger_state": item["ledger_state"],
            "producer": None,
        })
        for item in context["evidence"]
    ]


@pytest.mark.parametrize("recipe_id,decision", [
    ("published_tax_distress", "MATCH"),
    ("stalled_tax_foreclosure_heirship", "NO_MATCH"),
    ("open_probate_property", "UNKNOWN"),
    ("long_term_absentee_potential_equity", "MATCH"),
])
def test_consumer_accepts_projection_and_preserves_decision(kernel, body, receipts, recipe_id, decision):
    membership, types, hashing = kernel
    receipt = receipts[recipe_id]
    projected = to_intake_receipt(receipt)
    label = next(k for k, v in body["recipe_labels"].items() if v == recipe_id)
    observation = membership.accept_fixture_eligibility(
        candidate=_candidate(types, body["context"]),
        recipe=_recipe(types, body["policies"][label]),
        evidence=_evidence(types, body["context"]),
        receipt=membership.IndependentEligibilityReceipt.model_validate(projected),
        as_of=datetime.fromisoformat("2026-09-08T02:00:00+00:00"),
    )
    assert observation.decision == decision == receipt.decision
    assert observation.membership_key == receipt.membership_key
    assert observation.receipt_sha256 == projected["content_sha256"]
    assert hashing.content_hash(projected) == projected["content_sha256"]
    assert observation.opportunity_id == receipt.opportunity_id == body["context"]["opportunity_id"]
    assert observation.asset_id == receipt.asset_id == body["context"]["asset_id"]
    assert observation.recipe_version_id == receipt.recipe_version_id
    assert observation.production_write is False
    assert observation.contact_authorized is False


def test_consumer_sees_two_match_memberships_for_one_candidate(kernel, body, receipts):
    membership, types, hashing = kernel
    keys = {}
    for label, recipe_id in body["recipe_labels"].items():
        observation = membership.accept_fixture_eligibility(
            candidate=_candidate(types, body["context"]),
            recipe=_recipe(types, body["policies"][label]),
            evidence=_evidence(types, body["context"]),
            receipt=membership.IndependentEligibilityReceipt.model_validate(to_intake_receipt(receipts[recipe_id])),
            as_of=datetime.fromisoformat("2026-09-08T02:00:00+00:00"),
        )
        keys[recipe_id] = (observation.membership_key, observation.decision)
    assert len({k for k, _ in keys.values()}) == 4
    assert sorted(d for _, d in keys.values()) == ["MATCH", "MATCH", "NO_MATCH", "UNKNOWN"]


def test_consumer_rejects_tampered_projection(kernel, body, receipts):
    membership, types, hashing = kernel
    projected = to_intake_receipt(receipts["open_probate_property"])
    projected["decision"] = "NO_MATCH"
    with pytest.raises(ValueError, match="digest mismatch"):
        membership.IndependentEligibilityReceipt.model_validate(projected)
