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
        "global_rank": "7",
        "recipe_rank": "2",
        "recipe_membership_count": "5",
        "ranking_universe_id": "universe-fixture",
        "score": "63",
        "curative_priority_score": "63",
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
        "assessed_value_less_tax": "242000",
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
    assert first["schema_version"] == "oppack.why-this-ranked.v2"
    assert first["ranking_version"] == DEFAULT_RANKING_VERSION
    assert first["recipe_rank"] == 2
    assert first["global_rank"] == 7
    assert first["recipe_rank"] != first["global_rank"]
    assert first["recipe_membership_count"] == 5
    assert first["ranking_universe_id"] == "universe-fixture"
    assert first["score"] == 63
    assert first["score_semantics"] == "DETERMINISTIC_ADDITIVE_CURATIVE_PRIORITY"
    assert int(row["rank"]) == first["global_rank"]
    assert first["component_contributions"] == [
        {"factor_id": "published_tax_distress", "delta": 10},
        {"factor_id": "active_tax_foreclosure", "delta": 25},
        {"factor_id": "heir_or_estate_signal", "delta": 20},
        {"factor_id": "out_of_state_owner", "delta": 5},
    ]
    assert first["penalties"] == []
    assert "verified_equity" in first["unknown_material_factors"]


def test_why_this_ranked_tie_break_distinguishes_known_zero_from_unknown():
    known_zero = build_why_this_ranked(
        _row(
            equity_status="VERIFIED",
            trigger_count="0",
            assessed_value="0",
            tax_balance="0",
            assessed_value_less_tax="0",
        )
    )
    unknown = build_why_this_ranked(
        _row(
            trigger_count="",
            assessed_value="",
            tax_balance="",
            assessed_value_less_tax="",
        )
    )

    assert known_zero["tie_break"]["assessed_value"] == {
        "state": "KNOWN",
        "value": "0",
    }
    assert known_zero["tie_break"]["tax_balance"] == {
        "state": "KNOWN",
        "value": "0",
    }
    assert unknown["tie_break"]["assessed_value"] == {
        "state": "UNKNOWN",
        "value": None,
    }
    assert unknown["tie_break"]["tax_balance"] == {
        "state": "UNKNOWN",
        "value": None,
    }
    assert unknown["tie_break"]["trigger_count"] == {
        "state": "UNKNOWN",
        "value": None,
    }


def test_why_this_ranked_missing_recipe_rank_is_null_not_zero():
    missing = build_why_this_ranked(_row(recipe_rank=""))
    absent = dict(_row())
    absent.pop("recipe_rank")
    absent_receipt = build_why_this_ranked(absent)

    assert missing["recipe_rank"] is None
    assert absent_receipt["recipe_rank"] is None
    assert missing["recipe_rank"] != 0
    assert absent_receipt["recipe_rank"] != 0
    assert missing["global_rank"] == 7
    assert absent_receipt["global_rank"] == 7


def test_why_this_ranked_does_not_treat_global_rank_as_recipe_rank():
    ranked = build_why_this_ranked(_row(rank="7", global_rank="7", recipe_rank="2"))
    assert ranked["recipe_rank"] == 2
    assert ranked["global_rank"] == 7
    assert ranked["recipe_rank"] != ranked["global_rank"]


def test_why_this_ranked_preserves_unknown_material_factors_for_tie_break_inputs():
    ranked = build_why_this_ranked(
        _row(
            trigger_count="",
            assessed_value="",
            tax_balance="",
            assessed_value_less_tax="",
        )
    )
    assert "verified_equity" in ranked["unknown_material_factors"]
    assert "assessed_value" in ranked["unknown_material_factors"]
    assert "tax_balance" in ranked["unknown_material_factors"]
    assert "assessed_value_less_tax" in ranked["unknown_material_factors"]


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
