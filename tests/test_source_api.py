"""
test_source_api.py — the JSON source adapter that replaced the paid-carousel scrape.

The event contract these assert is the one run.py already consumed from
parse.parse_sale_events; if it drifts, the downstream (attribute -> persist ->
engine) drifts silently, which is exactly how in_territory=0 went unnoticed.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recipe_engine.cities import ANCHOR_CITIES, ANCHOR_QUERIES, attribute_county
from recipe_engine.parse import parse_sale_events
from recipe_engine.source_api import fetch_anchor_events, to_event, _query_url


def row(**over):
    base = {
        "id": 5038419,
        "name": "ZILKE ESTATE SALE",
        "orgName": "Zilke Liquidations",
        "address": "",
        "cityName": "Baroda",
        "stateCode": "MI",
        "postalCodeNumber": 49101,
        "latitude": 41.9534,
        "longitude": -86.4853,
        "firstLocalStartDate": "2026-08-21T09:00:00",
        "lastLocalEndDate": "2026-08-23T15:00:00",
    }
    base.update(over)
    return base


# ---- contract with the rest of the pipeline --------------------------------

def test_event_shape_matches_the_json_ld_parser():
    """Every key run.py reads must exist, so the source is a drop-in."""
    html = (
        '<script type="application/ld+json">'
        '{"@type":"SaleEvent","url":"https://www.estatesales.net/MI/Baroda/49101/1",'
        '"name":"x","location":{"address":{"streetAddress":"1 A St",'
        '"addressLocality":"Baroda","addressRegion":"MI","postalCode":"49101"}}}'
        "</script>"
    )
    legacy = next(parse_sale_events(html))
    api = to_event(row())
    assert set(legacy).issubset(set(api)), set(legacy) - set(api)


def test_carries_the_sales_own_coordinates():
    ev = to_event(row())
    assert ev["latitude"] == pytest.approx(41.9534)
    assert ev["longitude"] == pytest.approx(-86.4853)


def test_a_row_without_an_id_is_skipped_not_guessed():
    assert to_event(row(id=None)) is None
    assert to_event(row(id="")) is None


def test_withheld_address_is_null_not_empty_string():
    """The site hides the street until utcShowAddressAfter; "" is not an address."""
    assert to_event(row(address=""))["street_address"] is None
    assert to_event(row(address="  "))["street_address"] is None


def test_seller_phone_is_never_collected():
    ev = to_event(row(phoneNumbers=["574-555-0100"], showPhoneNumber=True))
    assert ev["organizer_phone"] is None


def test_payload_keeps_the_raw_row_verbatim():
    r = row()
    assert to_event(r)["payload"] == r


# ---- territory --------------------------------------------------------------

def test_a_real_in_territory_row_attributes_to_its_county():
    ev = to_event(row())
    slug, in_terr = attribute_county(ev["state"], ev["postal_code"], ev["city"])
    assert (slug, in_terr) == ("berrien-mi", True)


def test_out_of_market_row_stays_report_only():
    ev = to_event(row(cityName="Downers Grove", stateCode="IL", postalCodeNumber=60516))
    _, in_terr = attribute_county(ev["state"], ev["postal_code"], ev["city"])
    assert in_terr is False


# ---- anchors + query --------------------------------------------------------

def test_every_anchor_has_query_geometry():
    assert set(ANCHOR_QUERIES) == set(ANCHOR_CITIES)


def test_query_url_targets_the_allowed_api_path():
    url = _query_url(41.6764, -86.2520, 25)
    assert "/api/sale-details?" in url
    assert "bycoordinatesanddistance:41.6764_-86.252_25" in url
    # robots.txt disallows /api/user-view-details specifically; never that one.
    assert "user-view-details" not in url


# ---- cache ------------------------------------------------------------------

def test_same_day_rerun_is_cache_only(tmp_path):
    (tmp_path / "south-bend_20260818.api.json").write_text(json.dumps([row()]))
    events, from_cache, final_url = fetch_anchor_events(
        "south-bend", ANCHOR_QUERIES["south-bend"], tmp_path, "20260818")
    assert from_cache is True
    assert final_url is None
    assert [e["listing_id"] for e in events] == ["5038419"]


def test_cached_rows_without_ids_are_dropped(tmp_path):
    (tmp_path / "a_20260818.api.json").write_text(json.dumps([row(), {"name": "no id"}]))
    events, _, _ = fetch_anchor_events("a", (0, 0, 25), tmp_path, "20260818")
    assert len(events) == 1


# ---- explicitTypes=DateTime wrapping ---------------------------------------

def test_wrapped_datetimes_are_unwrapped_to_scalars():
    """explicitTypes=DateTime returns {_type,_value}; persist needs the scalar."""
    ev = to_event(row(
        firstLocalStartDate={"_type": "DateTime", "_value": "2026-08-21T09:00:00"},
        lastLocalEndDate={"_type": "DateTime", "_value": "2026-08-23T15:00:00"},
    ))
    assert ev["start_at"] == "2026-08-21T09:00:00"
    assert ev["end_at"] == "2026-08-23T15:00:00"


def test_plain_string_dates_still_work():
    ev = to_event(row(firstLocalStartDate="2026-08-21T09:00:00"))
    assert ev["start_at"] == "2026-08-21T09:00:00"


def test_missing_dates_are_none():
    ev = to_event(row(firstLocalStartDate=None, lastLocalEndDate=None))
    assert ev["start_at"] is None and ev["end_at"] is None
