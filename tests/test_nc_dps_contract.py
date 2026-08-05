from __future__ import annotations

import ast
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "recipe_engine/nc_dps_contract.py"
SCHEMA_PATH = (
    ROOT / "contracts/nc_dps_recipe_candidate.v1.schema.json"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "nc_dps_recipe_contract_test_module",
        MODULE_PATH,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            "Could not load nc_dps_contract.py"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NCDPSRecipeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()
        cls.schema = json.loads(
            SCHEMA_PATH.read_text()
        )

    def candidate(self):
        return {
            "schema_version": self.module.CONTRACT_VERSION,
            "candidate_id": "candidate:nc:37179:123",
            "property_id": "property:nc:37179:123",
            "strategy_id": "title_cure_and_resale",
            "eligible": True,
            "disqualifiers": [],
            "economics": {
                "acquisition_cost": 75000,
                "title_cure_cost": 15000,
                "legal_cost": 7500,
                "carrying_cost": 5000,
                "disposition_cost": 18000,
                "gross_exit_value": 225000,
                "expected_net_value": 104500,
                "risk_adjusted_value": 72000,
            },
            "execution_plan": {
                "action_summary": (
                    "Complete title diligence and compare cure paths."
                ),
                "dependencies": [
                    "human_counsel_review",
                    "verified_ownership_authority",
                ],
                "reversible": True,
                "legal_review_required": True,
                "human_approval_required": True,
            },
            "evidence": {
                "property_packet_version": (
                    "nc_dps_property_packet.v1"
                ),
                "property_packet_sha256": "a" * 64,
                "score_receipt_sha256": "b" * 64,
                "source_record_count": 8,
                "contradictions": [],
            },
            "governance": {
                field: False
                for field in self.module.HARD_STOP_FIELDS
            },
        }

    def test_schema_strategy_enum_matches_runtime_contract(self):
        schema_values = tuple(
            self.schema["properties"]["strategy_id"]["enum"]
        )

        self.assertEqual(
            schema_values,
            self.module.STRATEGIES,
        )

    def test_required_strategy_families_exist(self):
        expected = {
            "whole_property_acquisition",
            "fractional_interest_acquisition",
            "heir_property_jv",
            "title_cure_and_resale",
            "stalled_tax_case_resolution",
            "tax_foreclosure_sale_bid",
            "surplus_funds_recovery",
            "monitor",
            "reject",
        }

        self.assertEqual(
            set(self.module.STRATEGIES),
            expected,
        )

    def test_schema_hard_stops_are_false(self):
        governance = self.schema["properties"][
            "governance"
        ]["properties"]

        self.assertEqual(
            set(governance),
            set(self.module.HARD_STOP_FIELDS),
        )

        for field in self.module.HARD_STOP_FIELDS:
            self.assertIs(
                governance[field]["const"],
                False,
            )

    def test_valid_analytical_candidate_passes(self):
        self.assertIsNone(
            self.module.validate_p0_candidate(
                self.candidate()
            )
        )

    def test_execution_power_is_rejected(self):
        candidate = self.candidate()
        candidate["governance"]["payment_authorized"] = True

        with self.assertRaises(ValueError):
            self.module.validate_p0_candidate(
                candidate
            )

    def test_missing_top_level_field_is_rejected(self):
        candidate = self.candidate()
        del candidate["economics"]

        with self.assertRaises(ValueError):
            self.module.validate_p0_candidate(
                candidate
            )

    def test_unknown_top_level_field_is_rejected(self):
        candidate = self.candidate()
        candidate["secret_execution_flag"] = False

        with self.assertRaises(ValueError):
            self.module.validate_p0_candidate(
                candidate
            )

    def test_malformed_evidence_hash_is_rejected(self):
        candidate = self.candidate()
        candidate["evidence"]["property_packet_sha256"] = (
            "not-a-hash"
        )

        with self.assertRaises(ValueError):
            self.module.validate_p0_candidate(
                candidate
            )

    def test_negative_acquisition_cost_is_rejected(self):
        candidate = self.candidate()
        candidate["economics"]["acquisition_cost"] = -1

        with self.assertRaises(ValueError):
            self.module.validate_p0_candidate(
                candidate
            )

    def test_duplicate_dependencies_are_rejected(self):
        candidate = self.candidate()
        candidate["execution_plan"]["dependencies"] = [
            "human_counsel_review",
            "human_counsel_review",
        ]

        with self.assertRaises(ValueError):
            self.module.validate_p0_candidate(
                candidate
            )

    def test_human_approval_must_be_true(self):
        candidate = self.candidate()
        candidate["execution_plan"][
            "human_approval_required"
        ] = False

        with self.assertRaises(ValueError):
            self.module.validate_p0_candidate(
                candidate
            )

    def test_legal_review_must_be_true(self):
        candidate = self.candidate()
        candidate["execution_plan"][
            "legal_review_required"
        ] = False

        with self.assertRaises(ValueError):
            self.module.validate_p0_candidate(
                candidate
            )

    def test_unknown_strategy_is_rejected(self):
        candidate = self.candidate()
        candidate["strategy_id"] = "invented_strategy"

        with self.assertRaises(ValueError):
            self.module.validate_p0_candidate(
                candidate
            )

    def test_zero_source_records_are_rejected(self):
        candidate = self.candidate()
        candidate["evidence"]["source_record_count"] = 0

        with self.assertRaises(ValueError):
            self.module.validate_p0_candidate(
                candidate
            )

    def test_contract_module_has_no_live_io_imports(self):
        tree = ast.parse(
            MODULE_PATH.read_text()
        )

        forbidden = {
            "psycopg",
            "requests",
            "httpx",
            "urllib",
            "socket",
            "subprocess",
        }

        found = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(
                    alias.name.split(".")[0]
                    for alias in node.names
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
            ):
                found.add(
                    node.module.split(".")[0]
                )

        self.assertFalse(
            found & forbidden,
            f"forbidden imports: {sorted(found & forbidden)}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
