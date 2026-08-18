"""
source_api.py — the anchor source that actually contains the local inventory.

WHY THIS EXISTS (E2 finding, 2026-08-18): the server-rendered city page carries
exactly 20 <app-sale-row> elements, 1:1 with its 20 JSON-LD SaleEvent blocks, and
the page labels them itself — "the featured sales listed here were not chosen by
EstateSales.NET. Companies have paid extra to highlight their sale(s)". The same
paid pool is returned on every anchor, so 13 anchors deduped to 27 events and NONE
was ever in territory. The area's own listings live behind a client-hydrated region
that is still a `Loading...` placeholder in the SSR payload. in_territory=0 was a
SOURCE COVERAGE failure, never proof that the territory had no inventory.

The site's own city view loads that inventory from a public JSON endpoint:

    GET /api/sale-details?bypass=bycoordinatesanddistance:{lat}_{lng}_{miles}

robots.txt (fetched 2026-08-18, 265 bytes) disallows /account, /homepages, /v2,
/v3, /legacy and /api/user-view-details. It does NOT disallow /api/sale-details.
The runtime robots check in fetch.py is reused here rather than reimplemented, so
that rule can never drift between the two paths.

This module is a SOURCE, not a parser: it returns the same event dict shape
parse.parse_sale_events yields, so run.py's downstream (attribute -> persist ->
engine) is unchanged. Two differences, both honest upgrades:

  * every event carries the sale's OWN latitude/longitude, so geo_precision is
    'exact' instead of the city-centroid stopgap
  * organizer_phone is deliberately NOT selected. The old JSON-LD path captured it;
    this one does not collect seller-side contact data it has no authorization to use.
"""
import json
import time
from pathlib import Path

import requests

from .fetch import BASE, USER_AGENT, MIN_SPACING_SECONDS, TIMEOUT, _robots_ok

API_PATH = "/api/sale-details"

# Only fields the pipeline actually consumes. Keeping this list tight keeps the
# response small and keeps us from hoarding data we have no use for.
SELECT_FIELDS = ",".join([
    # NOTE: `typeName` is derived server-side and returned automatically —
    # asking for it explicitly makes the endpoint 500. Do not add it here.
    "id", "name", "orgName", "address", "cityName", "stateCode",
    "postalCodeNumber", "latitude", "longitude", "type",
    "isLocallyFeatured", "isRegionallyFeaturedThisWeek",
    "isNationallyFeaturedThisWeek", "isMarketplaceSale",
    "firstLocalStartDate", "lastLocalEndDate",
    "utcDateFirstPublished", "utcShowAddressAfter", "primaryMetroAreaId",
])

_last_fetch_at = 0.0


def _query_url(lat: float, lng: float, miles: int) -> str:
    return (
        f"{BASE}{API_PATH}"
        f"?bypass=bycoordinatesanddistance:{lat}_{lng}_{miles}"
        f"&select={SELECT_FIELDS}"
        f"&explicitTypes=DateTime"
    )


def _dt(value):
    """Unwrap one date field.

    `explicitTypes=DateTime` makes the endpoint return dates as
    {"_type": "DateTime", "_value": "2026-08-21T09:00:00"} rather than a bare
    string. Persist wants a scalar, so unwrap it — and still accept a plain
    string, so the adapter survives that query flag being dropped.
    """
    if isinstance(value, dict):
        return value.get("_value")
    return value or None


def _listing_url(row: dict) -> str:
    """Reconstruct the public listing URL, same shape the JSON-LD path produced."""
    st = (row.get("stateCode") or "").strip()
    city = (row.get("cityName") or "").strip()
    zipc = str(row.get("postalCodeNumber") or "").strip()
    return f"{BASE}/{st}/{city}/{zipc}/{row.get('id')}"


def to_event(row: dict):
    """One API row -> the event dict the rest of the pipeline already speaks.

    Returns None when the row has no id: the listing id is the idempotency anchor
    for recipe.sale_events, parcel_apn and owner_hash, so a row without one is
    skipped rather than given a guessed key.
    """
    listing_id = row.get("id")
    if listing_id in (None, ""):
        return None
    lat, lng = row.get("latitude"), row.get("longitude")
    return {
        "listing_id": str(listing_id),
        "url": _listing_url(row),
        "title": (row.get("name") or "").strip() or None,
        "description": None,          # not selected; payload keeps the raw row
        "organizer_name": (row.get("orgName") or "").strip() or None,
        "organizer_phone": None,      # deliberately not collected
        "start_at": _dt(row.get("firstLocalStartDate")),
        "end_at": _dt(row.get("lastLocalEndDate")),
        # The site withholds street address until utcShowAddressAfter; an empty
        # string means "not disclosed yet", which is NULL, not an address.
        "street_address": (row.get("address") or "").strip() or None,
        "city": (row.get("cityName") or "").strip() or None,
        "state": (row.get("stateCode") or "").strip() or None,
        "postal_code": str(row.get("postalCodeNumber") or "").strip() or None,
        "latitude": float(lat) if isinstance(lat, (int, float)) else None,
        "longitude": float(lng) if isinstance(lng, (int, float)) else None,
        "payload": row,               # raw API row, verbatim
    }


def fetch_anchor_events(slug: str, anchor, cache_dir: Path, today: str):
    """
    Fetch one anchor's local inventory, cache-first.
    `anchor` is (lat, lng, radius_miles).
    Returns (events, from_cache: bool, final_url: str|None).

    Cache file mirrors fetch.py's contract — {slug}_{YYYYMMDD}.json, one fetch per
    anchor per day — so a same-day rerun is cache-only on the network side and the
    gate test's second pass stays honest.
    """
    lat, lng, miles = anchor
    cache_file = cache_dir / f"{slug}_{today}.api.json"
    if cache_file.exists():
        rows = json.loads(cache_file.read_text(encoding="utf-8"))
        return [e for e in (to_event(r) for r in rows) if e], True, None

    url = _query_url(lat, lng, miles)
    if not _robots_ok(url):
        raise RuntimeError(f"robots.txt disallows {url} — refusing to fetch")

    global _last_fetch_at
    wait = MIN_SPACING_SECONDS - (time.monotonic() - _last_fetch_at)
    if wait > 0:
        time.sleep(wait)

    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    _last_fetch_at = time.monotonic()
    resp.raise_for_status()

    rows = resp.json()
    if not isinstance(rows, list):
        raise RuntimeError(
            f"{API_PATH} returned {type(rows).__name__}, expected a list — "
            "refusing to guess the shape")

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(rows), encoding="utf-8")
    return [e for e in (to_event(r) for r in rows) if e], False, resp.url
