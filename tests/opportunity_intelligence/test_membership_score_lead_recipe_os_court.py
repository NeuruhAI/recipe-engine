"""Cross-repo court: Lead Recipe OS consumes the membership score vector as-is.

The next layer (NBE + qualification) reads a ``score-vector.v1`` and plans
evidence over every component that is not EVALUATED.  This court validates the
projection with the consumer's own kernel types (``lead_recipe_os.kernel.types
.ScoreVector`` @ 40d48d4), requires the consumer to re-hash it to the same
``content_sha256``, and runs ``plan_next_best_evidence`` over it.

Skipped unless ``LEAD_RECIPE_OS_KERNEL_PATH`` names a checkout of that commit
(its kernel needs pydantic and pydantic-settings, which this repository does
not install).
"""

from __future__ import annotations

import os
import sys

import pytest

from recipe_engine.opportunity_intelligence.membership_scoring import (
    load_strategy,
    rank,
    score_membership,
    to_score_vector,
)

KERNEL_PATH = os.environ.get("LEAD_RECIPE_OS_KERNEL_PATH", "")
pytestmark = pytest.mark.skipif(
    not KERNEL_PATH,
    reason="set LEAD_RECIPE_OS_KERNEL_PATH to a lead-recipe-os checkout @40d48d4",
)


@pytest.fixture(scope="module")
def kernel():
    if KERNEL_PATH not in sys.path:
        sys.path.insert(0, KERNEL_PATH)
    types = pytest.importorskip("lead_recipe_os.kernel.types")
    nbe = pytest.importorskip("lead_recipe_os.kernel.nbe")
    vocab = pytest.importorskip("lead_recipe_os.vocabularies")
    return types, nbe, vocab


def _receipt(**overrides):
    row = {
        "county_fips": "37179",
        "parcel_apn": "02.239.077",
        "source_types": "taxdelinquent",
        "tax_foreclosure_indicator": "",
        "heir_indicator": "True",
        "fragmented_ownership_indicator": "True",
        "out_of_state": "",
        "years_owned": "",
        "assessed_value": "",
        "tax_balance": "",
        "equity_status": "UNKNOWN_NEEDS_DEBT_SEARCH",
        "independent_source_family_count": "2",
        "source_families": "county_tax_collector_publication|assessor_parcel_roll",
    }
    row.update(overrides)
    scored = score_membership(row, "fractional_family_ownership", strategy=load_strategy(), is_primary=False, match_rank=2)
    return rank([scored]).receipts[0]


def test_consumer_kernel_accepts_and_rehashes_the_score_vector(kernel):
    types, _nbe, _vocab = kernel
    vector = to_score_vector(_receipt(), tenant_id="tenant-fixture", universe_id="universe-fixture")
    model = types.ScoreVector.model_validate(vector)
    assert model.content_sha256 == vector["content_sha256"]
    states = {c.name: c.evaluation_state.value for c in model.components}
    assert states["fractional_family_ownership.economic_value@nc_curative_rank.v3"] == "UNKNOWN"
    assert states["fractional_family_ownership.contactability@nc_curative_rank.v3"] == "UNWIRED"


def test_consumer_refuses_an_unknown_dimension_dressed_as_zero(kernel):
    types, _nbe, _vocab = kernel
    vector = to_score_vector(_receipt(), tenant_id="tenant-fixture", universe_id="universe-fixture")
    economic = next(c for c in vector["components"] if c["dimension"] == "economic_value")
    economic["value"] = 0.0
    with pytest.raises(Exception, match="LOW_SCORE"):
        types.ScoreVector.model_validate(vector)


def test_next_best_evidence_plans_over_what_scoring_left_open(kernel):
    types, nbe, vocab = kernel
    vector = types.ScoreVector.model_validate(
        to_score_vector(_receipt(), tenant_id="tenant-fixture", universe_id="universe-fixture")
    )
    contact = types.ContactReadiness(
        tenant_id="tenant-fixture",
        universe_id="universe-fixture",
        opportunity_id=vector.opportunity_id,
        phone_present=vocab.ContactAxis.NOT_EVALUATED,
        contact_resolved=vocab.ContactAxis.NOT_EVALUATED,
        dnc_clear=vocab.ContactAxis.NOT_EVALUATED,
        consent=vocab.ContactAxis.NOT_EVALUATED,
        action_authority=vocab.ActionAuthority.NOT_EVALUATED,
    )
    plan = nbe.plan_next_best_evidence(
        tenant_id="tenant-fixture",
        universe_id="universe-fixture",
        opportunity_id=vector.opportunity_id,
        evidence_refs=["membership-score:" + vector.content_sha256],
        scores=vector,
        contact=contact,
        demand=None,
    )
    gap_text = " ".join(g.expected_information for g in plan.gaps)
    for open_dimension in ("economic_value", "contactability", "liquidity", "evidence_confidence",
                           "strategic_fit", "downside_risk"):
        assert f"evaluate fractional_family_ownership.{open_dimension}@nc_curative_rank.v3 " in gap_text
    for evaluated in ("signal_strength", "urgency", "data_completeness", "source_independence_diversity"):
        assert f".{evaluated}@" not in gap_text
