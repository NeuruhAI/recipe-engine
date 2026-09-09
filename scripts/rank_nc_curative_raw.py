#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from recipe_engine.closed_loop.identity import digest
from recipe_engine.opportunity_intelligence.oppack_receipts import (
    DEFAULT_RANKING_VERSION,
    DEFAULT_RECIPE_VERSION,
    _parse_contributions,
)


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None



SCORE_NAME = "curative_priority_score"
SCORE_SEMANTICS = "DETERMINISTIC_ADDITIVE_CURATIVE_PRIORITY"


def _recipe_version_of(row: dict[str, Any]) -> str:
    version = str(row.get("recipe_version") or "").strip()
    return version or DEFAULT_RECIPE_VERSION


def _ranking_version_of(row: dict[str, Any]) -> str:
    version = str(row.get("scoring_version") or row.get("ranking_version") or "").strip()
    return version or DEFAULT_RANKING_VERSION


def _recipe_partition_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("recipe_id") or ""), _recipe_version_of(row))


def _ranking_universe_id(rows: list[dict[str, Any]]) -> str:
    payload = {
        "ranking_version": DEFAULT_RANKING_VERSION,
        "recipe_version": DEFAULT_RECIPE_VERSION,
        "members": [
            [
                str(row.get("county_fips") or ""),
                str(row.get("parcel_apn") or ""),
                str(row.get("recipe_id") or ""),
                int(row["curative_priority_score"]),
            ]
            for row in rows
        ],
    }
    return digest(payload)


def integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def triggers(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    source_types = set(str(row.get("source_types") or "").split("|"))
    if "taxdelinquent" in source_types:
        out.append("tax_distress")
    if truthy(row.get("tax_foreclosure_indicator")) or "tax_foreclosure_case" in source_types:
        out.append("court_action")
    if truthy(row.get("heir_indicator")):
        out.append("heir_or_estate")
    if truthy(row.get("fragmented_ownership_indicator")):
        out.append("ownership_fragmentation")
    if truthy(row.get("out_of_state")):
        out.append("absentee_or_out_of_state")
    years = integer(row.get("years_owned"))
    if years is not None and years >= 20:
        out.append("long_term_ownership")
    assessed = num(row.get("assessed_value"))
    if assessed is not None and assessed >= 150000:
        out.append("high_value_property")
    return list(dict.fromkeys(out))


def base_score(row: dict[str, Any]) -> tuple[int, list[str]]:
    """Transparent pre-title ranking using only facts actually present.

    Equity itself is not scored because mortgage/lien balances are not yet known.
    Value and ownership duration are used only as a potential-equity proxy.
    """
    score = 0
    reasons: list[str] = []
    source_types = set(str(row.get("source_types") or "").split("|"))

    if "taxdelinquent" in source_types:
        score += 10
        reasons.append("published_tax_distress:+10")

    if truthy(row.get("tax_foreclosure_indicator")) or "tax_foreclosure_case" in source_types:
        score += 25
        reasons.append("active_tax_foreclosure:+25")

    if truthy(row.get("heir_indicator")):
        score += 20
        reasons.append("heir_or_estate_signal:+20")

    if truthy(row.get("fragmented_ownership_indicator")):
        score += 8
        reasons.append("ownership_fragmentation_proxy:+8")

    if truthy(row.get("out_of_state")):
        score += 5
        reasons.append("out_of_state_owner:+5")

    years = integer(row.get("years_owned"))
    if years is not None and years >= 20:
        score += 8
        reasons.append("ownership_20_plus_years:+8")
    elif years is not None and years >= 10:
        score += 4
        reasons.append("ownership_10_plus_years:+4")

    assessed = num(row.get("assessed_value"))
    if assessed is not None:
        if assessed >= 250000:
            score += 10
            reasons.append("assessed_value_250k_plus_proxy:+10")
        elif assessed >= 150000:
            score += 8
            reasons.append("assessed_value_150k_plus_proxy:+8")
        elif assessed >= 75000:
            score += 5
            reasons.append("assessed_value_75k_plus_proxy:+5")

    tax_balance = num(row.get("tax_balance"))
    if tax_balance is not None:
        if tax_balance >= 10000:
            score += 8
            reasons.append("tax_balance_10k_plus:+8")
        elif tax_balance >= 5000:
            score += 5
            reasons.append("tax_balance_5k_plus:+5")
        elif tax_balance >= 1000:
            score += 2
            reasons.append("tax_balance_1k_plus:+2")

    t = triggers(row)
    if len(t) >= 3:
        score += 10
        reasons.append("multi_trigger_3_plus:+10")

    return min(score, 100), reasons


def recipe(row: dict[str, Any]) -> tuple[str, str]:
    heir = truthy(row.get("heir_indicator"))
    fragmented = truthy(row.get("fragmented_ownership_indicator"))
    foreclosure = truthy(row.get("tax_foreclosure_indicator")) or "tax_foreclosure_case" in str(row.get("source_types") or "")
    out_state = truthy(row.get("out_of_state"))
    years = integer(row.get("years_owned")) or 0

    if heir and foreclosure:
        return "stalled_tax_foreclosure_heirship", "TITLE_CURATIVE_HEIR"
    if heir:
        return "heir_property_acquisition_research", "TITLE_CURATIVE_HEIR"
    if foreclosure and fragmented:
        return "tax_foreclosure_fractional_title", "TITLE_CURATIVE"
    if foreclosure:
        return "tax_foreclosure_curative", "TITLE_CURATIVE"
    if fragmented:
        return "fractional_family_ownership", "TITLE_CURATIVE"
    if out_state and years >= 20:
        return "long_term_absentee_potential_equity", "ACQUISITION_RESEARCH"
    return "published_tax_distress", "ENRICHMENT"


# The ladder in ``recipe`` is first-match-wins: it returns exactly ONE recipe
# id per row and silently discards every other branch whose predicate is also
# true.  ``recipe_matches`` enumerates the SAME predicates in the SAME order
# without collapsing them.  It adds no predicate, no weight and no score;
# ``recipe_matches(row)[0] == recipe(row)[0]`` for every row, by construction.
#
# ``published_tax_distress`` is the ladder's unconditional fallthrough, not a
# positive predicate, so it is a match only when no positive branch fires.
# Deriving it from ``source_types`` or from a score factor would be a new
# recipe predicate and is refused here.
RECIPE_LADDER: tuple[tuple[str, str], ...] = (
    ("stalled_tax_foreclosure_heirship", "TITLE_CURATIVE_HEIR"),
    ("heir_property_acquisition_research", "TITLE_CURATIVE_HEIR"),
    ("tax_foreclosure_fractional_title", "TITLE_CURATIVE"),
    ("tax_foreclosure_curative", "TITLE_CURATIVE"),
    ("fractional_family_ownership", "TITLE_CURATIVE"),
    ("long_term_absentee_potential_equity", "ACQUISITION_RESEARCH"),
    ("published_tax_distress", "ENRICHMENT"),
)
RECIPE_FALLTHROUGH_ID = "published_tax_distress"


def recipe_matches(row: dict[str, Any]) -> list[str]:
    """Every ladder branch whose predicate holds, in ladder precedence order.

    Position 0 is the primary and equals ``recipe(row)[0]``.  The fallthrough
    id appears only when no positive branch matched (then it is the sole
    element), exactly as the ladder would have returned it.
    """
    heir = truthy(row.get("heir_indicator"))
    fragmented = truthy(row.get("fragmented_ownership_indicator"))
    foreclosure = truthy(row.get("tax_foreclosure_indicator")) or "tax_foreclosure_case" in str(row.get("source_types") or "")
    out_state = truthy(row.get("out_of_state"))
    years = integer(row.get("years_owned")) or 0

    predicates = (
        heir and foreclosure,
        heir,
        foreclosure and fragmented,
        foreclosure,
        fragmented,
        out_state and years >= 20,
    )
    matches = [
        recipe_id
        for (recipe_id, _lane), holds in zip(RECIPE_LADDER[:-1], predicates)
        if holds
    ]
    if not matches:
        matches.append(RECIPE_FALLTHROUGH_ID)
    return matches


def tier(score: int) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 35:
        return "D"
    return "E"


def _sort_numeric_key(value: Any) -> tuple[int, float]:
    """Deterministic numeric tie-break preserving UNKNOWN != known zero."""
    numeric = num(value)
    if numeric is None:
        return 1, 0.0
    return 0, float(numeric)


def rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        score, reasons = base_score(row)
        recipe_id, lane = recipe(row)
        t = triggers(row)
        assessed = num(row.get("assessed_value"))
        tax_balance = num(row.get("tax_balance"))
        spread = num(row.get("assessed_value_less_tax"))
        spread_unknown, spread_value = _sort_numeric_key(row.get("assessed_value_less_tax"))
        assessed_unknown, assessed_value = _sort_numeric_key(row.get("assessed_value"))
        tax_unknown, tax_value = _sort_numeric_key(row.get("tax_balance"))
        years_owned = integer(row.get("years_owned"))
        enriched = dict(row)
        enriched.update({
            "curative_priority_score": score,
            "priority_tier": tier(score),
            "recipe_id": recipe_id,
            "research_lane": lane,
            "trigger_count": len(t),
            "trigger_classes": "|".join(t),
            "score_reasons": "|".join(reasons),
            "potential_equity_proxy": (
                "HIGH"
                if assessed is not None and years_owned is not None and assessed >= 150000 and years_owned >= 20
                else "MEDIUM"
                if assessed is not None and assessed >= 75000
                else "UNKNOWN"
            ),
            "verified_equity": "",
            "equity_status": row.get("equity_status") or "UNKNOWN_NEEDS_DEBT_SEARCH",
            "phone": "",
            "phone_source": "",
            "phone_status": "NOT_ENRICHED",
            "next_research_action": "PULL_DEED_MORTGAGE_LIENS" if score >= 55 else "CONTINUE_PUBLIC_ENRICHMENT",
            "contact_authorized": False,
            "_sort_spread_unknown": spread_unknown,
            "_sort_spread": spread_value,
            "_sort_assessed_unknown": assessed_unknown,
            "_sort_assessed": assessed_value,
            "_sort_tax_unknown": tax_unknown,
            "_sort_tax": tax_value,
        })
        out.append(enriched)

    out.sort(key=lambda r: (
        -int(r["curative_priority_score"]),
        -int(r["trigger_count"]),
        int(r["_sort_spread_unknown"]),
        -float(r["_sort_spread"]),
        int(r["_sort_assessed_unknown"]),
        -float(r["_sort_assessed"]),
        int(r["_sort_tax_unknown"]),
        -float(r["_sort_tax"]),
        str(r.get("county_fips") or ""),
        str(r.get("parcel_apn") or ""),
    ))
    # `rank` remains GLOBAL_RANK over the full universe. Do not rename it.
    # recipe_rank is 1-based among recipe_id + recipe_version, same global order.
    universe_id = _ranking_universe_id(out)
    membership_totals: Counter[tuple[str, str]] = Counter(_recipe_partition_key(row) for row in out)
    recipe_seen: Counter[tuple[str, str]] = Counter()
    for idx, row in enumerate(out, 1):
        key = _recipe_partition_key(row)
        recipe_seen[key] += 1
        row["rank"] = idx
        row["global_rank"] = idx
        row["recipe_rank"] = recipe_seen[key]
        row["recipe_membership_count"] = membership_totals[key]
        row["recipe_version"] = _recipe_version_of(row)
        row["ranking_version"] = _ranking_version_of(row)
        row["scoring_version"] = _ranking_version_of(row)
        row["score"] = row["curative_priority_score"]
        row["score_name"] = SCORE_NAME
        row["score_semantics"] = SCORE_SEMANTICS
        row["ranking_universe_id"] = universe_id
        row["component_contributions"] = json.dumps(_parse_contributions(str(row.get("score_reasons") or "")), separators=(",", ":"), sort_keys=True)
        for key_name in (
            "_sort_spread_unknown",
            "_sort_spread",
            "_sort_assessed_unknown",
            "_sort_assessed",
            "_sort_tax_unknown",
            "_sort_tax",
        ):
            row.pop(key_name, None)
    return out


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    if fields is None:
        leading = [k for k in ("rank", "global_rank", "recipe_rank", "recipe_membership_count") if k in rows[0]]
        fields = leading + [k for k in rows[0].keys() if k not in leading]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def deedsonar_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for r in rows:
        notes = (
            f"NC DPS raw curative research | rank={r['rank']} | score={r['curative_priority_score']} "
            f"| tier={r['priority_tier']} | recipe={r['recipe_id']} | triggers={r['trigger_classes']} "
            f"| equity={r['equity_status']} | contact_authorized=false"
        )
        result.append({
            "county": r.get("county"),
            "county_fips": r.get("county_fips"),
            "parcel_apn": r.get("parcel_apn"),
            "owner_name": r.get("owner_name"),
            "property_address": r.get("property_address"),
            "owner_mailing_address": r.get("owner_mailing_address"),
            "owner_mailing_state": r.get("owner_mailing_state"),
            "assessed_value": r.get("assessed_value"),
            "market_value": "",
            "tax_balance": r.get("tax_balance"),
            "tax_years": r.get("tax_years"),
            "notes": notes,
            "source_url": r.get("source_url"),
            "curative_priority_score": r.get("curative_priority_score"),
            "score": r.get("score", r.get("curative_priority_score")),
            "score_name": r.get("score_name", SCORE_NAME),
            "score_semantics": r.get("score_semantics", SCORE_SEMANTICS),
            "priority_tier": r.get("priority_tier"),
            "recipe_id": r.get("recipe_id"),
            "global_rank": r.get("global_rank", r.get("rank")),
            "recipe_rank": r.get("recipe_rank"),
            "recipe_membership_count": r.get("recipe_membership_count"),
            "ranking_version": r.get("ranking_version", r.get("scoring_version", "")),
            "ranking_universe_id": r.get("ranking_universe_id", ""),
            "score_reasons": r.get("score_reasons"),
            "component_contributions": r.get("component_contributions", ""),
            "why_matched_receipt": r.get("why_matched_receipt", ""),
            "why_ranked_receipt": r.get("why_ranked_receipt", ""),
            "next_best_evidence_receipt": r.get("next_best_evidence_receipt", ""),
            "unknown_material_factors": r.get("unknown_material_factors", ""),
            "contradictions": r.get("contradictions", ""),
            "source_families": r.get("source_families", ""),
            "independent_source_family_count": r.get("independent_source_family_count", ""),
            "input_hash": r.get("input_hash", ""),
            "scoring_version": r.get("scoring_version", ""),
            "recipe_version": r.get("recipe_version", ""),
            "trigger_classes": r.get("trigger_classes"),
            "years_owned": r.get("years_owned"),
            "out_of_state": r.get("out_of_state"),
            "heir_indicator": r.get("heir_indicator"),
            "tax_foreclosure_indicator": r.get("tax_foreclosure_indicator"),
            "equity_status": r.get("equity_status"),
            "phone": "",
            "contact_authorized": False,
        })
    return result


def run(input_csv: Path, output_dir: Path) -> dict[str, Any]:
    with input_csv.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    ranked = rank_rows(rows)
    output_dir.mkdir(parents=True, exist_ok=True)

    write_csv(output_dir / "NC_CURATIVE_ALL.csv", ranked)
    write_csv(output_dir / "NC_CURATIVE_TOP_500.csv", ranked[:500])
    write_csv(output_dir / "NC_CURATIVE_TOP_100.csv", ranked[:100])
    write_csv(output_dir / "DEEDSONAR_RAW_IMPORT.csv", deedsonar_rows(ranked))

    summary = {
        "input_properties": len(rows),
        "ranked_properties": len(ranked),
        "tier_counts": dict(Counter(r["priority_tier"] for r in ranked)),
        "recipe_counts": dict(Counter(r["recipe_id"] for r in ranked)),
        "top_500": min(500, len(ranked)),
        "top_100": min(100, len(ranked)),
        "verified_equity_count": 0,
        "equity_warning": "Assessed value and ownership duration are only potential-equity proxies until mortgage/lien debt is pulled.",
        "phone_status": "NOT_ENRICHED",
        "contact_authorized": False,
        "deedsonar_import": str(output_dir / "DEEDSONAR_RAW_IMPORT.csv"),
    }
    (output_dir / "NC_CURATIVE_RECIPE_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    print(json.dumps(run(args.input, args.output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
