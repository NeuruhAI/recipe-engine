"""
The signal -> candidate graduation court.

These pin the rule the estate depends on: an estate sale is a life-event signal,
never by itself proof of distress or of probate, and a listing id is not a
parcel. Nothing here promotes anything — the module is pure and the founder owns
the thresholds.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recipe_engine.autonomy.models import AuthorityLevel, ConflictState  # noqa: E402
from recipe_engine.graduation import (  # noqa: E402
    GraduationPolicy, GraduationState, PropertyIdentity, SignalEvidence, classify,
)


def ev(source_type="estatesales.net", family="estate_sale",
       authority=AuthorityLevel.OBSERVED, freshness="current"):
    return SignalEvidence(source_type=source_type, family=family,
                          authority_level=authority, freshness=freshness,
                          observed_at="2026-08-18T00:00:00Z")


def real_parcel(**over):
    base = dict(parcel_apn="03 026 001 500", county_fips="26107",
                apn_is_real_parcel=True, address_verified=True)
    base.update(over)
    return PropertyIdentity(**base)


def listing_key():
    """What the engine actually holds for an estate sale: a synthetic key."""
    return PropertyIdentity(parcel_apn="estate-5038419", county_fips="26021",
                            apn_is_real_parcel=False, address_verified=False)


# ---- the core refusal -------------------------------------------------------

def test_an_estate_sale_alone_is_only_an_observed_signal():
    v = classify(listing_key(), [ev()])
    assert v.state is GraduationState.OBSERVED_SIGNAL
    assert v.would_promote_to_research is False
    assert v.would_promote_to_candidate is False
    assert "real_parcel_apn" in v.missing


def test_a_listing_id_is_never_treated_as_a_parcel():
    # Even with a second source, a synthetic key cannot become a property.
    v = classify(listing_key(), [ev(), ev(source_type="obituary_feed", family="obituary")])
    assert v.state is GraduationState.OBSERVED_SIGNAL
    assert v.would_promote_to_research is False


def test_estate_sale_is_not_distress_even_when_corroborated():
    v = classify(real_parcel(), [ev(), ev(source_type="obituary_feed", family="obituary")],
                 opportunity_score=90)
    assert v.state is GraduationState.CORROBORATED
    assert "distress_evidence" in v.missing
    assert v.would_promote_to_candidate is False


# ---- the corroboration rule, inherited from validation.py -------------------

def test_one_ordinary_source_on_a_real_parcel_is_research_only():
    v = classify(real_parcel(), [ev()])
    assert v.state is GraduationState.RESEARCH_PROPERTY
    assert v.would_promote_to_research is True
    assert v.would_promote_to_candidate is False
    assert "second_distinct_source" in v.missing


def test_two_distinct_sources_corroborate():
    v = classify(real_parcel(), [ev(), ev(source_type="county_tax_roll", family="tax_distress")],
                 opportunity_score=90)
    assert v.state is GraduationState.QUALIFIED_CANDIDATE


def test_the_same_source_twice_does_not_corroborate():
    v = classify(real_parcel(), [ev(), ev()])
    assert v.corroborating_sources == 1
    assert v.state is GraduationState.RESEARCH_PROPERTY


def test_one_authoritative_source_corroborates_alone():
    v = classify(real_parcel(),
                 [ev(source_type="county_tax_roll", family="tax_distress",
                     authority=AuthorityLevel.DOMAIN_AUTHORITY)],
                 opportunity_score=90)
    assert v.state is GraduationState.QUALIFIED_CANDIDATE


# ---- gates that outrank the evidence ----------------------------------------

def test_an_open_conflict_blocks_everything():
    v = classify(real_parcel(),
                 [ev(source_type="county_tax_roll", family="tax_distress",
                     authority=AuthorityLevel.DOMAIN_AUTHORITY)],
                 conflict=ConflictState.OPEN, opportunity_score=99)
    assert v.state is GraduationState.CONFLICT
    assert v.would_promote_to_candidate is False


def test_all_expired_evidence_is_rejected():
    v = classify(real_parcel(),
                 [ev(family="tax_distress", freshness="expired",
                     authority=AuthorityLevel.DOMAIN_AUTHORITY)],
                 opportunity_score=99)
    assert v.state is GraduationState.REJECTED_EXPIRED


def test_a_missing_score_is_never_a_pass():
    v = classify(real_parcel(),
                 [ev(source_type="county_tax_roll", family="tax_distress",
                     authority=AuthorityLevel.DOMAIN_AUTHORITY)],
                 opportunity_score=None)
    assert v.state is GraduationState.CORROBORATED
    assert "opportunity_score" in v.missing


def test_a_low_score_is_not_a_candidate():
    v = classify(real_parcel(),
                 [ev(source_type="county_tax_roll", family="tax_distress",
                     authority=AuthorityLevel.DOMAIN_AUTHORITY)],
                 opportunity_score=10)
    assert v.state is GraduationState.CORROBORATED
    assert any("opportunity_score" in m for m in v.missing)


def test_no_evidence_at_all_is_an_observed_signal_not_a_pass():
    v = classify(real_parcel(), [])
    assert v.state is GraduationState.OBSERVED_SIGNAL
    assert v.would_promote_to_candidate is False


# ---- determinism -------------------------------------------------------------

def test_classification_is_deterministic_on_replay():
    identity, evidence = real_parcel(), [ev(), ev(source_type="county_tax_roll", family="tax_distress")]
    first = classify(identity, evidence, opportunity_score=90)
    second = classify(identity, evidence, opportunity_score=90)
    assert first == second


def test_policy_is_explicit_and_overridable():
    lenient = GraduationPolicy(require_distress_for_candidate=False, min_opportunity_score=0)
    v = classify(real_parcel(), [ev(), ev(source_type="obituary_feed", family="obituary")],
                 opportunity_score=0, policy=lenient)
    assert v.state is GraduationState.QUALIFIED_CANDIDATE, (
        "the thresholds must be founder-tunable, not hard-coded doctrine")
