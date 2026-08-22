"""Canonical, founder-tunable opportunity recipe definitions.

The values below are policy, not learned parameters.  Each score delta is
named, versioned and returned in the LandOS explanation.  Changing one requires
a new recipe version; a historic evaluation remains bound to this exact config
digest.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contracts import (
    CANONICAL_RECIPE_ID,
    CANONICAL_RECIPE_VERSION,
    RECIPE_CONTRACT_VERSION,
    SCORE_VERSION,
    recipe_config_digest,
    validate_recipe,
)


def _source(source_id: str, mode: str, expected_information: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "mode": mode,
        "expected_information": expected_information,
    }


_RECIPE: dict[str, Any] = {
    "schema_version": RECIPE_CONTRACT_VERSION,
    "recipe_id": CANONICAL_RECIPE_ID,
    "version": CANONICAL_RECIPE_VERSION,
    "score_version": SCORE_VERSION,
    "description": (
        "Deterministic land-acquisition opportunity scoring with independent "
        "confidence, authority, contact, deal and offer gates."
    ),
    "score": {"base": 0, "minimum": 0, "maximum": 100},
    "asset_aliases": {
        "waterfront": "waterfront",
        "water_front": "waterfront",
        "acreage": "acreage",
        "rural_acreage": "acreage",
        "vacant_land": "vacant_land",
        "vacant": "vacant_land",
        "residential": "residential",
        "single_family": "residential",
        "single_family_residential": "residential",
        "commercial": "commercial",
        "retail": "commercial",
        "industrial": "commercial",
        "unknown": "unknown",
    },
    "signal_rules": [
        {
            "signal_type": "tax_delinquent",
            "label": "verified tax delinquency",
            "category": "distress",
            "points_if_present": 22,
            "points_if_absent": 0,
            "confidence_weight": 10,
            "material": True,
            "next_sources": [
                _source("COUNTY_TAX_ROLL", "AUTOMATIC", "current delinquency and amount"),
                _source("COUNTY_TAX_COLLECTOR", "MANUAL", "official payoff/status"),
            ],
        },
        {
            "signal_type": "probate_or_estate",
            "label": "probate or estate circumstance",
            "category": "life_event",
            "points_if_present": 18,
            "points_if_absent": 0,
            "confidence_weight": 10,
            "material": True,
            "next_sources": [
                _source("PUBLIC_PROBATE_INDEX", "AUTOMATIC", "estate case match or no-match"),
                _source("COUNTY_ESTATE_INDEX_OFFLINE", "MANUAL", "offline estate file and authority"),
                _source("RECORDED_DEED", "MANUAL", "chain of title and vesting"),
            ],
        },
        {
            "signal_type": "pre_foreclosure",
            "label": "verified pre-foreclosure",
            "category": "distress",
            "points_if_present": 16,
            "points_if_absent": 0,
            "confidence_weight": 8,
            "material": True,
            "next_sources": [
                _source("COUNTY_FORECLOSURE_DOCKET", "MANUAL", "current filing status"),
            ],
        },
        {
            "signal_type": "vacancy",
            "label": "vacancy evidence",
            "category": "condition",
            "points_if_present": 12,
            "points_if_absent": 0,
            "confidence_weight": 6,
            "material": False,
            "next_sources": [
                _source("FIELD_OBSERVATION", "MANUAL", "current occupancy indicators"),
            ],
        },
        {
            "signal_type": "code_violation",
            "label": "open code violation",
            "category": "distress",
            "points_if_present": 8,
            "points_if_absent": 0,
            "confidence_weight": 5,
            "material": False,
            "next_sources": [
                _source("MUNICIPAL_CODE_PORTAL", "AUTOMATIC", "open violation detail"),
            ],
        },
        {
            "signal_type": "absentee_owner",
            "label": "absentee ownership",
            "category": "owner",
            "points_if_present": 10,
            "points_if_absent": 0,
            "confidence_weight": 6,
            "material": True,
            "next_sources": [
                _source("COUNTY_PARCEL_ROLL", "AUTOMATIC", "situs and owner mailing comparison"),
            ],
        },
        {
            "signal_type": "long_hold",
            "label": "long ownership tenure",
            "category": "owner",
            "points_if_present": 8,
            "points_if_absent": 0,
            "confidence_weight": 5,
            "material": False,
            "next_sources": [
                _source("RECORDED_DEED", "MANUAL", "vesting date and chain of title"),
            ],
        },
        {
            "signal_type": "value_spread",
            "label": "underwritten value spread",
            "category": "economics",
            "points_if_present": 24,
            "points_if_absent": 0,
            "confidence_weight": 12,
            "material": True,
            "next_sources": [
                _source("LANDOS_VALUATION", "AUTOMATIC", "comp-grounded value range"),
            ],
        },
        {
            "signal_type": "title_conflict",
            "label": "unresolved title conflict",
            "category": "risk",
            "points_if_present": -30,
            "points_if_absent": 0,
            "confidence_weight": 12,
            "material": True,
            "next_sources": [
                _source("RECORDED_DEED", "MANUAL", "current vesting and conflicting instruments"),
            ],
        },
        {
            "signal_type": "marketability_risk",
            "label": "material marketability risk",
            "category": "risk",
            "points_if_present": -18,
            "points_if_absent": 0,
            "confidence_weight": 8,
            "material": True,
            "next_sources": [
                _source("LAND_DD", "AUTOMATIC", "access, flood, wetlands and buildability"),
            ],
        },
        {
            "signal_type": "government_owner",
            "label": "government ownership",
            "category": "exclusion",
            "points_if_present": -100,
            "points_if_absent": 0,
            "confidence_weight": 10,
            "material": True,
            "next_sources": [
                _source("COUNTY_PARCEL_ROLL", "AUTOMATIC", "recorded owner class"),
            ],
        },
    ],
    "confidence": {
        "formula": (
            "100 * sum(factor_weight * stated_credit) / sum(all_material_weights); "
            "YES and NO readiness facts both count as known, UNKNOWN counts zero"
        ),
        "credits": {"HIGH": 1.0, "MEDIUM": 0.75, "LOW": 0.5, "UNKNOWN": 0.0},
        "conflicted_credit": 0.25,
        "readiness_fact_weight": 5,
    },
    "readiness": {
        "authority": ["legal_authority_verified"],
        "contact": [
            "identity_verified",
            "contact_point_verified",
            "contact_authorized",
        ],
        "deal": [
            "identity_verified",
            "legal_authority_verified",
            "value_established",
            "underwriting_complete",
            "risk_review_complete",
        ],
        "offer": [
            "identity_verified",
            "legal_authority_verified",
            "value_established",
            "underwriting_complete",
            "risk_review_complete",
            "offer_authorized",
        ],
    },
    "action_rules": [
        {"fact": "identity_verified", "when": ["NO", "UNKNOWN"],
         "action": "RESOLVE_IDENTITY", "route": "IDENTITY_RESEARCH"},
        {"fact": "legal_authority_verified", "when": ["NO", "UNKNOWN"],
         "action": "ESTABLISH_LEGAL_AUTHORITY", "route": "AUTHORITY_RESEARCH"},
        {"fact": "value_established", "when": ["NO", "UNKNOWN"],
         "action": "ESTABLISH_VALUE", "route": "UNDERWRITING"},
        {"fact": "underwriting_complete", "when": ["NO", "UNKNOWN"],
         "action": "COMPLETE_UNDERWRITING", "route": "UNDERWRITING"},
        {"fact": "risk_review_complete", "when": ["NO", "UNKNOWN"],
         "action": "COMPLETE_RISK_REVIEW", "route": "DUE_DILIGENCE"},
        {"fact": "contact_point_verified", "when": ["NO", "UNKNOWN"],
         "action": "RESOLVE_CONTACT", "route": "CONTACT_RESEARCH"},
        {"fact": "contact_authorized", "when": ["NO", "UNKNOWN"],
         "action": "VERIFY_CONTACT_AUTHORIZATION", "route": "COMPLIANCE_REVIEW"},
        {"fact": "offer_authorized", "when": ["NO", "UNKNOWN"],
         "action": "REQUEST_OFFER_AUTHORIZATION", "route": "ACQUISITION_REVIEW"},
    ],
    "fact_sources": {
        "identity_verified": [
            _source("COUNTY_PARCEL_ROLL", "AUTOMATIC", "recorded owner identity"),
        ],
        "legal_authority_verified": [
            _source("PUBLIC_PROBATE_INDEX", "AUTOMATIC", "estate case and appointed party"),
            _source("COUNTY_ESTATE_INDEX_OFFLINE", "MANUAL", "adjudicating authority record"),
            _source("RECORDED_DEED", "MANUAL", "vesting authority"),
        ],
        "value_established": [
            _source("LANDOS_VALUATION", "AUTOMATIC", "comp-grounded value range"),
        ],
        "underwriting_complete": [
            _source("LAND_DD", "AUTOMATIC", "due-diligence inputs and risk flags"),
        ],
        "risk_review_complete": [
            _source("LAND_DD", "AUTOMATIC", "access, environmental and physical risk"),
        ],
        "contact_point_verified": [
            _source("CONTACT_RESOLUTION_ROUTER", "AUTOMATIC", "living seller-side contact route"),
        ],
        "contact_authorized": [
            _source("DEEDSONAR_CONTACT_GATE", "AUTOMATIC", "channel-specific authorization"),
        ],
        "offer_authorized": [
            _source("DEEDSONAR_OFFER_GATE", "MANUAL", "explicit operator offer authority"),
        ],
    },
    "priority_bands": [
        {"min_score": 75, "min_confidence": 60, "priority": "P1"},
        {"min_score": 55, "min_confidence": 40, "priority": "P2"},
        {"min_score": 0, "min_confidence": 0, "priority": "P3_RESEARCH"},
    ],
}
_RECIPE["config_digest"] = recipe_config_digest(_RECIPE)
validate_recipe(_RECIPE)


def land_acquisition_recipe_v1() -> dict[str, Any]:
    """Return a defensive copy of the canonical wire recipe."""
    return deepcopy(_RECIPE)


LAND_ACQUISITION_RECIPE_V1_DIGEST = _RECIPE["config_digest"]
