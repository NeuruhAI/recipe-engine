"""C7 court: join existing daily estate-sale feed to parcel identity.

IDENTITY_JOIN_MISSING → JOIN_EXISTING_DAILY_FEED

recipe-engine consumes landos-engine identity receipts the way
``graduate_signals.py`` already does (listing_id == SIGNAL_ID) and
projects MATCH / NO_MATCH / UNKNOWN. It does not invent a parcel
resolver, LandOS call, DeedSonar write, or Founder Runtime capability.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from recipe_engine.estate_sale_identity_join import (
    GAP,
    IDENTITY_OWNER_REPO,
    IDENTITY_OWNER_SCRIPT,
    JOIN_DECISIONS,
    JOIN_KEY,
    RECIPE_ID,
    SCHEMA_VERSION,
    UNLOCK,
    UNLOCK_MODE,
    IdentityVerdict,
    identity_owner,
    is_real_parcel_apn,
    join_daily_estate_sale_feed,
    join_feed_row,
)
from recipe_engine.parse import canon_keys

ROOT = Path(__file__).resolve().parents[1]
GRADUATION = ROOT / "data" / "graduation_runs" / "graduation-v1-20260819T104146Z.json"
RECEIPT = ROOT / "artifacts" / "C7_ESTATE_SALE_JOIN_RECEIPT.json"
RULING = ROOT / "docs" / "C7_ESTATE_SALE_JOIN.md"
JOIN_MODULE = ROOT / "recipe_engine" / "estate_sale_identity_join.py"

# Sealed 2026-08-19 daily in-territory listings already joined by graduate_signals.
SEALED_DECISIONS = {
    "4958115": "MATCH",
    "4993218": "NO_MATCH",
    "4995987": "MATCH",
    "5003424": "UNKNOWN",
    "5021610": "UNKNOWN",
    "5029542": "UNKNOWN",
    "5030895": "MATCH",
    "5038419": "MATCH",
    "5038740": "UNKNOWN",
}

FORBIDDEN_IMPORT_ROOTS = (
    "requests",
    "psycopg",
    "recipe_engine.engine",
    "recipe_engine.founder_runtime",
    "recipe_engine.opportunity_intelligence.eligibility",
    "recipe_engine.opportunity_intelligence.evaluate",
)
FORBIDDEN_NAMES = (
    "LandosEngine",
    "AddressApnResolver",
    "evaluate_recipes",
    "evaluate_eligibility",
)


def _graduation() -> dict:
    return json.loads(GRADUATION.read_text(encoding="utf-8"))


def _feed_from_graduation(body: dict) -> list[dict]:
    """Daily-feed shape graduate_signals already reads from recipe.sale_events."""
    return [
        {
            "listing_id": row["SIGNAL_ID"],
            "state": (row.get("PROPERTY_KEY") or "MI:").split(":")[0] or "MI",
        }
        for row in body["results"]
    ]


def test_sealed_daily_feed_projects_match_no_match_unknown():
    body = _graduation()
    projection = join_daily_estate_sale_feed(body["results"], body["results"])
    by_id = {row["listing_id"]: row for row in projection["rows"]}
    assert set(by_id) == set(SEALED_DECISIONS)
    for listing_id, decision in SEALED_DECISIONS.items():
        assert by_id[listing_id]["decision"] == decision, listing_id
        assert by_id[listing_id]["decision"] in JOIN_DECISIONS
    assert projection["counts"] == {"MATCH": 4, "NO_MATCH": 1, "UNKNOWN": 4}
    assert projection["unlock_mode"] == UNLOCK_MODE
    assert projection["gap"] == GAP
    assert projection["LANDOS_CALLED"] == "NO"


def test_match_rows_carry_real_parcel_not_listing_sentinel():
    body = _graduation()
    projection = join_daily_estate_sale_feed(_feed_from_graduation(body), body["results"])
    for row in projection["rows"]:
        synthetic = canon_keys(row["listing_id"])["parcel_apn"]
        assert row["synthetic_listing_key"] == synthetic
        if row["decision"] == "MATCH":
            assert is_real_parcel_apn(row["parcel_apn"])
            assert row["parcel_apn"] != synthetic
            assert row["identity_kind"] == "parcel"
            assert row["property_key"]
        else:
            assert row["parcel_apn"] is None
            assert row["identity_kind"] == "unresolved"


def test_identity_receipt_absent_is_unknown_not_fabricated_match():
    feed = [{"listing_id": "4958115"}, {"listing_id": "4993218"}]
    projection = join_daily_estate_sale_feed(feed, None)
    assert projection["identity_receipt_absent"] is True
    assert projection["counts"] == {"MATCH": 0, "NO_MATCH": 0, "UNKNOWN": 2}
    for row in projection["rows"]:
        assert row["decision"] == "UNKNOWN"
        assert row["parcel_apn"] is None
        assert IDENTITY_OWNER_REPO in row["reason"]
        assert IDENTITY_OWNER_SCRIPT in row["reason"]


def test_missing_identity_row_and_empty_listing_stay_unknown():
    projection = join_daily_estate_sale_feed(
        [{"listing_id": "5040000"}, {"listing_id": ""}],
        [{"SIGNAL_ID": "4958115", "IDENTITY_VERDICT": "RESOLVED",
          "REAL_PARCEL_APN": "11-12-5220-0128-00-0"}],
    )
    assert [row["decision"] for row in projection["rows"]] == ["UNKNOWN", "UNKNOWN"]
    assert projection["rows"][0]["reason"].startswith("UNKNOWN: no identity row")
    assert projection["rows"][1]["reason"].startswith("UNKNOWN: daily feed row has no listing_id")


def test_listing_sentinel_and_catch_all_pin_never_match():
    assert is_real_parcel_apn("estate-4958115") is False
    assert is_real_parcel_apn("99-56-9999-9999-99-9") is False
    assert is_real_parcel_apn("11-12-5220-0128-00-0") is True
    synthetic = join_feed_row(
        {"listing_id": "4958115"},
        {"SIGNAL_ID": "4958115", "IDENTITY_VERDICT": "RESOLVED",
         "REAL_PARCEL_APN": "estate-4958115"},
    )
    sentinel = join_feed_row(
        {"listing_id": "4993218"},
        {"SIGNAL_ID": "4993218", "IDENTITY_VERDICT": "RESOLVED",
         "REAL_PARCEL_APN": "99-56-9999-9999-99-9"},
    )
    assert synthetic["decision"] == "UNKNOWN"
    assert sentinel["decision"] == "UNKNOWN"
    assert synthetic["parcel_apn"] is None
    assert sentinel["parcel_apn"] is None


def test_owner_verdicts_map_without_guessing():
    expected = {
        IdentityVerdict.RESOLVED: "MATCH",
        IdentityVerdict.SENTINEL_PARCEL_REJECTED: "NO_MATCH",
        IdentityVerdict.NO_PARCEL_AT_POINT: "NO_MATCH",
        IdentityVerdict.AMBIGUOUS_MULTIPLE_PARCELS: "UNKNOWN",
        IdentityVerdict.NO_RESOLVER_FOR_COUNTY: "UNKNOWN",
        IdentityVerdict.RESOLVER_UNAVAILABLE: "UNKNOWN",
    }
    apn_for = {
        IdentityVerdict.RESOLVED: "11-12-5220-0128-00-0",
    }
    for verdict, decision in expected.items():
        row = join_feed_row(
            {"listing_id": "1", "state": "MI"},
            {
                "SIGNAL_ID": "1",
                "IDENTITY_VERDICT": verdict.value,
                "REAL_PARCEL_APN": apn_for.get(verdict),
                "COUNTY_FIPS": "26021",
                "STATE": "MI",
                "IDENTITY_RESOLVER": "berrien_parcels_spatial_intersect",
            },
        )
        assert row["decision"] == decision, verdict
        assert row["decision"] in JOIN_DECISIONS


def test_unknown_owner_verdict_is_unknown():
    row = join_feed_row(
        {"listing_id": "1"},
        {"SIGNAL_ID": "1", "IDENTITY_VERDICT": "INVENTED_VERDICT",
         "REAL_PARCEL_APN": "11-12-5220-0128-00-0"},
    )
    assert row["decision"] == "UNKNOWN"
    assert row["parcel_apn"] is None


def test_identity_owner_is_landos_engine_not_this_repo():
    owner = identity_owner()
    assert owner["repo"] == "NeuruhAI/landos-engine"
    assert owner["in_recipe_engine"] is False
    assert owner["script"] == IDENTITY_OWNER_SCRIPT
    assert owner["consumer_seam"] == "scripts/graduate_signals.py"
    assert JOIN_KEY == "listing_id == SIGNAL_ID"
    assert RECIPE_ID == "estate_sale_property"


def test_join_module_does_not_invent_landos_or_runtime_calls():
    tree = ast.parse(JOIN_MODULE.read_text(encoding="utf-8"), filename="estate_sale_identity_join.py")
    imported: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] if "." in alias.name else alias.name
                            for alias in node.names)
            imported.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            names.update(alias.name for alias in node.names)
        if isinstance(node, ast.Name):
            names.add(node.id)
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
    for root in FORBIDDEN_IMPORT_ROOTS:
        assert root not in imported, root
    for name in FORBIDDEN_NAMES:
        assert name not in names, name
    text = JOIN_MODULE.read_text(encoding="utf-8")
    assert "LANDOS_ENGINE_URL" not in text
    assert "/evaluate" not in text


def test_receipt_and_ruling_seal_the_gap_closure():
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    ruling = RULING.read_text(encoding="utf-8")
    assert receipt["schema_version"] == SCHEMA_VERSION
    assert receipt["SUCCESS_CRITERION"] == "IDENTITY_JOIN_MISSING→JOIN_EXISTING_DAILY_FEED"
    assert receipt["unlock"] == UNLOCK
    assert receipt["unlock_mode"] == UNLOCK_MODE
    assert receipt["gap"] == GAP
    assert receipt["identity_owner"]["repo"] == IDENTITY_OWNER_REPO
    assert receipt["identity_owner"]["in_recipe_engine"] is False
    assert receipt["counts"] == {"MATCH": 4, "NO_MATCH": 1, "UNKNOWN": 4}
    assert receipt["SUPABASE_MUTATED"] == "NO"
    assert receipt["DEEDSONAR_MUTATED"] == "NO"
    assert receipt["NEW_DAEMON"] == "NO"
    assert receipt["LANDOS_CALLED"] == "NO"
    assert receipt["MERGE"] == "NO"
    assert "IDENTITY_JOIN_MISSING" in ruling
    assert "JOIN_EXISTING_DAILY_FEED" in ruling
    assert IDENTITY_OWNER_REPO in ruling
    assert "estate-{listing_id}" in ruling
