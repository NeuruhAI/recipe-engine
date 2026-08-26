from __future__ import annotations

import random
import unittest

from scripts.rank_nc_curative_raw import base_score, deedsonar_rows, rank_rows, recipe


class CurativeRawRankingTests(unittest.TestCase):
    def row(self, **overrides):
        row = {
            "county": "Union",
            "county_fips": "37179",
            "parcel_apn": "02.239.077",
            "owner_name": "UNKNOWN HEIRS OF EXAMPLE",
            "source_types": "taxdelinquent|tax_foreclosure_case",
            "tax_foreclosure_indicator": "True",
            "heir_indicator": "True",
            "fragmented_ownership_indicator": "True",
            "out_of_state": "True",
            "years_owned": "26",
            "assessed_value": "250000",
            "tax_balance": "8000",
            "assessed_value_less_tax": "242000",
            "equity_status": "UNKNOWN_NEEDS_DEBT_SEARCH",
        }
        row.update(overrides)
        return row

    def test_heir_foreclosure_routes_to_curative_recipe(self):
        rid, lane = recipe(self.row())
        self.assertEqual(rid, "stalled_tax_foreclosure_heirship")
        self.assertEqual(lane, "TITLE_CURATIVE_HEIR")

    def test_score_rewards_requested_dimensions_without_claiming_equity(self):
        score, reasons = base_score(self.row())
        self.assertGreaterEqual(score, 80)
        self.assertIn("out_of_state_owner:+5", reasons)
        self.assertIn("ownership_20_plus_years:+8", reasons)
        self.assertIn("active_tax_foreclosure:+25", reasons)
        self.assertIn("heir_or_estate_signal:+20", reasons)

    def test_high_signal_property_ranks_above_plain_delinquency(self):
        plain = self.row(
            parcel_apn="P2",
            owner_name="EXAMPLE OWNER",
            source_types="taxdelinquent",
            tax_foreclosure_indicator="False",
            heir_indicator="False",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="3",
            assessed_value="80000",
            tax_balance="500",
            assessed_value_less_tax="79500",
        )
        ranked = rank_rows([plain, self.row()])
        self.assertEqual(ranked[0]["parcel_apn"], "02.239.077")
        self.assertEqual(ranked[0]["verified_equity"], "")
        self.assertFalse(ranked[0]["contact_authorized"])

    def test_known_zero_not_equal_unknown_for_tie_break(self):
        known_zero = self.row(
            parcel_apn="K0",
            owner_name="OWNER KNOWN ZERO",
            source_types="taxdelinquent",
            tax_foreclosure_indicator="False",
            heir_indicator="False",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="",
            assessed_value="0",
            tax_balance="",
            assessed_value_less_tax="",
        )
        unknown = self.row(
            parcel_apn="KU",
            owner_name="OWNER UNKNOWN",
            source_types="taxdelinquent",
            tax_foreclosure_indicator="False",
            heir_indicator="False",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="",
            assessed_value="",
            tax_balance="",
            assessed_value_less_tax="",
        )
        ranked = rank_rows([unknown, known_zero])
        self.assertEqual(ranked[0]["parcel_apn"], "K0")
        self.assertEqual(ranked[1]["parcel_apn"], "KU")

    def test_known_negative_zero_positive_numeric_tiebreak_semantics(self):
        high = self.row(
            parcel_apn="S-HIGH",
            source_types="taxdelinquent",
            tax_foreclosure_indicator="False",
            heir_indicator="False",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="",
            assessed_value="0",
            tax_balance="",
            assessed_value_less_tax="10",
        )
        zero = self.row(
            parcel_apn="S-ZERO",
            source_types="taxdelinquent",
            tax_foreclosure_indicator="False",
            heir_indicator="False",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="",
            assessed_value="0",
            tax_balance="",
            assessed_value_less_tax="0",
        )
        negative = self.row(
            parcel_apn="S-NEG",
            source_types="taxdelinquent",
            tax_foreclosure_indicator="False",
            heir_indicator="False",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="",
            assessed_value="0",
            tax_balance="",
            assessed_value_less_tax="-5",
        )
        ranked = rank_rows([zero, negative, high])
        self.assertEqual(
            [r["parcel_apn"] for r in ranked],
            ["S-HIGH", "S-ZERO", "S-NEG"],
        )

    def test_unknown_tiebreak_is_deterministic_across_reordered_inputs(self):
        a = self.row(
            parcel_apn="A-UNK",
            source_types="taxdelinquent",
            tax_foreclosure_indicator="False",
            heir_indicator="False",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="",
            assessed_value="",
            tax_balance="",
            assessed_value_less_tax="",
        )
        z = self.row(
            parcel_apn="Z-UNK",
            source_types="taxdelinquent",
            tax_foreclosure_indicator="False",
            heir_indicator="False",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="",
            assessed_value="",
            tax_balance="",
            assessed_value_less_tax="",
        )
        first = [r["parcel_apn"] for r in rank_rows([z, a])]
        second = [r["parcel_apn"] for r in rank_rows([a, z])]
        shuffled_inputs = [a, z]
        random.shuffle(shuffled_inputs)
        third = [r["parcel_apn"] for r in rank_rows(shuffled_inputs)]
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertEqual(first, ["A-UNK", "Z-UNK"])

    def test_global_rank_aliases_rank_and_recipe_rank_is_per_recipe(self):
        stalled_a = self.row(parcel_apn="STALLED-A")
        stalled_b = self.row(
            parcel_apn="STALLED-B",
            assessed_value="75000",
            tax_balance="1000",
            assessed_value_less_tax="74000",
            years_owned="5",
            out_of_state="False",
            fragmented_ownership_indicator="False",
        )
        published_a = self.row(
            parcel_apn="PUB-A",
            owner_name="DOE JOHN",
            source_types="taxdelinquent",
            tax_foreclosure_indicator="False",
            heir_indicator="False",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="3",
            assessed_value="80000",
            tax_balance="500",
            assessed_value_less_tax="79500",
        )
        published_b = self.row(
            parcel_apn="PUB-B",
            owner_name="ROE JANE",
            source_types="taxdelinquent",
            tax_foreclosure_indicator="False",
            heir_indicator="False",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="2",
            assessed_value="40000",
            tax_balance="100",
            assessed_value_less_tax="39900",
        )
        ranked = rank_rows([published_b, stalled_b, published_a, stalled_a])
        by_parcel = {row["parcel_apn"]: row for row in ranked}

        self.assertEqual(
            [row["parcel_apn"] for row in ranked],
            ["STALLED-A", "STALLED-B", "PUB-A", "PUB-B"],
        )
        for row in ranked:
            self.assertEqual(row["rank"], row["global_rank"])

        self.assertEqual(by_parcel["STALLED-A"]["recipe_id"], "stalled_tax_foreclosure_heirship")
        self.assertEqual(by_parcel["STALLED-B"]["recipe_id"], "stalled_tax_foreclosure_heirship")
        self.assertEqual(by_parcel["PUB-A"]["recipe_id"], "published_tax_distress")
        self.assertEqual(by_parcel["PUB-B"]["recipe_id"], "published_tax_distress")

        self.assertEqual(by_parcel["STALLED-A"]["global_rank"], 1)
        self.assertEqual(by_parcel["STALLED-A"]["recipe_rank"], 1)
        self.assertEqual(by_parcel["STALLED-B"]["global_rank"], 2)
        self.assertEqual(by_parcel["STALLED-B"]["recipe_rank"], 2)
        self.assertEqual(by_parcel["PUB-A"]["global_rank"], 3)
        self.assertEqual(by_parcel["PUB-A"]["recipe_rank"], 1)
        self.assertEqual(by_parcel["PUB-B"]["global_rank"], 4)
        self.assertEqual(by_parcel["PUB-B"]["recipe_rank"], 2)

        self.assertNotEqual(by_parcel["PUB-A"]["global_rank"], by_parcel["PUB-A"]["recipe_rank"])
        self.assertNotEqual(by_parcel["PUB-B"]["global_rank"], by_parcel["PUB-B"]["recipe_rank"])

        exported = deedsonar_rows(ranked)
        self.assertEqual(exported[0]["global_rank"], 1)
        self.assertEqual(exported[2]["recipe_rank"], 1)
        self.assertEqual(exported[2]["global_rank"], 3)
        self.assertIn("published_tax_distress:+10", exported[2]["score_reasons"])
        self.assertIn("why_matched_receipt", exported[0])
        self.assertIn("why_ranked_receipt", exported[0])
        self.assertIn("next_best_evidence_receipt", exported[0])
        self.assertIn("scoring_version", exported[0])
        self.assertIn("recipe_version", exported[0])
        self.assertEqual(by_parcel["STALLED-A"]["recipe_membership_count"], 2)
        self.assertEqual(by_parcel["PUB-A"]["recipe_membership_count"], 2)
        self.assertEqual(by_parcel["STALLED-A"]["ranking_universe_id"], by_parcel["PUB-B"]["ranking_universe_id"])
        self.assertEqual(by_parcel["STALLED-A"]["score_semantics"], "DETERMINISTIC_ADDITIVE_CURATIVE_PRIORITY")
        self.assertTrue(exported[0]["ranking_universe_id"])
        self.assertEqual(exported[0]["recipe_membership_count"], 2)
        self.assertEqual(exported[0]["score_semantics"], "DETERMINISTIC_ADDITIVE_CURATIVE_PRIORITY")

    def test_interleaved_global_rank_keeps_per_recipe_positions(self):
        """GLOBAL 1A 2B 3A 4B 5A => recipe A 1,2,3 and recipe B 1,2."""
        # Keep each row at 2 triggers so multi_trigger_3_plus does not collapse A/B.
        # A = heir_property (no taxdelinquent). B = published_tax_distress (no 150k).
        a1 = self.row(
            parcel_apn="A1",
            owner_name="HEIRS OF A1",
            source_types="",
            tax_foreclosure_indicator="False",
            heir_indicator="True",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="3",
            assessed_value="250000",
            tax_balance="12000",
            assessed_value_less_tax="238000",
        )
        b1 = self.row(
            parcel_apn="B1",
            owner_name="OWNER B1",
            source_types="taxdelinquent",
            tax_foreclosure_indicator="False",
            heir_indicator="False",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="26",
            assessed_value="80000",
            tax_balance="12000",
            assessed_value_less_tax="68000",
        )
        a2 = self.row(
            parcel_apn="A2",
            owner_name="HEIRS OF A2",
            source_types="",
            tax_foreclosure_indicator="False",
            heir_indicator="True",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="3",
            assessed_value="150000",
            tax_balance="500",
            assessed_value_less_tax="149500",
        )
        b2 = self.row(
            parcel_apn="B2",
            owner_name="OWNER B2",
            source_types="taxdelinquent",
            tax_foreclosure_indicator="False",
            heir_indicator="False",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="26",
            assessed_value="80000",
            tax_balance="500",
            assessed_value_less_tax="79500",
        )
        a3 = self.row(
            parcel_apn="A3",
            owner_name="HEIRS OF A3",
            source_types="",
            tax_foreclosure_indicator="False",
            heir_indicator="True",
            fragmented_ownership_indicator="False",
            out_of_state="False",
            years_owned="3",
            assessed_value="40000",
            tax_balance="100",
            assessed_value_less_tax="39900",
        )
        ranked = rank_rows([b2, a3, b1, a1, a2])
        parcels = [row["parcel_apn"] for row in ranked]
        recipes = [row["recipe_id"] for row in ranked]
        self.assertEqual(parcels, ["A1", "B1", "A2", "B2", "A3"])
        self.assertEqual(
            recipes,
            [
                "heir_property_acquisition_research",
                "published_tax_distress",
                "heir_property_acquisition_research",
                "published_tax_distress",
                "heir_property_acquisition_research",
            ],
        )
        self.assertEqual([row["global_rank"] for row in ranked], [1, 2, 3, 4, 5])
        self.assertEqual([row["rank"] for row in ranked], [1, 2, 3, 4, 5])
        self.assertEqual([row["recipe_rank"] for row in ranked], [1, 1, 2, 2, 3])
        self.assertEqual(
            [row["recipe_membership_count"] for row in ranked],
            [3, 2, 3, 2, 3],
        )
        self.assertEqual(len({row["ranking_universe_id"] for row in ranked}), 1)
        self.assertIn("heir_or_estate_signal:+20", ranked[0]["score_reasons"])
        self.assertIn("published_tax_distress:+10", ranked[1]["score_reasons"])
        self.assertTrue(all(row["verified_equity"] == "" for row in ranked))
        self.assertTrue(all("verified_equity" not in (row["score_reasons"] or "") for row in ranked))


if __name__ == "__main__":
    unittest.main(verbosity=2)
