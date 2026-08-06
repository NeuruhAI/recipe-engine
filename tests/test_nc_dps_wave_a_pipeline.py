from __future__ import annotations

import unittest

from recipe_engine.nc_dps_wave_a_pipeline import candidate


def packet(triggers, priority=60.0, sources=2):
    return {
        "property_id": "NC:37135:123-456",
        "scores": {"trigger_classes": triggers, "composite_priority": priority},
        "valuation": {"conservative_as_is_value": 250000, "tax_balance": 5000},
        "source_records": [
            {
                "source_url": f"https://official.example/{index}",
                "evidence_class": "verified_public_record",
                "confidence": 0.9,
            }
            for index in range(sources)
        ],
    }


class WaveARecipeTests(unittest.TestCase):
    def receipt(self):
        return {"property_id": "NC:37135:123-456", "route": "automated_enrichment_then_analyst"}

    def test_tax_distress_without_court_is_not_stalled_case(self):
        result = candidate(
            packet(["tax_distress", "absentee_or_out_of_state"]),
            self.receipt(),
        )
        self.assertEqual(result["strategy_id"], "whole_property_acquisition")
        self.assertFalse(any(result["governance"].values()))

    def test_actual_court_action_uses_stalled_case(self):
        result = candidate(
            packet(["tax_distress", "court_action"]),
            self.receipt(),
        )
        self.assertEqual(result["strategy_id"], "stalled_tax_case_resolution")

    def test_single_source_tax_row_is_monitor(self):
        result = candidate(packet(["tax_distress"], priority=20, sources=1), self.receipt())
        self.assertEqual(result["strategy_id"], "monitor")


if __name__ == "__main__":
    unittest.main(verbosity=2)
