from __future__ import annotations

import unittest

from recipe_engine.nc_dps_wave_a_pipeline import candidate


def packet(triggers, priority=60.0, source_count=1):
    return {
        "property_id": "NC:37135:123-456",
        "scores": {
            "trigger_classes": list(triggers),
            "composite_priority": priority,
        },
        "valuation": {
            "conservative_as_is_value": 250000,
            "tax_balance": 5000,
        },
        "source_records": [
            {
                "source_url": f"https://official.example/{i}",
                "evidence_class": "verified_public_record",
                "confidence": 0.9,
            }
            for i in range(source_count)
        ],
    }


def receipt(p):
    return {
        "property_id": p["property_id"],
        "route": "automated_enrichment_then_analyst",
    }


class WaveARecipeTests(unittest.TestCase):
    def test_actual_court_action_uses_stalled_case(self):
        p = packet(["tax_distress", "court_action"], source_count=2)
        result = candidate(p, receipt(p))
        self.assertEqual(result["strategy_id"], "stalled_tax_case_resolution")
        self.assertFalse(any(result["governance"].values()))

    def test_single_source_tax_row_is_monitor(self):
        p = packet(["tax_distress"], source_count=1)
        result = candidate(p, receipt(p))
        self.assertEqual(result["strategy_id"], "monitor")
        self.assertFalse(any(result["governance"].values()))

    def test_tax_distress_without_court_is_not_stalled_case(self):
        p = packet(["tax_distress", "absentee_or_out_of_state"], source_count=2)
        result = candidate(p, receipt(p))
        self.assertNotEqual(result["strategy_id"], "stalled_tax_case_resolution")
        self.assertEqual(result["strategy_id"], "whole_property_acquisition")


if __name__ == "__main__":
    unittest.main(verbosity=2)
