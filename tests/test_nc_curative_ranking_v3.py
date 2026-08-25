import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import rank_nc_curative_raw_v3 as v3


def _write_input(path: Path) -> None:
    rows = [
        {
            "county": "Union",
            "county_fips": "37179",
            "parcel_apn": "02.239.077",
            "owner_name": "UNKNOWN HEIRS OF FREDDIE SOWELL",
            "property_address": "123 MAIN ST",
            "owner_mailing_address": "PO BOX 1",
            "owner_mailing_state": "SC",
            "assessed_value": "156600",
            "tax_balance": "12000",
            "tax_years": "2022",
            "years_owned": "25",
            "out_of_state": "True",
            "heir_indicator": "True",
            "fragmented_ownership_indicator": "True",
            "tax_foreclosure_indicator": "True",
            "source_types": "taxdelinquent|tax_foreclosure_case",
            "source_url": "https://example.test",
            "equity_status": "UNKNOWN_NEEDS_DEBT_SEARCH",
        },
        {
            "county": "Mecklenburg",
            "county_fips": "37119",
            "parcel_apn": "06724316",
            "owner_name": "DOE JOHN",
            "property_address": "9 OAK AV",
            "owner_mailing_address": "9 OAK AV",
            "owner_mailing_state": "NC",
            "assessed_value": "80000",
            "tax_balance": "900",
            "tax_years": "2025",
            "years_owned": "3",
            "out_of_state": "False",
            "heir_indicator": "False",
            "fragmented_ownership_indicator": "False",
            "tax_foreclosure_indicator": "False",
            "source_types": "taxdelinquent",
            "source_url": "https://example.test",
            "equity_status": "UNKNOWN_NEEDS_DEBT_SEARCH",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_family_count_collapses_parcel_roll_triggers_to_one_family():
    assert v3.families_for("heir_or_estate|long_term_ownership|high_value_property") == [
        "assessor_parcel_roll"
    ]
    assert v3.families_for(
        "tax_distress|court_action|heir_or_estate|absentee_or_out_of_state"
    ) == [
        "county_tax_collector_publication",
        "county_foreclosure_docket_publication",
        "assessor_parcel_roll",
    ]


def test_run_annotates_without_changing_rank_or_score():
    with TemporaryDirectory() as tmp:
        inp = Path(tmp) / "in.csv"
        out = Path(tmp) / "out"
        _write_input(inp)
        summary = v3.run(inp, out)

        with (out / "NC_CURATIVE_ALL.csv").open(newline="", encoding="utf-8-sig") as fh:
            ranked = list(csv.DictReader(fh))
        assert ranked[0]["parcel_apn"] == "02.239.077"
        assert ranked[0]["independent_source_family_count"] == "3"
        assert ranked[0]["scoring_version"] == v3.SCORING_VERSION
        assert ranked[0]["recipe_version"] == v3.RECIPE_VERSION
        assert ranked[1]["independent_source_family_count"] == "1"
        assert int(ranked[0]["curative_priority_score"]) > int(ranked[1]["curative_priority_score"])
        why_matched = json.loads(ranked[0]["why_matched_receipt"])
        why_ranked = json.loads(ranked[0]["why_ranked_receipt"])
        nbe = json.loads(ranked[0]["next_best_evidence_receipt"])
        assert why_matched["recipe_id"] == ranked[0]["recipe_id"]
        assert why_ranked["recipe_rank"] == int(ranked[0]["rank"])
        assert nbe["schema_version"] == "oppack.next-best-evidence.v1"

        stored = json.loads((out / "NC_CURATIVE_RECIPE_SUMMARY.json").read_text(encoding="utf-8"))
        assert stored["scoring_version"] == v3.SCORING_VERSION
        assert stored["independent_source_family_distribution"] == {"1": 1, "3": 1}
        assert stored["heir_classifier"] == "strict_v2"
        assert summary["contact_authorized"] is False
        assert summary["why_this_matched"] == "DETERMINISTIC_V1"
        assert summary["why_this_ranked"] == "DETERMINISTIC_V1"
