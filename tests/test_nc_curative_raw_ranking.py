from __future__ import annotations

import random
import unittest

from scripts.rank_nc_curative_raw import base_score, rank_rows, recipe


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
