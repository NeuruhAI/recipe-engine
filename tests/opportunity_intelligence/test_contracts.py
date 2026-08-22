"""Recipe Engine owns the portable policy and generalization courts."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from recipe_engine.opportunity_intelligence import (
    ASSET_CLASSES,
    CANONICAL_RECIPE_ID,
    CANONICAL_RECIPE_VERSION,
    INPUT_CONTRACT_VERSION,
    LAND_ACQUISITION_RECIPE_V1_DIGEST,
    OpportunityContractError,
    PROPERTY_POPULATION_CONTRACT_VERSION,
    RECIPE_CONTRACT_VERSION,
    RESULT_CONTRACT_VERSION,
    SCORE_VERSION,
    SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION,
    SOURCE_FACTORY_RUN_RECEIPT_VERSION,
    generalization_fixtures,
    land_acquisition_recipe_v1,
    validate_input,
    validate_recipe,
)


ROOT = Path(__file__).resolve().parents[2]


def test_canonical_recipe_is_versioned_and_content_addressed():
    recipe = land_acquisition_recipe_v1()
    assert recipe["schema_version"] == RECIPE_CONTRACT_VERSION
    assert recipe["recipe_id"] == CANONICAL_RECIPE_ID
    assert recipe["version"] == CANONICAL_RECIPE_VERSION
    assert recipe["score_version"] == SCORE_VERSION
    assert recipe["config_digest"] == LAND_ACQUISITION_RECIPE_V1_DIGEST
    assert validate_recipe(recipe) is recipe


def test_a_policy_change_without_a_new_digest_is_refused():
    changed = land_acquisition_recipe_v1()
    changed["signal_rules"][0]["points_if_present"] += 1
    with pytest.raises(OpportunityContractError, match="config_digest mismatch"):
        validate_recipe(changed)


def test_every_score_rule_names_its_points_and_research_sources():
    for rule in land_acquisition_recipe_v1()["signal_rules"]:
        assert isinstance(rule["points_if_present"], int)
        assert isinstance(rule["points_if_absent"], int)
        assert rule["next_sources"], rule["signal_type"]
    assert "model" not in land_acquisition_recipe_v1()


def test_contract_schema_ids_match_runtime_versions():
    expected = {
        "opportunity_intelligence.recipe.v1.schema.json": RECIPE_CONTRACT_VERSION,
        "opportunity_intelligence.input.v1.schema.json": INPUT_CONTRACT_VERSION,
        "opportunity_intelligence.property_population.v1.schema.json": (
            PROPERTY_POPULATION_CONTRACT_VERSION
        ),
        "opportunity_intelligence.result.v1.schema.json": RESULT_CONTRACT_VERSION,
        "opportunity_intelligence.source_factory_adapter.v1.schema.json": (
            SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION
        ),
    }
    for name, contract in expected.items():
        schema = json.loads((ROOT / "contracts" / name).read_text())
        assert schema["$id"] == contract


def test_generalization_court_covers_all_six_asset_classes():
    fixtures = generalization_fixtures()
    assert {case.expected_asset_class for case in fixtures} == set(ASSET_CLASSES)
    assert len({case.fixture_id for case in fixtures}) == len(fixtures)
    for case in fixtures:
        assert validate_input(case.payload) is case.payload
        assert case.payload["world_mode"] == "FIXTURE"
        assert case.payload["fixture_notice"]["actual_case_payload"] is False


def test_willow_is_explicitly_cardinality_only_and_matches_accepted_counts():
    willow = next(case for case in generalization_fixtures()
                  if case.fixture_id == "t50_120_willow_cardinality_contract")
    notice = willow.payload["fixture_notice"]
    assert notice["kind"] == "T50_CARDINALITY_AND_LOSSLESSNESS_ONLY"
    assert notice["actual_case_replay_owner"] == "DEEDSONAR"
    assert notice["actual_protected_payload_replay_status"] == "DELEGATED_TO_DEEDSONAR_NOT_RUN_HERE"
    counts = {key: len(values) for key, values in willow.payload["lineage"].items()}
    assert counts == {
        "evidence_refs": 54,
        "contact_evidence_refs": 31,
        "source_families": 22,
        "relationship_node_refs": 10,
        "relationship_edge_refs": 15,
        "quarantined_identity_refs": 7,
    }
    assert counts == notice["asserted_cardinalities"]
    assert "address" not in willow.payload["subject"]


def test_fixture_calls_return_defensive_recipe_copies():
    fixtures = generalization_fixtures()
    first = fixtures[0].payload["recipe"]
    second = fixtures[1].payload["recipe"]
    assert first == second and first is not second


def test_source_factory_fixture_keeps_signal_and_property_counts_distinct():
    fixture = next(
        case for case in generalization_fixtures()
        if case.fixture_id == "source_factory_wake_23_signals_one_property"
    )
    population = fixture.payload["property_population"]
    provenance = population["dedupe_provenance"]
    source_rollup = provenance["source_rollup"]

    assert population["schema_version"] == PROPERTY_POPULATION_CONTRACT_VERSION
    assert population["signal_observation_count"] == len(fixture.payload["signals"]) == 23
    assert population["property_count"] == 1
    assert population["property_identity"] == {
        "kind": "parcel",
        "key": "37183|P|0609751927",
        "county_fips": "37183",
        "basis_value": "0609751927",
    }
    assert provenance["producer_contract"] == SOURCE_FACTORY_RUN_RECEIPT_VERSION
    assert provenance["producer_commit"] == "f857bdbeaf4ce5c366e5837beab98f5767e11edf"
    assert provenance["territory_compatibility"]["consumer_commit"] == (
        "b1be5d3afbf2308b565c41c057216ec11e26ddaf"
    )
    assert source_rollup["landed_signal_count"] == 14233
    assert source_rollup["property_count"] == 6052
    assert source_rollup["signal_amplification"] == 2.3518
    assert source_rollup["landed_signal_count"] != source_rollup["property_count"]


def test_population_count_mismatch_and_rollup_tamper_fail_closed():
    fixture = next(
        case for case in generalization_fixtures()
        if case.fixture_id == "source_factory_wake_23_signals_one_property"
    )
    wrong_observation_count = deepcopy(fixture.payload)
    wrong_observation_count["property_population"]["signal_observation_count"] = 14233
    with pytest.raises(OpportunityContractError, match=r"len\(input.signals\)"):
        validate_input(wrong_observation_count)

    conflated_subject_count = deepcopy(fixture.payload)
    conflated_subject_count["property_population"]["property_count"] = 23
    conflated_subject_count["property_population"]["signal_amplification"] = 1.0
    with pytest.raises(OpportunityContractError, match="must be 1 for a parcel"):
        validate_input(conflated_subject_count)

    tampered_rollup = deepcopy(fixture.payload)
    tampered_rollup["property_population"]["dedupe_provenance"]["source_rollup"][
        "quarantined_row_count"
    ] = 16
    with pytest.raises(OpportunityContractError, match="source_rollup_sha256"):
        validate_input(tampered_rollup)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("formula", "", "confidence.formula"),
        ("conflicted_credit", 1.1, "conflicted_credit"),
    ],
)
def test_confidence_policy_validation_fails_closed(field, value, match):
    changed = land_acquisition_recipe_v1()
    changed["confidence"][field] = value
    with pytest.raises(OpportunityContractError, match=match):
        validate_recipe(changed)


def test_confidence_credit_and_empty_refs_fail_closed():
    changed = land_acquisition_recipe_v1()
    changed["confidence"]["credits"]["HIGH"] = "certain"
    with pytest.raises(OpportunityContractError, match="credits.HIGH"):
        validate_recipe(changed)

    fixture = deepcopy(generalization_fixtures()[0].payload)
    fixture["signals"][0]["evidence_refs"] = [""]
    with pytest.raises(OpportunityContractError, match="evidence_refs item"):
        validate_input(fixture)

    fixture = deepcopy(generalization_fixtures()[0].payload)
    fixture["lineage"]["evidence_refs"] = [""]
    with pytest.raises(OpportunityContractError, match="lineage.evidence_refs item"):
        validate_input(fixture)


def test_runtime_requiredness_matches_input_schema():
    fixture = deepcopy(generalization_fixtures()[0].payload)
    del fixture["asset"]
    with pytest.raises(OpportunityContractError, match="input.asset is required"):
        validate_input(fixture)
