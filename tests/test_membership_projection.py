"""Multi-membership emission: recipe_matches + DeedSonar membership projection.

unittest only (the host runs ``python3 -m unittest discover -s tests``)."""
from __future__ import annotations

import csv
import hashlib
import json
import unittest
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import project_deedsonar_receiver as proj
from scripts import rank_nc_curative_raw_v3 as v3
from scripts.rank_nc_curative_raw import (
    RECIPE_FALLTHROUGH_ID,
    RECIPE_LADDER,
    recipe,
    recipe_matches,
)


def _row(**overrides):
    row = {
        "county": "Union",
        "county_fips": "37179",
        "parcel_apn": "02.239.077",
        "owner_name": "UNKNOWN HEIRS OF EXAMPLE",
        "property_address": "1 MAIN ST",
        "owner_mailing_address": "PO BOX 1",
        "owner_mailing_state": "SC",
        "source_types": "taxdelinquent|tax_foreclosure_case",
        "tax_foreclosure_indicator": "True",
        "heir_indicator": "True",
        "fragmented_ownership_indicator": "True",
        "out_of_state": "True",
        "years_owned": "26",
        "assessed_value": "250000",
        "tax_balance": "8000",
        "tax_years": "2022",
        "equity_status": "UNKNOWN_NEEDS_DEBT_SEARCH",
        "source_url": "https://example.test",
        "assessed_value_less_tax": "242000",
        "property_key": "NC:37179:02.239.077",
        "source_row_id": "NC:37179:02.239.077",
        "candidate_id": "",
    }
    row.update(overrides)
    return row


def _fixture_rows():
    """Six rows spanning every ladder branch that current data can reach."""
    return [
        _row(),  # heir+foreclosure+fragmented -> 5 memberships
        _row(
            parcel_apn="09.100.001", owner_name="UNKNOWN HEIRS OF JANE ROE",
            fragmented_ownership_indicator="False", out_of_state="False", years_owned="6",
            assessed_value="90000", tax_balance="1500", assessed_value_less_tax="88500",
            property_key="NC:37179:09.100.001", source_row_id="NC:37179:09.100.001",
        ),  # heir+foreclosure -> stalled, heir, tf_curative
        _row(
            parcel_apn="06724316", county="Mecklenburg", county_fips="37119",
            owner_name="DOE JOHN & DOE JANE", source_types="taxdelinquent",
            tax_foreclosure_indicator="False", heir_indicator="False",
            fragmented_ownership_indicator="True", out_of_state="False", years_owned="",
            assessed_value="80000", tax_balance="900", assessed_value_less_tax="79100",
            property_key="NC:37119:06724316", source_row_id="NC:37119:06724316",
        ),  # fragmented only
        _row(
            parcel_apn="P-PLAIN", owner_name="OWNER PLAIN", source_types="taxdelinquent",
            tax_foreclosure_indicator="False", heir_indicator="False",
            fragmented_ownership_indicator="False", out_of_state="False", years_owned="",
            assessed_value="40000", tax_balance="100", assessed_value_less_tax="39900",
            property_key="NC:37179:P-PLAIN", source_row_id="NC:37179:P-PLAIN",
        ),  # fallthrough only
        _row(
            parcel_apn="P-FC", owner_name="OWNER FC LLC", source_types="tax_foreclosure_case",
            tax_foreclosure_indicator="", heir_indicator="False",
            fragmented_ownership_indicator="False", out_of_state="False", years_owned="",
            assessed_value="", tax_balance="", assessed_value_less_tax="",
            property_key="NC:37179:P-FC", source_row_id="NC:37179:P-FC",
        ),  # foreclosure only via source_types
        _row(
            parcel_apn="P-ABS", owner_name="OWNER ABSENT", source_types="taxdelinquent",
            tax_foreclosure_indicator="False", heir_indicator="False",
            fragmented_ownership_indicator="False", out_of_state="True", years_owned="30",
            assessed_value="120000", tax_balance="2500", assessed_value_less_tax="117500",
            property_key="NC:37179:P-ABS", source_row_id="NC:37179:P-ABS",
        ),  # long-term absentee only
    ]


def _write_input(path: Path, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class RecipeMatchesTests(unittest.TestCase):
    def test_ladder_order_and_ids_are_the_single_winner_ladder(self):
        self.assertEqual(
            [rid for rid, _ in RECIPE_LADDER],
            [
                "stalled_tax_foreclosure_heirship",
                "heir_property_acquisition_research",
                "tax_foreclosure_fractional_title",
                "tax_foreclosure_curative",
                "fractional_family_ownership",
                "long_term_absentee_potential_equity",
                "published_tax_distress",
            ],
        )
        self.assertEqual(RECIPE_LADDER[-1][0], RECIPE_FALLTHROUGH_ID)

    def test_first_match_equals_recipe_for_every_input_combination(self):
        seen = set()
        for heir, frag, fc_flag, fc_src, oos, yrs in product(
            ("True", "False", ""), ("True", ""), ("True", ""),
            ("tax_foreclosure_case", "taxdelinquent", ""), ("True", ""), ("25", "19", ""),
        ):
            row = {
                "heir_indicator": heir,
                "fragmented_ownership_indicator": frag,
                "tax_foreclosure_indicator": fc_flag,
                "source_types": fc_src,
                "out_of_state": oos,
                "years_owned": yrs,
            }
            matches = recipe_matches(row)
            self.assertEqual(matches[0], recipe(row)[0], row)
            self.assertEqual(len(matches), len(set(matches)), row)
            self.assertTrue(all(rid in {r for r, _ in RECIPE_LADDER} for rid in matches))
            seen.add(tuple(matches))
        # every ladder id is reachable as a primary somewhere in the table
        self.assertEqual({m[0] for m in seen}, {rid for rid, _ in RECIPE_LADDER})

    def test_fallthrough_is_never_emitted_beside_a_positive_match(self):
        self.assertEqual(recipe_matches({"source_types": "taxdelinquent"}), [RECIPE_FALLTHROUGH_ID])
        self.assertEqual(recipe_matches({}), [RECIPE_FALLTHROUGH_ID])
        for row in _fixture_rows():
            matches = recipe_matches(row)
            if RECIPE_FALLTHROUGH_ID in matches:
                self.assertEqual(matches, [RECIPE_FALLTHROUGH_ID])

    def test_conjunction_rows_keep_every_true_branch(self):
        self.assertEqual(
            recipe_matches(_row()),
            [
                "stalled_tax_foreclosure_heirship",
                "heir_property_acquisition_research",
                "tax_foreclosure_fractional_title",
                "tax_foreclosure_curative",
                "fractional_family_ownership",
                "long_term_absentee_potential_equity",
            ],
        )
        self.assertEqual(
            recipe_matches(_row(heir_indicator="False", tax_foreclosure_indicator="False", source_types="taxdelinquent", years_owned="5")),
            ["fractional_family_ownership"],
        )

    def test_missing_indicator_is_not_a_match_exactly_like_the_ladder(self):
        # truthy(None) is False in the ladder; recipe_matches inherits that, no more, no less.
        row = {"heir_indicator": None, "tax_foreclosure_indicator": None}
        self.assertEqual(recipe_matches(row), [recipe(row)[0]])

    def test_unknown_years_cannot_fire_absentee_branch(self):
        row = _row(heir_indicator="False", tax_foreclosure_indicator="False", source_types="taxdelinquent",
                   fragmented_ownership_indicator="False", out_of_state="True", years_owned="")
        self.assertEqual(recipe_matches(row), [RECIPE_FALLTHROUGH_ID])


class MembershipProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.input_csv = root / "in.csv"
        _write_input(self.input_csv, _fixture_rows())
        self.run_dir = root / "run"
        v3.run(self.input_csv, self.run_dir)
        self.ranked_csv = self.run_dir / "NC_CURATIVE_ALL.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def _project(self, out_name="proj", run_date="20260909"):
        return proj.project(
            ranked_csv=self.ranked_csv,
            input_csv=self.input_csv,
            output_dir=Path(self.tmp.name) / out_name,
            run_date=run_date,
        )

    def test_receiver_projection_keeps_the_27_column_contract(self):
        receipt = self._project()
        path = Path(receipt["receiver_projection"]["path"])
        header = path.open(encoding="utf-8").readline().rstrip("\r\n").split(",")
        self.assertEqual(header, list(proj.RECEIVER_PROJECTION_FIELDS))
        self.assertEqual(len(header), 27)
        rows = proj.load_csv(path)
        self.assertEqual(len(rows), 6)
        self.assertEqual({r["ranking_run_id"] for r in rows}, {"ranking_run_20260909_" + receipt["input_sha256"][:12]})
        self.assertTrue(all(r["property_key"] and r["source_row_id"] for r in rows))
        self.assertEqual(receipt["receiver_projection"]["PROJECTION_ROWS"], 6)
        self.assertEqual(receipt["receiver_projection"]["CANDIDATE_ID_ATTACHED"], 0)

    def test_membership_projection_contract_and_invariants(self):
        receipt = self._project()
        path = Path(receipt["membership_projection"]["path"])
        header = path.open(encoding="utf-8").readline().rstrip("\r\n").split(",")
        self.assertEqual(header, list(proj.MEMBERSHIP_PROJECTION_FIELDS))
        members = proj.load_csv(path)
        receiver = {r["property_key"]: r for r in proj.load_csv(Path(receipt["receiver_projection"]["path"]))}
        by_prop: dict[str, list[dict]] = {}
        for m in members:
            by_prop.setdefault(m["property_key"], []).append(m)
        self.assertEqual(set(by_prop), set(receiver))
        for key, rows in by_prop.items():
            primaries = [r for r in rows if r["is_primary"] == "true"]
            self.assertEqual(len(primaries), 1, key)
            primary = primaries[0]
            self.assertEqual(primary["match_rank"], "1")
            self.assertEqual(primary["recipe_id"], receiver[key]["recipe_id"])
            self.assertEqual(primary["why_matched_receipt"], receiver[key]["why_matched_receipt"])
            self.assertEqual(primary["input_hash"], receiver[key]["input_hash"])
            self.assertEqual(primary["recipe_version"], receiver[key]["recipe_version"])
            self.assertEqual(sorted(int(r["match_rank"]) for r in rows), list(range(1, len(rows) + 1)))
            self.assertEqual(len({r["recipe_id"] for r in rows}), len(rows))
            for r in rows:
                self.assertEqual(json.loads(r["why_matched_receipt"])["recipe_id"], r["recipe_id"])
                self.assertEqual(r["ranking_run_id"], receipt["ranking_run_id"])
                self.assertEqual(json.loads(r["why_matched_receipt"])["facts_used"],
                                 json.loads(primary["why_matched_receipt"])["facts_used"])
        # secondary memberships carry a DIFFERENT input_hash (it binds recipe_id) but the same facts
        secondaries = [r for r in members if r["is_primary"] == "false"]
        self.assertTrue(secondaries)
        for r in secondaries:
            self.assertNotEqual(r["input_hash"], receiver[r["property_key"]]["input_hash"])

    def test_membership_counts_for_the_fixture(self):
        receipt = self._project()
        stats = receipt["membership_projection"]
        # The v2 strict classifier reads "HEIRS" as both heir and fragmented, so
        # rows 1 and 2 hit five positive branches each (row 1 also absentee):
        # 6 + 5 + 1 + 1 + 1 + 1 memberships.
        self.assertEqual(stats["MEMBERSHIP_ROWS"], 15)
        self.assertEqual(stats["PROPERTIES"], 6)
        self.assertEqual(stats["PROPERTIES_MULTI"], 2)
        self.assertEqual(stats["MAX_MEMBERSHIPS_PER_PROPERTY"], 6)
        self.assertEqual(stats["MEMBERSHIPS_PER_PROPERTY_DISTRIBUTION"], {"1": 4, "5": 1, "6": 1})
        self.assertEqual(stats["COHORT_SIZES"], {
            "fractional_family_ownership": 3,
            "heir_property_acquisition_research": 2,
            "long_term_absentee_potential_equity": 2,
            "published_tax_distress": 1,
            "stalled_tax_foreclosure_heirship": 2,
            "tax_foreclosure_curative": 3,
            "tax_foreclosure_fractional_title": 2,
        })
        self.assertEqual(stats["SECONDARY_COHORT_SIZES"], {
            "fractional_family_ownership": 2,
            "heir_property_acquisition_research": 2,
            "long_term_absentee_potential_equity": 1,
            "tax_foreclosure_curative": 2,
            "tax_foreclosure_fractional_title": 2,
        })
        self.assertEqual(stats["PRIMARY_PARITY_WITH_RECEIVER"], "ASSERTED")
        self.assertIs(receipt["db_mutated"], False)
        self.assertIs(receipt["outbound"], False)

    def test_both_artifacts_are_deterministic_bytes(self):
        a = self._project("a")
        b = self._project("b")
        for key in ("receiver_projection", "membership_projection"):
            self.assertEqual(a[key]["sha256"], b[key]["sha256"])
            self.assertEqual(
                hashlib.sha256(Path(a[key]["path"]).read_bytes()).hexdigest(), a[key]["sha256"]
            )

    def test_run_id_is_explicit_date_plus_input_hash(self):
        self.assertEqual(proj.ranking_run_id("20260909", "d630e7333bdfc9e7" + "0" * 48),
                         "ranking_run_20260909_d630e7333bdf")
        with self.assertRaises(ValueError):
            proj.ranking_run_id("2026-09-09", "d630e7333bdfc9e7")
        a = self._project("a", "20260827")
        b = self._project("b", "20260909")
        self.assertNotEqual(a["ranking_run_id"], b["ranking_run_id"])
        # only the run id column differs between the two receiver projections
        ra = Path(a["receiver_projection"]["path"]).read_bytes().replace(b"ranking_run_20260827_", b"ranking_run_XXXXXXXX_")
        rb = Path(b["receiver_projection"]["path"]).read_bytes().replace(b"ranking_run_20260909_", b"ranking_run_XXXXXXXX_")
        self.assertEqual(ra, rb)

    def test_fails_closed_when_primary_disagrees_with_single_winner(self):
        ranked = proj.load_csv(self.ranked_csv)
        receiver_rows, _ = proj.receiver_projection_rows(ranked, None, run_id="ranking_run_20260909_x", input_hash="0" * 64)
        receiver_rows[0]["recipe_id"] = "tax_foreclosure_curative"
        with self.assertRaises(proj.ProjectionParityError):
            proj.membership_projection_rows(ranked, receiver_rows, run_id="ranking_run_20260909_x")

    def test_fails_closed_when_stored_receipt_is_not_the_engines(self):
        ranked = proj.load_csv(self.ranked_csv)
        receiver_rows, _ = proj.receiver_projection_rows(ranked, None, run_id="r", input_hash="0" * 64)
        receiver_rows[1]["why_matched_receipt"] = "{}"
        with self.assertRaises(proj.ProjectionParityError):
            proj.membership_projection_rows(ranked, receiver_rows, run_id="r")

    def test_fails_closed_on_row_order_mismatch(self):
        ranked = proj.load_csv(self.ranked_csv)
        receiver_rows, _ = proj.receiver_projection_rows(ranked, None, run_id="r", input_hash="0" * 64)
        with self.assertRaises(proj.ProjectionParityError):
            proj.membership_projection_rows(ranked, list(reversed(receiver_rows)), run_id="r")

    def test_master_join_prefers_master_identity_without_owner_name(self):
        ranked = proj.load_csv(self.ranked_csv)
        master = [
            {"county_fips": "37179", "parcel_apn": "02.239.077", "property_key": "NC:37179:02.239.077",
             "external_lead_id": "ext-1", "candidate_id": "cand-1", "owner_name": "SOMEONE ELSE"},
        ]
        rows, stats = proj.receiver_projection_rows(ranked, master, run_id="r", input_hash="0" * 64)
        joined = next(r for r in rows if r["parcel_apn"] == "02.239.077")
        self.assertEqual(joined["candidate_id"], "cand-1")
        self.assertEqual(joined["source_row_id"], "ext-1")
        self.assertEqual(stats["PROPERTY_JOINABLE"], 1)
        self.assertEqual(stats["PROPERTY_UNJOINABLE"], 5)
        self.assertEqual(stats["CANDIDATE_ID_ATTACHED"], 1)
        self.assertIs(stats["owner_name_join"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
