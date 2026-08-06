from __future__ import annotations

import unittest

from recipe_engine.nc_dps_wave_a_pipeline import candidate


class WaveARecipeTests(unittest.TestCase):
    def test_tax_distress_candidate_validates_and_has_no_execution_power(self):
        packet = {
            "property_id": "NC:37135:123-456",
            "scores": {"trigger_classes": ["tax_distress", "absentee_or_out_of_state"], "composite_priority": 60.0},
            "valuation": {"conservative_as_is_value": 250000, "tax_balance": 5000},
            "source_records": [{"source_url": "https://official.example", "evidence_class": "verified_public_record", "confidence": 0.9}],
        }
        receipt = {"property_id": packet["property_id"], "route": "automated_enrichment_then_analyst"}
        result = candidate(packet, receipt)
        self.assertEqual(result["strategy_id"], "whole_property_acquisition")
        self.assertTrue(result["eligible"])
        self.assertFalse(any(result["governance"].values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
