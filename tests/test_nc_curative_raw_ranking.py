from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
