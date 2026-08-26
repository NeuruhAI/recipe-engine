"""Deterministic Opportunity Pack receipts for match/rank/evidence.

These helpers are intentionally policy-light and data-contract-heavy:
they turn a ranked recipe candidate row into deterministic receipts without
calling external systems, spending money, or inventing unavailable evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from recipe_engine.closed_loop.identity import digest


MATCH_RECEIPT_VERSION = "oppack.why-this-matched.v1"
RANK_RECEIPT_VERSION = "oppack.why-this-ranked.v2"
NBE_RECEIPT_VERSION = "oppack.next-best-evidence.v1"
DEFAULT_RECIPE_VERSION = "nc_curative_recipes.v1"
DEFAULT_RANKING_VERSION = "nc_curative_rank.v3"

_TRIGGER_TO_SIGNAL = {
    "tax_distress": "tax_delinquent",
    "court_action": "pre_foreclosure",
    "heir_or_estate": "probate_or_estate",
    "ownership_fragmentation": "title_conflict",
    "absentee_or_out_of_state": "absentee_owner",
    "long_term_ownership": "long_hold",
    "high_value_property": "value_spread",
}

_SOURCE_TYPE_TO_FAMILY = {
    "taxdelinquent": "tax_source",
    "tax_foreclosure_case": "foreclosure_source",
    "taxsale": "tax_sale_source",
    "tax_sale": "tax_sale_source",
}

_SCORE_REASON = re.compile(r"^(?P<factor>[^:]+):(?P<delta>[+-]?\d+)$")


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _sorted_unique(values: Sequence[str]) -> list[str]:
    return sorted({value for value in values if value})


def _split_pipe(value: Any) -> list[str]:
    return [part for part in str(value or "").split("|") if part]


def _recipe_version(row: Mapping[str, Any]) -> str:
    version = str(row.get("recipe_version") or "").strip()
    return version or DEFAULT_RECIPE_VERSION


def _ranking_version(row: Mapping[str, Any]) -> str:
    version = str(row.get("scoring_version") or "").strip()
    return version or DEFAULT_RANKING_VERSION


def _known_decimal(value: Any) -> dict[str, Any]:
    parsed = _as_decimal(value)
    if parsed is None:
        return {"state": "UNKNOWN", "value": None}
    return {"state": "KNOWN", "value": str(parsed)}


def _known_int(value: Any) -> dict[str, Any]:
    parsed = _as_int(value)
    if parsed is None:
        return {"state": "UNKNOWN", "value": None}
    return {"state": "KNOWN", "value": parsed}


def _input_hash_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "county_fips": row.get("county_fips"),
        "parcel_apn": row.get("parcel_apn"),
        "recipe_id": row.get("recipe_id"),
        "source_types": str(row.get("source_types") or ""),
        "trigger_classes": str(row.get("trigger_classes") or ""),
        "tax_foreclosure_indicator": str(row.get("tax_foreclosure_indicator") or ""),
        "heir_indicator": str(row.get("heir_indicator") or ""),
        "fragmented_ownership_indicator": str(
            row.get("fragmented_ownership_indicator") or ""
        ),
        "out_of_state": str(row.get("out_of_state") or ""),
        "years_owned": str(row.get("years_owned") or ""),
        "assessed_value": str(row.get("assessed_value") or ""),
        "tax_balance": str(row.get("tax_balance") or ""),
        "tax_years": str(row.get("tax_years") or ""),
        "equity_status": str(row.get("equity_status") or ""),
    }


def _coverage_state(source_types: Sequence[str]) -> str:
    if any(source_type in {"taxsale", "tax_sale"} for source_type in source_types):
        return "PARTIAL"
    if "tax_foreclosure_case" in source_types:
        return "PARTIAL"
    if "taxdelinquent" in source_types:
        return "FREE_COVERAGE"
    return "UNKNOWN"


def build_why_this_matched(row: Mapping[str, Any]) -> dict[str, Any]:
    """Build the deterministic why-this-matched receipt for one candidate row."""
    source_types = _sorted_unique(_split_pipe(row.get("source_types")))
    triggers = _sorted_unique(_split_pipe(row.get("trigger_classes")))
    source_ids = _sorted_unique(
        _SOURCE_TYPE_TO_FAMILY.get(source_type, f"source_factory.{source_type}")
        for source_type in source_types
    )
    signals_triggered = _sorted_unique(
        _TRIGGER_TO_SIGNAL.get(trigger, trigger) for trigger in triggers
    )

    years_owned = _as_int(row.get("years_owned"))
    assessed_value = _as_decimal(row.get("assessed_value"))
    tax_balance = _as_decimal(row.get("tax_balance"))

    unknown_inputs: list[str] = []
    if years_owned is None:
        unknown_inputs.append("years_owned")
    if assessed_value is None:
        unknown_inputs.append("assessed_value")
    if tax_balance is None:
        unknown_inputs.append("tax_balance")
    if str(row.get("equity_status") or "").strip().startswith("UNKNOWN"):
        unknown_inputs.append("verified_equity")

    facts_used = {
        "tax_foreclosure_indicator": _truthy(row.get("tax_foreclosure_indicator")),
        "heir_indicator": _truthy(row.get("heir_indicator")),
        "fragmented_ownership_indicator": _truthy(
            row.get("fragmented_ownership_indicator")
        ),
        "out_of_state": _truthy(row.get("out_of_state")),
        "years_owned": years_owned,
        "assessed_value": str(assessed_value) if assessed_value is not None else None,
        "tax_balance": str(tax_balance) if tax_balance is not None else None,
    }

    derived_inputs = {
        "trigger_count": _as_int(row.get("trigger_count")),
        "potential_equity_proxy": str(row.get("potential_equity_proxy") or "UNKNOWN"),
        "source_type_count": len(source_types),
    }

    amplifiers = _sorted_unique(
        [
            "absentee_owner" if _truthy(row.get("out_of_state")) else "",
            "long_hold" if years_owned is not None and years_owned >= 20 else "",
            "value_spread" if assessed_value is not None and assessed_value >= 150000 else "",
        ]
    )

    return {
        "schema_version": MATCH_RECEIPT_VERSION,
        "recipe_id": str(row.get("recipe_id") or ""),
        "recipe_version": _recipe_version(row),
        "input_hash": digest(_input_hash_payload(row)),
        "facts_used": facts_used,
        "derived_inputs": derived_inputs,
        "unknown_inputs": sorted(unknown_inputs),
        "negative_evidence": [],
        "disqualifiers_checked": [
            "government_owned_asset",
            "contact_without_permission",
            "source_rights_prohibit_automation",
        ],
        "signals_triggered": signals_triggered,
        "amplifiers": amplifiers,
        "source_ids": source_ids,
        "coverage_state": _coverage_state(source_types),
    }


def _parse_contributions(score_reasons: str) -> list[dict[str, Any]]:
    contributions: list[dict[str, Any]] = []
    for token in _split_pipe(score_reasons):
        match = _SCORE_REASON.match(token)
        if not match:
            continue
        contributions.append(
            {
                "factor_id": match.group("factor"),
                "delta": int(match.group("delta")),
            }
        )
    return contributions


def build_why_this_ranked(row: Mapping[str, Any]) -> dict[str, Any]:
    """Build the deterministic why-this-ranked receipt for one candidate row."""
    contributions = _parse_contributions(str(row.get("score_reasons") or ""))
    penalties = [item for item in contributions if item["delta"] < 0]

    unknown_material_factors: list[str] = []
    equity_status = str(row.get("equity_status") or "")
    if equity_status.startswith("UNKNOWN"):
        unknown_material_factors.append("verified_equity")
    if "assessed_value" in row and _as_decimal(row.get("assessed_value")) is None:
        unknown_material_factors.append("assessed_value")
    if "tax_balance" in row and _as_decimal(row.get("tax_balance")) is None:
        unknown_material_factors.append("tax_balance")
    if (
        "assessed_value_less_tax" in row
        and _as_decimal(row.get("assessed_value_less_tax")) is None
    ):
        unknown_material_factors.append("assessed_value_less_tax")

    contradictions: list[str] = []
    if not _truthy(row.get("tax_foreclosure_indicator")) and "court_action" in _split_pipe(
        row.get("trigger_classes")
    ):
        contradictions.append(
            "court_action_triggered_without_boolean_tax_foreclosure_indicator"
        )

    tie_break = {
        "trigger_count": _known_int(row.get("trigger_count")),
        "assessed_value": _known_decimal(row.get("assessed_value")),
        "tax_balance": _known_decimal(row.get("tax_balance")),
        "assessed_value_less_tax": _known_decimal(row.get("assessed_value_less_tax")),
        "county_fips": str(row.get("county_fips") or ""),
        "parcel_apn": str(row.get("parcel_apn") or ""),
    }

    global_rank = _as_int(row.get("global_rank"))
    if global_rank is None:
        # `rank` is GLOBAL_RANK; never treat it as recipe_rank.
        global_rank = _as_int(row.get("rank"))

    return {
        "schema_version": RANK_RECEIPT_VERSION,
        "recipe_id": str(row.get("recipe_id") or ""),
        "recipe_version": _recipe_version(row),
        "ranking_version": _ranking_version(row),
        "input_hash": digest(_input_hash_payload(row)),
        "component_contributions": contributions,
        "penalties": penalties,
        "unknown_material_factors": sorted(unknown_material_factors),
        "contradictions": contradictions,
        "freshness_effects": {
            "tax_years": str(row.get("tax_years") or "UNKNOWN"),
        },
        "tie_break": tie_break,
        # recipe_rank is per-recipe only. Missing/invalid stays null — never 0.
        # v2 exists because v1 recipe_rank meant GLOBAL_RANK. Additive fields
        # below do not change v2 recipe_rank/global_rank meaning.
        "recipe_rank": _as_int(row.get("recipe_rank")),
        "global_rank": global_rank,
        "recipe_membership_count": _as_int(row.get("recipe_membership_count")),
        "ranking_universe_id": str(row.get("ranking_universe_id") or "") or None,
        "score": _as_int(row.get("score") if row.get("score") not in (None, "") else row.get("curative_priority_score")),
        "score_name": str(row.get("score_name") or "curative_priority_score"),
        "score_semantics": str(row.get("score_semantics") or "DETERMINISTIC_ADDITIVE_CURATIVE_PRIORITY"),
        "score_version": _ranking_version(row),
    }


def build_next_best_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    """Emit one deterministic next-best-evidence recommendation."""
    recipe_id = str(row.get("recipe_id") or "")
    source_types = _sorted_unique(_split_pipe(row.get("source_types")))

    if recipe_id in {
        "stalled_tax_foreclosure_heirship",
        "tax_foreclosure_curative",
        "tax_foreclosure_fractional_title",
    }:
        return {
            "schema_version": NBE_RECEIPT_VERSION,
            "question": "What is the current docket stage and redemption timeline?",
            "source_id_or_family": "county_foreclosure_docket_publication",
            "field_needed": "foreclosure_stage|sale_date|redemption_deadline",
            "expected_information_gain": "HIGH",
            "decision_impact": "Can upgrade monitor -> curative execution path",
            "estimated_cost": "$0",
            "estimated_latency": "same_day_to_3d",
            "operator_effort": "LOW",
            "compliance_risk": "LOW",
            "authority_required": "NONE",
            "fallback": "operator docket verification",
            "current_coverage_state": "PARTIAL",
        }

    if str(row.get("equity_status") or "").startswith("UNKNOWN"):
        return {
            "schema_version": NBE_RECEIPT_VERSION,
            "question": "What recorded debt remains and has any release already occurred?",
            "source_id_or_family": "gap.deed.recorded_instruments",
            "field_needed": "mortgage_balance_proxy|release_recorded|lien_stack",
            "expected_information_gain": "HIGH",
            "decision_impact": "Can materially change recipe-rank and strategy",
            "estimated_cost": "$0",
            "estimated_latency": "3d_to_7d",
            "operator_effort": "MEDIUM",
            "compliance_risk": "LOW",
            "authority_required": "RECORDER_ACCESS",
            "fallback": "title_curative_queue",
            "current_coverage_state": "WALLED",
        }

    return {
        "schema_version": NBE_RECEIPT_VERSION,
        "question": "Can we confirm current owner mailing state and occupancy signal freshness?",
        "source_id_or_family": (
            "tax_source" if "taxdelinquent" in source_types else "owner_parcel_source"
        ),
        "field_needed": "mailing_state|out_of_state|situs_alignment",
        "expected_information_gain": "MEDIUM",
        "decision_impact": "Can re-rank between similar-score candidates",
        "estimated_cost": "$0",
        "estimated_latency": "same_day",
        "operator_effort": "LOW",
        "compliance_risk": "LOW",
        "authority_required": "NONE",
        "fallback": "continue_public_enrichment",
        "current_coverage_state": _coverage_state(source_types),
    }
