from __future__ import annotations

from recipe_engine.opportunity_intelligence import (
    DEFAULT_RANKING_VERSION,
    DEFAULT_RECIPE_VERSION,
    MATCH_RECEIPT_VERSION,
    NBE_RECEIPT_VERSION,
    RANK_RECEIPT_VERSION,
    build_next_best_evidence,
    build_why_this_matched,
    build_why_this_ranked,
)


def _row(**overrides):
    row = {
        "rank": "7",
        "county_fips": "37179",
        "parcel_apn": "02.239.077",
        "recipe_id": "stalled_tax_foreclosure_heirship",
        "source_types": "taxdelinquent|tax_foreclosure_case",
        "trigger_classes": (
            "tax_distress|court_action|heir_or_estate|"
            "ownership_fragmentation|absentee_or_out_of_state|long_term_ownership"
        ),
        "tax_foreclosure_indicator": "True",
        "heir_indicator": "True",
        "fragmented_ownership_indicator": "True",
        "out_of_state": "True",
        "years_owned": "26",
        "assessed_value": "250000",
        "tax_balance": "8000",
        "tax_years": "2022",
        "trigger_count": "6",
        "score_reasons": (
            "published_tax_distress:+10|active_tax_foreclosure:+25|"
            "heir_or_estate_signal:+20|out_of_state_owner:+5"
        ),
        "equity_status": "UNKNOWN_NEEDS_DEBT_SEARCH",
        "potential_equity_proxy": "HIGH",
    }
    row.update(overrides)
    return row


def test_why_this_matched_is_deterministic_and_complete():
    row = _row()
    first = build_why_this_matched(row)
    second = build_why_this_matched(row)
    assert first == second
    assert first["schema_version"] == MATCH_RECEIPT_VERSION
    assert first["recipe_id"] == "stalled_tax_foreclosure_heirship"
    assert first["recipe_version"] == DEFAULT_RECIPE_VERSION
    assert first["coverage_state"] == "PARTIAL"
    assert "pre_foreclosure" in first["signals_triggered"]
    assert "verified_equity" in first["unknown_inputs"]
    assert first["negative_evidence"] == []
    # Missing source coverage is preserved as unknown/walled state, not negative evidence.
    assert first["source_ids"] == ["foreclosure_source", "tax_source"]


def test_why_this_ranked_is_deterministic_and_parses_contributions():
    row = _row()
    first = build_why_this_ranked(row)
    second = build_why_this_ranked(row)
    assert first == second
    assert first["schema_version"] == RANK_RECEIPT_VERSION
    assert first["ranking_version"] == DEFAULT_RANKING_VERSION
    assert first["recipe_rank"] == 7
    assert first["component_contributions"] == [
        {"factor_id": "published_tax_distress", "delta": 10},
        {"factor_id": "active_tax_foreclosure", "delta": 25},
        {"factor_id": "heir_or_estate_signal", "delta": 20},
        {"factor_id": "out_of_state_owner", "delta": 5},
    ]
    assert first["penalties"] == []
    assert "verified_equity" in first["unknown_material_factors"]


def test_next_best_evidence_for_foreclosure_recipe_is_docket_first():
    evidence = build_next_best_evidence(_row())
    assert evidence["schema_version"] == NBE_RECEIPT_VERSION
    assert evidence["source_id_or_family"] == "county_foreclosure_docket_publication"
    assert evidence["field_needed"] == "foreclosure_stage|sale_date|redemption_deadline"
    assert evidence["estimated_cost"] == "$0"
    assert evidence["current_coverage_state"] == "PARTIAL"


def test_next_best_evidence_for_unknown_equity_respects_walled_state():
    evidence = build_next_best_evidence(
        _row(
            recipe_id="published_tax_distress",
            source_types="taxdelinquent",
            trigger_classes="tax_distress",
        )
    )
    assert evidence["source_id_or_family"] == "gap.deed.recorded_instruments"
    assert evidence["current_coverage_state"] == "WALLED"
    assert evidence["fallback"] == "title_curative_queue"
