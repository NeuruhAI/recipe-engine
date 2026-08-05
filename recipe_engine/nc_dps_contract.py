"""
NC DPS strategy-candidate boundary.

This module validates analytical strategy candidates. It performs no network,
database, CRM, filing, payment, contact, recording or execution activity.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

CONTRACT_VERSION = "nc_dps_recipe_candidate.v1"

STRATEGIES = (
    "whole_property_acquisition",
    "fractional_interest_acquisition",
    "heir_property_jv",
    "title_cure_and_resale",
    "stalled_tax_case_resolution",
    "tax_foreclosure_sale_bid",
    "surplus_funds_recovery",
    "monitor",
    "reject",
)

TOP_LEVEL_FIELDS = (
    "schema_version",
    "candidate_id",
    "property_id",
    "strategy_id",
    "eligible",
    "disqualifiers",
    "economics",
    "execution_plan",
    "evidence",
    "governance",
)

ECONOMIC_FIELDS = (
    "acquisition_cost",
    "title_cure_cost",
    "legal_cost",
    "carrying_cost",
    "disposition_cost",
    "gross_exit_value",
    "expected_net_value",
    "risk_adjusted_value",
)

NONNEGATIVE_ECONOMIC_FIELDS = (
    "acquisition_cost",
    "title_cure_cost",
    "legal_cost",
    "carrying_cost",
    "disposition_cost",
    "gross_exit_value",
)

EXECUTION_PLAN_FIELDS = (
    "action_summary",
    "dependencies",
    "reversible",
    "legal_review_required",
    "human_approval_required",
)

EVIDENCE_FIELDS = (
    "property_packet_version",
    "property_packet_sha256",
    "score_receipt_sha256",
    "source_record_count",
    "contradictions",
)

HARD_STOP_FIELDS = (
    "legal_conclusion_authorized",
    "contact_authorized",
    "offer_authorized",
    "payment_authorized",
    "filing_authorized",
    "recording_authorized",
    "execution_authorized",
)

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _require_exact_keys(
    value: Mapping[str, Any],
    required: tuple[str, ...],
    field: str,
) -> None:
    actual = set(value)
    expected = set(required)

    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)

    if missing or unknown:
        raise ValueError(
            f"{field} keys invalid; missing={missing}, unknown={unknown}"
        )


def _require_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")

    return value


def _require_unique_string_list(value: Any, field: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")

    if any(
        not isinstance(item, str) or not item.strip()
        for item in value
    ):
        raise ValueError(
            f"{field} must contain only non-empty strings"
        )

    if len(value) != len(set(value)):
        raise ValueError(
            f"{field} must not contain duplicates"
        )


def _require_optional_number(
    value: Any,
    field: str,
    *,
    nonnegative: bool,
) -> None:
    if value is None:
        return

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{field} must be numeric or null"
        )

    if nonnegative and float(value) < 0:
        raise ValueError(
            f"{field} must not be negative"
        )


def _require_sha256(value: Any, field: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(
            f"{field} must be a lowercase 64-character SHA-256"
        )


def validate_p0_candidate(candidate: Mapping[str, Any]) -> None:
    if not isinstance(candidate, Mapping):
        raise ValueError("candidate must be an object")

    _require_exact_keys(
        candidate,
        TOP_LEVEL_FIELDS,
        "candidate",
    )

    if candidate["schema_version"] != CONTRACT_VERSION:
        raise ValueError(
            "wrong NC DPS recipe-candidate schema version"
        )

    _require_nonempty_string(
        candidate["candidate_id"],
        "candidate_id",
    )

    _require_nonempty_string(
        candidate["property_id"],
        "property_id",
    )

    strategy_id = candidate["strategy_id"]

    if strategy_id not in STRATEGIES:
        raise ValueError(
            f"unknown NC DPS strategy: {strategy_id!r}"
        )

    if not isinstance(candidate["eligible"], bool):
        raise ValueError(
            "eligible must be boolean"
        )

    _require_unique_string_list(
        candidate["disqualifiers"],
        "disqualifiers",
    )

    economics = candidate["economics"]

    if not isinstance(economics, Mapping):
        raise ValueError(
            "economics must be an object"
        )

    _require_exact_keys(
        economics,
        ECONOMIC_FIELDS,
        "economics",
    )

    for field in ECONOMIC_FIELDS:
        _require_optional_number(
            economics[field],
            f"economics.{field}",
            nonnegative=field in NONNEGATIVE_ECONOMIC_FIELDS,
        )

    execution_plan = candidate["execution_plan"]

    if not isinstance(execution_plan, Mapping):
        raise ValueError(
            "execution_plan must be an object"
        )

    _require_exact_keys(
        execution_plan,
        EXECUTION_PLAN_FIELDS,
        "execution_plan",
    )

    _require_nonempty_string(
        execution_plan["action_summary"],
        "execution_plan.action_summary",
    )

    _require_unique_string_list(
        execution_plan["dependencies"],
        "execution_plan.dependencies",
    )

    if not isinstance(execution_plan["reversible"], bool):
        raise ValueError(
            "execution_plan.reversible must be boolean"
        )

    if execution_plan["legal_review_required"] is not True:
        raise ValueError(
            "execution_plan.legal_review_required must be true"
        )

    if execution_plan["human_approval_required"] is not True:
        raise ValueError(
            "execution_plan.human_approval_required must be true"
        )

    evidence = candidate["evidence"]

    if not isinstance(evidence, Mapping):
        raise ValueError(
            "evidence must be an object"
        )

    _require_exact_keys(
        evidence,
        EVIDENCE_FIELDS,
        "evidence",
    )

    if (
        evidence["property_packet_version"]
        != "nc_dps_property_packet.v1"
    ):
        raise ValueError(
            "wrong property-packet contract version"
        )

    _require_sha256(
        evidence["property_packet_sha256"],
        "evidence.property_packet_sha256",
    )

    _require_sha256(
        evidence["score_receipt_sha256"],
        "evidence.score_receipt_sha256",
    )

    source_record_count = evidence["source_record_count"]

    if (
        isinstance(source_record_count, bool)
        or not isinstance(source_record_count, int)
        or source_record_count < 1
    ):
        raise ValueError(
            "evidence.source_record_count must be a positive integer"
        )

    _require_unique_string_list(
        evidence["contradictions"],
        "evidence.contradictions",
    )

    governance = candidate["governance"]

    if not isinstance(governance, Mapping):
        raise ValueError(
            "governance must be an object"
        )

    _require_exact_keys(
        governance,
        HARD_STOP_FIELDS,
        "governance",
    )

    unauthorized = [
        field
        for field in HARD_STOP_FIELDS
        if governance[field] is not False
    ]

    if unauthorized:
        raise ValueError(
            "P0 candidate carries unauthorized execution power: "
            f"{unauthorized}"
        )
