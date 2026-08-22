#!/usr/bin/env python3
"""Cross-repo contract court; prints one deterministic receipt and writes nothing.

Usage:
    python scripts/verify_opportunity_intelligence.py \
      --landos-root /path/to/landos-engine
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

RECIPE_ROOT = Path(__file__).resolve().parents[1]
if str(RECIPE_ROOT) not in sys.path:
    sys.path.insert(0, str(RECIPE_ROOT))

from recipe_engine.closed_loop.identity import canonical_json, digest
from recipe_engine.opportunity_intelligence import (
    INPUT_CONTRACT_VERSION,
    LAND_ACQUISITION_RECIPE_V1_DIGEST,
    PROPERTY_POPULATION_CONTRACT_VERSION,
    RECEIPT_CONTRACT_VERSION,
    RECIPE_CONTRACT_VERSION,
    RESULT_CONTRACT_VERSION,
    SCORE_VERSION,
    SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION,
    SOURCE_FACTORY_RUN_RECEIPT_VERSION,
    VERIFICATION_RECEIPT_VERSION,
    OpportunityContractError,
    adapt_source_factory_paths,
    generalization_fixtures,
    recipe_config_digest,
    validate_input,
    verify_source_factory_adaptation,
)


ACCEPTED_SOURCE_FACTORY_COMMIT = "f857bdbeaf4ce5c366e5837beab98f5767e11edf"
ACCEPTED_TERRITORY_IDENTITY_COMMIT = "b1be5d3afbf2308b565c41c057216ec11e26ddaf"
ACCEPTED_SOURCE_FACTORY_RUN_ID = "run-20260821T215734Z"


def _validator_parity_court(landos: Any) -> dict[str, Any]:
    """Prove both runtimes fail closed on the same digest-valid mutations."""
    base = next(
        fixture.payload
        for fixture in generalization_fixtures()
        if fixture.fixture_id == "t50_120_willow_cardinality_contract"
    )
    mutations: list[tuple[str, Any, bool]] = [
        (
            "unsupported_score_version",
            lambda payload: payload["recipe"].__setitem__(
                "score_version", "landos.opportunity-score.future"
            ),
            True,
        ),
        (
            "blank_signal_label",
            lambda payload: payload["recipe"]["signal_rules"][0].__setitem__(
                "label", ""
            ),
            True,
        ),
        (
            "blank_signal_category",
            lambda payload: payload["recipe"]["signal_rules"][0].__setitem__(
                "category", ""
            ),
            True,
        ),
        (
            "empty_action_rules",
            lambda payload: payload["recipe"].__setitem__("action_rules", []),
            True,
        ),
        (
            "duplicate_action_fact",
            lambda payload: payload["recipe"]["action_rules"].append(
                deepcopy(payload["recipe"]["action_rules"][0])
            ),
            True,
        ),
        (
            "blank_source_expected_information",
            lambda payload: payload["recipe"]["signal_rules"][0]["next_sources"][
                0
            ].__setitem__("expected_information", ""),
            True,
        ),
        (
            "blank_fact_source_key",
            lambda payload: payload["recipe"]["fact_sources"].__setitem__(
                "", payload["recipe"]["fact_sources"].pop(
                    next(iter(payload["recipe"]["fact_sources"]))
                )
            ),
            True,
        ),
        (
            "invalid_t50_result_state",
            lambda payload: payload["research_attempts"][0].__setitem__(
                "result_state", "FUTURE_UNRECOGNIZED_STATE"
            ),
            False,
        ),
        (
            "non_object_resolution_context",
            lambda payload: payload.__setitem__("resolution_context", []),
            False,
        ),
    ]

    passed: list[str] = []
    for name, mutate, redigest in mutations:
        payload = deepcopy(base)
        mutate(payload)
        if redigest:
            payload["recipe"]["config_digest"] = recipe_config_digest(
                payload["recipe"]
            )

        recipe_rejected = False
        try:
            validate_input(payload)
        except OpportunityContractError:
            recipe_rejected = True

        landos_rejected = False
        try:
            landos.evaluate_opportunity(payload)
        except landos.OpportunityIntelligenceError:
            landos_rejected = True

        if not recipe_rejected or not landos_rejected:
            raise AssertionError(
                f"validator parity failure {name}: "
                f"recipe_rejected={recipe_rejected} "
                f"landos_rejected={landos_rejected}"
            )
        passed.append(name)

    return {
        "status": "PASSED",
        "mutation_count": len(passed),
        "digest_valid_recipe_mutations": 7,
        "input_mutations": 2,
        "mutations": passed,
    }


def build_verification_receipt(
    landos_root: Path,
    *,
    source_factory_root: Path | None = None,
    source_factory_run_id: str = ACCEPTED_SOURCE_FACTORY_RUN_ID,
) -> dict[str, Any]:
    landos_root = landos_root.resolve()
    if not (landos_root / "app" / "opportunity_intelligence.py").is_file():
        raise SystemExit(f"not a LandOS root: {landos_root}")
    sys.path.insert(0, str(landos_root))
    from app import opportunity_intelligence as landos  # noqa: PLC0415

    expected_versions = {
        "recipe": RECIPE_CONTRACT_VERSION,
        "input": INPUT_CONTRACT_VERSION,
        "result": RESULT_CONTRACT_VERSION,
        "receipt": RECEIPT_CONTRACT_VERSION,
        "property_population": PROPERTY_POPULATION_CONTRACT_VERSION,
        "source_factory_adapter": SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION,
        "score": SCORE_VERSION,
    }
    observed_versions = {
        "recipe": landos.RECIPE_CONTRACT_VERSION,
        "input": landos.INPUT_CONTRACT_VERSION,
        "result": landos.RESULT_CONTRACT_VERSION,
        "receipt": landos.RECEIPT_CONTRACT_VERSION,
        "property_population": landos.PROPERTY_POPULATION_CONTRACT_VERSION,
        "source_factory_adapter": landos.SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION,
        "score": landos.SCORE_VERSION,
    }
    if observed_versions != expected_versions:
        raise AssertionError(
            f"cross-repo contract drift: expected={expected_versions} observed={observed_versions}"
        )

    validator_parity = _validator_parity_court(landos)

    results: dict[str, Any] = {}
    willow_lossless = False
    source_factory_contract: dict[str, Any] | None = None
    for fixture in generalization_fixtures():
        first = landos.evaluate_opportunity(fixture.payload)
        second = landos.evaluate_opportunity(fixture.payload)
        if first != second:
            raise AssertionError(f"non-deterministic evaluation: {fixture.fixture_id}")
        if not landos.verify_opportunity_receipt(first, fixture.payload):
            raise AssertionError(f"invalid receipt: {fixture.fixture_id}")
        observed = (
            first["asset_classification"]["asset_class"],
            first["opportunity_score"]["score"],
            first["next_best_action"]["action"],
        )
        expected = (
            fixture.expected_asset_class,
            fixture.expected_score,
            fixture.expected_next_action,
        )
        if observed != expected:
            raise AssertionError(
                f"fixture mismatch {fixture.fixture_id}: expected={expected} observed={observed}"
            )
        if fixture.fixture_id == "t50_120_willow_cardinality_contract":
            willow_lossless = (
                first["opportunity_genome"]["lineage"] == fixture.payload["lineage"]
                and first["research_results"]["raw_attempts"]
                == fixture.payload["research_attempts"]
            )
            if not willow_lossless:
                raise AssertionError("Willow cardinality fixture was not carried losslessly")
        if fixture.fixture_id == "source_factory_wake_23_signals_one_property":
            population = fixture.payload["property_population"]
            output_population = first["property_population"]
            source_rollup = population["dedupe_provenance"]["source_rollup"]
            lossless = (
                first["opportunity_genome"]["property_population"] == population
                and output_population == population
                and first["stacked_signals"]["raw_observations"]
                == fixture.payload["signals"]
            )
            if not lossless:
                raise AssertionError(
                    "source-factory property/cardinality provenance was not carried losslessly"
                )
            if (
                first["stacked_signals"]["observation_count"] != 23
                or output_population["property_count"] != 1
                or source_rollup["landed_signal_count"] != 14233
                or source_rollup["property_count"] != 6052
            ):
                raise AssertionError(
                    "signal observations were conflated with deduped property counts"
                )
            source_factory_contract = {
                "kind": "PROPERTY_IDENTITY_AND_CARDINALITY_COMPATIBILITY",
                "signal_bodies": "SYNTHETIC_FIXTURE",
                "producer_contract": SOURCE_FACTORY_RUN_RECEIPT_VERSION,
                "producer_commit": population["dedupe_provenance"]["producer_commit"],
                "territory_consumer_commit": population["dedupe_provenance"]
                ["territory_compatibility"]["consumer_commit"],
                "subject_signal_observation_count": output_population[
                    "signal_observation_count"
                ],
                "subject_property_count": output_population["property_count"],
                "source_run_signal_observation_count": source_rollup[
                    "landed_signal_count"
                ],
                "source_run_property_count": source_rollup["property_count"],
                "source_run_signal_amplification": source_rollup[
                    "signal_amplification"
                ],
                "source_rollup_sha256": population["dedupe_provenance"]
                ["source_rollup_sha256"],
                "counts_not_interchangeable": (
                    source_rollup["landed_signal_count"]
                    != source_rollup["property_count"]
                ),
                "lossless": lossless,
            }
        results[fixture.fixture_id] = {
            "asset_class": observed[0],
            "opportunity_score": observed[1],
            "confidence_score": first["confidence_score"]["score"],
            "next_best_action": observed[2],
            "receipt_sha256": first["receipt"]["receipt_sha256"],
        }

    if source_factory_contract is None:
        raise AssertionError("source-factory property compatibility fixture is missing")

    if source_factory_root is None:
        actual_source_factory = {
            "status": "NOT_REQUESTED",
            "run_id": source_factory_run_id,
            "how_to_run": "pass --source-factory-root to execute the protected local JSONL acceptance",
        }
    else:
        root = source_factory_root.resolve()
        observed_commit = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        if observed_commit != ACCEPTED_SOURCE_FACTORY_COMMIT:
            raise AssertionError(
                "source-factory checkout drift: "
                f"expected={ACCEPTED_SOURCE_FACTORY_COMMIT} observed={observed_commit}"
            )
        adaptation = adapt_source_factory_paths(
            root,
            run_id=source_factory_run_id,
            producer_commit=observed_commit,
            territory_consumer_commit=ACCEPTED_TERRITORY_IDENTITY_COMMIT,
        )
        if not verify_source_factory_adaptation(adaptation):
            raise AssertionError("source-factory adaptation receipt/losslessness failed")
        adapter_receipt = adaptation["adapter_receipt"]
        if (
            adapter_receipt["signal_observation_count"] != 14233
            or adapter_receipt["property_count"] != 6052
            or adapter_receipt["unresolved_signal_count"] != 0
        ):
            raise AssertionError(
                "actual source-factory cardinality acceptance is not 14233 signals / "
                "6052 properties / 0 unresolved"
            )

        evaluated_receipts: list[str] = []
        evaluated_signal_count = 0
        for opportunity_input in adaptation["opportunity_inputs"]:
            result = landos.evaluate_opportunity(opportunity_input)
            if not landos.verify_opportunity_receipt(result, opportunity_input):
                raise AssertionError(
                    f"LandOS receipt failed for {opportunity_input['subject']['subject_id']}"
                )
            if (
                result["opportunity_genome"]["raw_signals"]
                != opportunity_input["signals"]
            ):
                raise AssertionError("LandOS did not preserve adapted raw signal envelopes")
            if (
                result["caller_top_level_extensions"]
                != result["opportunity_genome"]["caller_top_level_extensions"]
            ):
                raise AssertionError("LandOS did not preserve adapter top-level extensions")
            evaluated_signal_count += result["stacked_signals"]["observation_count"]
            evaluated_receipts.append(result["receipt"]["receipt_sha256"])

        if evaluated_signal_count != 14233:
            raise AssertionError(
                f"LandOS evaluated {evaluated_signal_count} signals, expected 14233"
            )
        actual_source_factory = {
            "status": "PASSED",
            "run_id": source_factory_run_id,
            "producer_commit": observed_commit,
            "territory_identity_commit": ACCEPTED_TERRITORY_IDENTITY_COMMIT,
            "signal_observation_count": adapter_receipt["signal_observation_count"],
            "deduped_property_count": adapter_receipt["property_count"],
            "unresolved_signal_count": adapter_receipt["unresolved_signal_count"],
            "source_signal_amplification": adaptation["source_run_receipt"]["estate"]
            ["signal_amplification"],
            "landos_evaluated_property_count": len(evaluated_receipts),
            "landos_evaluated_signal_observation_count": evaluated_signal_count,
            "adapter_receipt_sha256": adapter_receipt["adapter_receipt_sha256"],
            "landos_result_receipts_sha256": digest(evaluated_receipts),
            "raw_observations_and_provenance_lossless": True,
            "counts_not_interchangeable": True,
        }

    body = {
        "schema_version": VERIFICATION_RECEIPT_VERSION,
        "contracts": expected_versions,
        "recipe_digest": LAND_ACQUISITION_RECIPE_V1_DIGEST,
        "validator_parity": validator_parity,
        "fixture_count": len(results),
        "fixture_results": results,
        "source_factory_property_identity_fixture": source_factory_contract,
        "source_factory_actual_acceptance": actual_source_factory,
        "t50_willow_contract_fixture": {
            "kind": "CARDINALITY_AND_LOSSLESSNESS_ONLY",
            "actual_case_payload": False,
            "actual_case_replay_owner": "DEEDSONAR",
            "actual_protected_payload_replay_status": "DELEGATED_TO_DEEDSONAR_NOT_RUN_HERE",
            "losslessness_claim_scope": "placeholder refs and arbitrary research payload fields",
            "lossless": willow_lossless,
            "lineage_counts": {
                key: len(value)
                for key, value in next(
                    fixture.payload["lineage"] for fixture in generalization_fixtures()
                    if fixture.fixture_id == "t50_120_willow_cardinality_contract"
                ).items()
            },
        },
    }
    return {**body, "verification_sha256": digest(body)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--landos-root", type=Path, required=True)
    parser.add_argument("--source-factory-root", type=Path)
    parser.add_argument(
        "--source-factory-run-id", default=ACCEPTED_SOURCE_FACTORY_RUN_ID
    )
    args = parser.parse_args(argv)
    print(canonical_json(build_verification_receipt(
        args.landos_root,
        source_factory_root=args.source_factory_root,
        source_factory_run_id=args.source_factory_run_id,
    )))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
