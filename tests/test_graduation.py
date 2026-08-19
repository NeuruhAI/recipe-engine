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

from datetime import datetime, timedelta, timezone  # noqa: E402

from recipe_engine.autonomy.models import AuthorityLevel, ConflictState  # noqa: E402
from recipe_engine.graduation import (  # noqa: E402
    POLICY_VERSION, GraduationPolicy, GraduationState, PropertyIdentity,
    SignalEvidence, classify, evidence_hash,
)

NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)


def aged(days, **over):
    """Evidence observed `days` ago, labelled 'current' on purpose — the gate
    must compute age itself and not believe the label."""
    at = (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    return ev(observed_at=at, **over)


def ev(source_type="estatesales.net", family="estate_sale",
       authority=AuthorityLevel.OBSERVED, freshness="current",
       observed_at="2026-08-18T00:00:00Z"):
    return SignalEvidence(source_type=source_type, family=family,
                          authority_level=authority, freshness=freshness,
                          observed_at=observed_at)


def real_parcel(**over):
    base = dict(parcel_apn="03 026 001 500", county_fips="26107",
                apn_is_real_parcel=True, address_verified=True,
                identity_source="berrien_parcels_spatial_intersect",
                parcel_provenance="Parcels_Future/FeatureServer/1 · 1 parcel intersects")
    base.update(over)
    return PropertyIdentity(**base)


def listing_key():
    """What the engine actually holds for an estate sale: a synthetic key."""
    return PropertyIdentity(parcel_apn="estate-5038419", county_fips="26021",
                            apn_is_real_parcel=False, address_verified=False)


def authoritative_distress(days=1):
    return aged(days, source_type="county_tax_roll", family="tax_distress",
                authority=AuthorityLevel.DOMAIN_AUTHORITY)


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
                 opportunity_score=90, score_provenance='nc_dps_wave_a', now=NOW)
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
                 opportunity_score=90, score_provenance='nc_dps_wave_a', now=NOW)
    assert v.state is GraduationState.QUALIFIED_CANDIDATE


def test_the_same_source_twice_does_not_corroborate():
    v = classify(real_parcel(), [ev(), ev()])
    assert v.corroborating_sources == 1
    assert v.state is GraduationState.RESEARCH_PROPERTY


def test_one_authoritative_source_corroborates_alone():
    v = classify(real_parcel(),
                 [ev(source_type="county_tax_roll", family="tax_distress",
                     authority=AuthorityLevel.DOMAIN_AUTHORITY)],
                 opportunity_score=90, score_provenance='nc_dps_wave_a', now=NOW)
    assert v.state is GraduationState.QUALIFIED_CANDIDATE


# ---- gates that outrank the evidence ----------------------------------------

def test_an_open_conflict_blocks_everything():
    v = classify(real_parcel(),
                 [ev(source_type="county_tax_roll", family="tax_distress",
                     authority=AuthorityLevel.DOMAIN_AUTHORITY)],
                 conflict=ConflictState.OPEN, opportunity_score=99, score_provenance='nc_dps_wave_a', now=NOW)
    assert v.state is GraduationState.CONFLICT
    assert v.would_promote_to_candidate is False


def test_the_freshness_label_no_longer_decides_expiry():
    """Policy v1 repair: expiry is computed from observed_at, so a caller's label
    cannot expire genuinely current evidence — nor rescue genuinely stale
    evidence (see test_a_current_label_cannot_talk_past_the_age_gate)."""
    one_day_old_but_mislabelled = aged(
        1, family="tax_distress", freshness="expired",
        source_type="county_tax_roll", authority=AuthorityLevel.DOMAIN_AUTHORITY)
    v = classify(real_parcel(), [one_day_old_but_mislabelled],
                 opportunity_score=99, score_provenance='nc_dps_wave_a', now=NOW)
    assert v.state is GraduationState.QUALIFIED_CANDIDATE
    assert v.evidence_ages_days == (1,)


def test_a_missing_score_is_never_a_pass():
    v = classify(real_parcel(),
                 [ev(source_type="county_tax_roll", family="tax_distress",
                     authority=AuthorityLevel.DOMAIN_AUTHORITY)],
                 opportunity_score=None, now=NOW)
    assert v.state is GraduationState.CORROBORATED
    assert "opportunity_score" in v.missing


def test_a_low_score_is_not_a_candidate():
    v = classify(real_parcel(),
                 [ev(source_type="county_tax_roll", family="tax_distress",
                     authority=AuthorityLevel.DOMAIN_AUTHORITY)],
                 opportunity_score=10, score_provenance='nc_dps_wave_a', now=NOW)
    assert v.state is GraduationState.CORROBORATED
    assert any("opportunity_score" in m for m in v.missing)


def test_no_evidence_at_all_is_an_observed_signal_not_a_pass():
    v = classify(real_parcel(), [])
    assert v.state is GraduationState.OBSERVED_SIGNAL
    assert v.would_promote_to_candidate is False


# ---- determinism -------------------------------------------------------------

def test_classification_is_deterministic_on_replay():
    identity, evidence = real_parcel(), [ev(), ev(source_type="county_tax_roll", family="tax_distress")]
    first = classify(identity, evidence, opportunity_score=90, score_provenance='nc_dps_wave_a', now=NOW)
    second = classify(identity, evidence, opportunity_score=90, score_provenance='nc_dps_wave_a', now=NOW)
    assert first == second


def test_policy_is_explicit_and_overridable():
    lenient = GraduationPolicy(require_distress_for_candidate=False, min_opportunity_score=0)
    v = classify(real_parcel(), [ev(), ev(source_type="obituary_feed", family="obituary")],
                 opportunity_score=0, score_provenance='nc_dps_wave_a', policy=lenient, now=NOW)
    assert v.state is GraduationState.QUALIFIED_CANDIDATE, (
        "the thresholds must be founder-tunable, not hard-coded doctrine")


# ---- POLICY V1 REPAIR 1: the age gate is computed, not believed --------------

def test_364_days_passes_the_age_gate():
    v = classify(real_parcel(), [authoritative_distress(364)],
                 opportunity_score=90, score_provenance="nc_dps_wave_a", now=NOW)
    assert v.state is GraduationState.QUALIFIED_CANDIDATE
    assert v.evidence_ages_days == (364,)


def test_365_days_is_the_last_day_that_passes():
    # Documented boundary: max_evidence_age_days is INCLUSIVE.
    v = classify(real_parcel(), [authoritative_distress(365)],
                 opportunity_score=90, score_provenance="nc_dps_wave_a", now=NOW)
    assert v.state is GraduationState.QUALIFIED_CANDIDATE
    assert v.evidence_ages_days == (365,)


def test_366_days_expires():
    v = classify(real_parcel(), [authoritative_distress(366)],
                 opportunity_score=90, score_provenance="nc_dps_wave_a", now=NOW)
    assert v.state is GraduationState.REJECTED_EXPIRED
    assert v.would_promote_to_candidate is False


def test_a_current_label_cannot_talk_past_the_age_gate():
    stale = aged(400, freshness="current", source_type="county_tax_roll",
                 family="tax_distress", authority=AuthorityLevel.DOMAIN_AUTHORITY)
    assert stale.freshness == "current"
    v = classify(real_parcel(), [stale], opportunity_score=90,
                 score_provenance="nc_dps_wave_a", now=NOW)
    assert v.state is GraduationState.REJECTED_EXPIRED


def test_mixed_fresh_and_expired_counts_only_the_current():
    v = classify(real_parcel(),
                 [authoritative_distress(3), aged(900, source_type="old_feed", family="ownership")],
                 opportunity_score=90, score_provenance="nc_dps_wave_a", now=NOW)
    assert v.state is GraduationState.QUALIFIED_CANDIDATE
    assert "ownership" not in v.distinct_families, "expired evidence must not corroborate"


def test_expired_evidence_cannot_supply_the_distress_family():
    v = classify(real_parcel(),
                 [aged(2, source_type="obituary_feed", family="obituary"),
                  aged(2, source_type="estatesales.net", family="estate_sale"),
                  aged(800, source_type="county_tax_roll", family="tax_distress")],
                 opportunity_score=90, score_provenance="nc_dps_wave_a", now=NOW)
    assert v.state is GraduationState.CORROBORATED
    assert "distress_evidence" in v.missing


def test_an_invalid_timestamp_is_missing_data_and_never_a_pass():
    bad = ev(observed_at="not-a-date", source_type="county_tax_roll",
             family="tax_distress", authority=AuthorityLevel.DOMAIN_AUTHORITY)
    assert bad.age_days(NOW) is None
    v = classify(real_parcel(), [bad], opportunity_score=90,
                 score_provenance="nc_dps_wave_a", now=NOW)
    assert v.state is GraduationState.REJECTED_EXPIRED
    assert "evidence.observed_at" in v.missing


def test_an_empty_timestamp_is_missing_data():
    assert ev(observed_at="").age_days(NOW) is None


# ---- POLICY V1 REPAIR 2: provenance completeness is a gate -------------------

def test_a_real_apn_without_a_resolver_cannot_graduate():
    unsourced = PropertyIdentity(parcel_apn="11-03-0015", county_fips="26021",
                                 apn_is_real_parcel=True, address_verified=True)
    v = classify(unsourced, [authoritative_distress(1)], opportunity_score=90,
                 score_provenance="nc_dps_wave_a", now=NOW)
    assert v.state is GraduationState.OBSERVED_SIGNAL
    assert v.provenance_complete is False
    assert "identity.identity_source" in v.missing
    assert "identity.parcel_provenance" in v.missing


def test_a_score_without_provenance_blocks_candidacy():
    v = classify(real_parcel(), [authoritative_distress(1)],
                 opportunity_score=90, score_provenance=None, now=NOW)
    assert v.state is GraduationState.OBSERVED_SIGNAL
    assert "score.provenance" in v.missing


def test_evidence_missing_its_source_type_blocks_candidacy():
    v = classify(real_parcel(),
                 [SignalEvidence(source_type="", family="tax_distress",
                                 authority_level=AuthorityLevel.DOMAIN_AUTHORITY,
                                 freshness="current",
                                 observed_at="2026-08-18T00:00:00Z")],
                 opportunity_score=90, score_provenance="nc_dps_wave_a", now=NOW)
    assert "evidence.source_type" in v.missing
    assert v.would_promote_to_candidate is False


def test_a_complete_verdict_reports_provenance_complete():
    v = classify(real_parcel(), [authoritative_distress(1)], opportunity_score=90,
                 score_provenance="nc_dps_wave_a", now=NOW)
    assert v.provenance_complete is True


# ---- POLICY V1 REPAIR 3: candidate floor is 55 ------------------------------

def test_score_54_is_not_candidate_eligible():
    v = classify(real_parcel(), [authoritative_distress(1)], opportunity_score=54,
                 score_provenance="nc_dps_wave_a", now=NOW)
    assert v.state is GraduationState.CORROBORATED
    assert "opportunity_score>=55" in v.missing


def test_score_55_is_candidate_eligible():
    v = classify(real_parcel(), [authoritative_distress(1)], opportunity_score=55,
                 score_provenance="nc_dps_wave_a", now=NOW)
    assert v.state is GraduationState.QUALIFIED_CANDIDATE


def test_the_45_to_54_band_stays_research_or_corroborated():
    for score in (45, 50, 54):
        v = classify(real_parcel(), [authoritative_distress(1)], opportunity_score=score,
                     score_provenance="nc_dps_wave_a", now=NOW)
        assert v.state is GraduationState.CORROBORATED, score
        assert v.would_promote_to_research is True


# ---- receipt / version -------------------------------------------------------

def test_every_verdict_carries_the_policy_version_and_an_evidence_hash():
    v = classify(real_parcel(), [authoritative_distress(1)], opportunity_score=90,
                 score_provenance="nc_dps_wave_a", now=NOW)
    assert v.policy_version == POLICY_VERSION == "graduation.v1"
    assert len(v.evidence_hash) == 64


def test_the_evidence_hash_changes_when_an_input_changes():
    a = evidence_hash(real_parcel(), [authoritative_distress(1)], 90, GraduationPolicy())
    b = evidence_hash(real_parcel(), [authoritative_distress(1)], 91, GraduationPolicy())
    assert a != b


def test_the_evidence_hash_is_order_independent():
    e1, e2 = authoritative_distress(1), aged(2, source_type="obituary_feed", family="obituary")
    assert (evidence_hash(real_parcel(), [e1, e2], 90, GraduationPolicy())
            == evidence_hash(real_parcel(), [e2, e1], 90, GraduationPolicy()))
