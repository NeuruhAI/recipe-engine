"""The ranker carries the property's FACTS DATE through untouched.

landos-engine enrichment emits two per-property columns, facts_as_of and
facts_as_of_basis (observed_event_date | unknown). The DeedSonar estate ages
every row by that date, so the ranked universe must hand it on verbatim, and it
must play no part in scoring: a newer observation of the same facts is not a
better opportunity.
"""
import csv
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import rank_nc_curative_raw_v3 as v3

RANK_FIELDS = ("rank", "global_rank", "recipe_rank", "curative_priority_score",
               "priority_tier", "recipe_id", "score", "score_reasons")


def _rows(with_facts: bool, facts_date: str = "2026-09-10"):
    base = [
        {"county": "Wake", "county_fips": "37183", "parcel_apn": "0791112233",
         "owner_name": "UNKNOWN HEIRS OF A", "property_address": "1 MAIN ST",
         "owner_mailing_state": "SC", "assessed_value": "156600", "tax_balance": "12000",
         "tax_years": "2022", "years_owned": "25", "out_of_state": "True",
         "heir_indicator": "True", "fragmented_ownership_indicator": "True",
         "tax_foreclosure_indicator": "True",
         "source_types": "taxdelinquent|tax_foreclosure_case",
         "source_url": "https://example.test", "equity_status": "UNKNOWN_NEEDS_DEBT_SEARCH"},
        {"county": "Johnston", "county_fips": "37101", "parcel_apn": "15K08001",
         "owner_name": "DOE JOHN", "property_address": "9 OAK AV",
         "owner_mailing_state": "NC", "assessed_value": "80000", "tax_balance": "900",
         "tax_years": "2025", "years_owned": "3", "out_of_state": "False",
         "heir_indicator": "False", "fragmented_ownership_indicator": "False",
         "tax_foreclosure_indicator": "False", "source_types": "taxdelinquent",
         "source_url": "https://example.test", "equity_status": "UNKNOWN_NEEDS_DEBT_SEARCH"},
    ]
    if with_facts:
        base[0].update(facts_as_of=facts_date, facts_as_of_basis="observed_event_date")
        base[1].update(facts_as_of="", facts_as_of_basis="unknown")
    return base


def _rank(rows):
    with TemporaryDirectory() as tmp:
        inp, out = Path(tmp) / "in.csv", Path(tmp) / "out"
        with inp.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        v3.run(inp, out)
        with (out / "NC_CURATIVE_ALL.csv").open(newline="", encoding="utf-8-sig") as fh:
            return {r["parcel_apn"]: r for r in csv.DictReader(fh)}


def test_facts_columns_survive_ranking_verbatim():
    ranked = _rank(_rows(with_facts=True))
    assert ranked["0791112233"]["facts_as_of"] == "2026-09-10"
    assert ranked["0791112233"]["facts_as_of_basis"] == "observed_event_date"
    assert ranked["15K08001"]["facts_as_of"] == ""
    assert ranked["15K08001"]["facts_as_of_basis"] == "unknown"


def test_facts_date_never_moves_score_rank_tier_or_recipe():
    without = _rank(_rows(with_facts=False))
    older = _rank(_rows(with_facts=True, facts_date="2026-08-01"))
    newer = _rank(_rows(with_facts=True, facts_date="2026-09-10"))
    for apn in without:
        for field in RANK_FIELDS:
            assert without[apn][field] == older[apn][field] == newer[apn][field], (apn, field)


def test_a_pre_contract_universe_still_ranks():
    # Inputs without the columns (the 08-12 universe) keep working; the columns
    # are simply absent downstream and the exporter reads that as UNKNOWN.
    ranked = _rank(_rows(with_facts=False))
    assert "facts_as_of" not in ranked["0791112233"]
