"""Daily estate-sale feed → parcel identity join (consumer only).

UNLOCK: ESTATE_SALE_JOIN
GAP: IDENTITY_JOIN_MISSING → JOIN_EXISTING_DAILY_FEED

recipe-engine already harvests estatesales.net into daily feed rows
(``run.py`` / ``recipe.sale_events``) and already *consumes* identity
receipts in ``scripts/graduate_signals.py`` by ``listing_id == SIGNAL_ID``.

It does NOT own address→parcel resolution. The identity owner is:

    NeuruhAI/landos-engine
      scripts/resolve_signal_parcel_identity.py
      app/resolvers/address_to_apn.py :: AddressApnResolver

This module joins existing daily-feed rows to existing identity-receipt
rows and projects MATCH / NO_MATCH / UNKNOWN. It never calls LandOS,
DeedSonar, estatesales.net, or a spatial resolver, and it never treats
``estate-{listing_id}`` as a real parcel.
"""

from __future__ import annotations

import re
from collections import Counter
from enum import Enum
from typing import Any, Mapping, Never, Sequence

from recipe_engine.opportunity_intelligence.contracts import property_identity
from recipe_engine.parse import canon_keys


SCHEMA_VERSION = "neuruh.estate-sale-identity-join.v1"
RECIPE_ID = "estate_sale_property"
JOIN_KEY = "listing_id == SIGNAL_ID"
UNLOCK = "ESTATE_SALE_JOIN"
GAP = "IDENTITY_JOIN_MISSING"
UNLOCK_MODE = "JOIN_EXISTING_DAILY_FEED"

IDENTITY_OWNER_REPO = "NeuruhAI/landos-engine"
IDENTITY_OWNER_SCRIPT = "scripts/resolve_signal_parcel_identity.py"
IDENTITY_OWNER_RESOLVER = "app/resolvers/address_to_apn.py::AddressApnResolver"

JOIN_DECISIONS = ("MATCH", "NO_MATCH", "UNKNOWN")

# Same refusal the identity owner already documents. Used only to reject a
# fabricated MATCH — this module never intersects a point against a layer.
SENTINEL_PIN = r"^99-|-9999-9999-"
_SENTINEL_PIN = re.compile(SENTINEL_PIN)
_SYNTHETIC_LISTING = re.compile(r"^estate-", re.IGNORECASE)


class IdentityVerdict(str, Enum):
    """Verdicts ``resolve_signal_parcel_identity`` already emits."""

    RESOLVED = "RESOLVED"
    SENTINEL_PARCEL_REJECTED = "SENTINEL_PARCEL_REJECTED"
    NO_PARCEL_AT_POINT = "NO_PARCEL_AT_POINT"
    AMBIGUOUS_MULTIPLE_PARCELS = "AMBIGUOUS_MULTIPLE_PARCELS"
    NO_RESOLVER_FOR_COUNTY = "NO_RESOLVER_FOR_COUNTY"
    RESOLVER_UNAVAILABLE = "RESOLVER_UNAVAILABLE"


def identity_owner() -> dict[str, Any]:
    return {
        "repo": IDENTITY_OWNER_REPO,
        "script": IDENTITY_OWNER_SCRIPT,
        "resolver": IDENTITY_OWNER_RESOLVER,
        "in_recipe_engine": False,
        "consumer_seam": "scripts/graduate_signals.py",
        "note": (
            "recipe-engine is the identity-join consumer. "
            "Address→parcel spatial resolve is owned by landos-engine."
        ),
    }


def is_real_parcel_apn(value: Any) -> bool:
    """True only for a non-empty APN that is not a listing sentinel."""
    text = str(value or "").strip()
    if not text:
        return False
    if _SYNTHETIC_LISTING.match(text):
        return False
    if _SENTINEL_PIN.search(text):
        return False
    return True


def feed_listing_id(row: Mapping[str, Any]) -> str:
    return str(row.get("listing_id") or row.get("SIGNAL_ID") or "").strip()


def identity_signal_id(row: Mapping[str, Any]) -> str:
    return str(row.get("SIGNAL_ID") or row.get("listing_id") or "").strip()


def index_identity_rows(rows: Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    """Index identity-receipt / graduation-result rows the way graduate_signals does."""
    indexed: dict[str, Mapping[str, Any]] = {}
    if not rows:
        return indexed
    for row in rows:
        signal_id = identity_signal_id(row)
        if signal_id:
            indexed[signal_id] = row
    return indexed


def _parse_verdict(raw: Any) -> IdentityVerdict | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for verdict in IdentityVerdict:
        if verdict.value == text:
            return verdict
    return None


def _decision_for_verdict(
    verdict: IdentityVerdict, apn: Any
) -> tuple[str, str]:
    match verdict:
        case IdentityVerdict.RESOLVED:
            if is_real_parcel_apn(apn):
                return "MATCH", "identity receipt RESOLVED with a real parcel APN"
            return (
                "UNKNOWN",
                "IDENTITY_VERDICT=RESOLVED but REAL_PARCEL_APN is missing, "
                "synthetic estate-{listing_id}, or a sentinel PIN — not a match",
            )
        case IdentityVerdict.SENTINEL_PARCEL_REJECTED:
            return (
                "NO_MATCH",
                "identity owner rejected a county catch-all / sentinel parcel",
            )
        case IdentityVerdict.NO_PARCEL_AT_POINT:
            return "NO_MATCH", "identity owner found no parcel at the exact point"
        case IdentityVerdict.AMBIGUOUS_MULTIPLE_PARCELS:
            return (
                "UNKNOWN",
                "identity owner found multiple parcels — refuse to pick",
            )
        case IdentityVerdict.NO_RESOLVER_FOR_COUNTY:
            return (
                "UNKNOWN",
                "IDENTITY_ABSENT: landos-engine has no parcel resolver for this county",
            )
        case IdentityVerdict.RESOLVER_UNAVAILABLE:
            return (
                "UNKNOWN",
                "IDENTITY_ABSENT: landos-engine resolver was unavailable",
            )
        case _:
            unreachable: Never = verdict
            raise AssertionError(f"unhandled identity verdict: {unreachable}")


def join_feed_row(
    feed_row: Mapping[str, Any],
    identity_row: Mapping[str, Any] | None,
    *,
    identity_receipt_absent: bool = False,
    recipe_id: str = RECIPE_ID,
) -> dict[str, Any]:
    listing_id = feed_listing_id(feed_row)
    synthetic = canon_keys(listing_id)["parcel_apn"] if listing_id else None
    base = {
        "listing_id": listing_id or None,
        "recipe_id": recipe_id,
        "decision": "UNKNOWN",
        "parcel_apn": None,
        "property_key": None,
        "identity_kind": "unresolved",
        "identity_verdict": None,
        "identity_source": None,
        "parcel_provenance": None,
        "synthetic_listing_key": synthetic,
        "join_key": JOIN_KEY,
    }
    if not listing_id:
        return {
            **base,
            "reason": "UNKNOWN: daily feed row has no listing_id",
        }
    if identity_receipt_absent:
        return {
            **base,
            "reason": (
                "IDENTITY_ABSENT: no identity receipt — owner is "
                f"{IDENTITY_OWNER_REPO}/{IDENTITY_OWNER_SCRIPT}"
            ),
        }
    if identity_row is None:
        return {
            **base,
            "reason": "UNKNOWN: no identity row for listing_id (join key missed)",
        }

    verdict_raw = identity_row.get("IDENTITY_VERDICT") or identity_row.get("identity_verdict")
    apn = identity_row.get("REAL_PARCEL_APN") or identity_row.get("parcel_apn")
    verdict = _parse_verdict(verdict_raw)
    if verdict is None:
        return {
            **base,
            "identity_verdict": str(verdict_raw).strip() or None,
            "identity_source": identity_row.get("IDENTITY_RESOLVER")
            or identity_row.get("identity_source"),
            "reason": (
                "UNKNOWN: identity verdict is missing or not one the "
                "landos-engine owner already emits"
            ),
        }

    decision, reason = _decision_for_verdict(verdict, apn)
    projection = {
        **base,
        "decision": decision,
        "identity_verdict": verdict.value,
        "identity_source": identity_row.get("IDENTITY_RESOLVER")
        or identity_row.get("identity_source"),
        "parcel_provenance": identity_row.get("PARCEL_PROVENANCE")
        or identity_row.get("parcel_provenance"),
        "reason": reason,
    }
    if decision != "MATCH":
        return projection

    county_fips = identity_row.get("COUNTY_FIPS") or identity_row.get("county_fips")
    state = identity_row.get("STATE") or identity_row.get("state") or feed_row.get("state")
    portable = property_identity(county_fips=county_fips, parcel_apn=apn)
    property_key = identity_row.get("PROPERTY_KEY")
    if not property_key and state and county_fips and apn:
        property_key = f"{state}:{county_fips}:{apn}"
    projection["parcel_apn"] = str(apn).strip()
    projection["property_key"] = property_key
    projection["identity_kind"] = portable["kind"]
    return projection


def join_daily_estate_sale_feed(
    feed_rows: Sequence[Mapping[str, Any]],
    identity_rows: Sequence[Mapping[str, Any]] | None,
    *,
    recipe_id: str = RECIPE_ID,
) -> dict[str, Any]:
    """Project MATCH / NO_MATCH / UNKNOWN for each daily-feed row.

    ``identity_rows is None`` means the identity receipt itself is absent
    (IDENTITY_ABSENT). An empty list means a receipt was supplied but
    contained no rows — each listing is still UNKNOWN, not invented.
    """
    identity_receipt_absent = identity_rows is None
    indexed = index_identity_rows(identity_rows)
    projections = [
        join_feed_row(
            row,
            indexed.get(feed_listing_id(row)),
            identity_receipt_absent=identity_receipt_absent,
            recipe_id=recipe_id,
        )
        for row in feed_rows
    ]
    counts = Counter(row["decision"] for row in projections)
    return {
        "schema_version": SCHEMA_VERSION,
        "unlock": UNLOCK,
        "gap": GAP,
        "unlock_mode": UNLOCK_MODE,
        "recipe_id": recipe_id,
        "join_key": JOIN_KEY,
        "identity_owner": identity_owner(),
        "identity_receipt_absent": identity_receipt_absent,
        "feed_rows": len(projections),
        "counts": {decision: int(counts.get(decision, 0)) for decision in JOIN_DECISIONS},
        "rows": projections,
        "SUPABASE_MUTATED": "NO",
        "DEEDSONAR_MUTATED": "NO",
        "NEW_DAEMON": "NO",
        "LANDOS_CALLED": "NO",
    }
