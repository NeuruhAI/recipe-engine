"""Courts for the real source-factory receipt/row adapter."""

from __future__ import annotations

from copy import deepcopy

import pytest

from recipe_engine.opportunity_intelligence import (
    SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION,
    SOURCE_FACTORY_RUN_RECEIPT_VERSION,
    OpportunityContractError,
    adapt_source_factory_run,
    verify_source_factory_adaptation,
)


def source_row(
    signal_id: str,
    *,
    apn: str = "",
    address: str = "",
    source_type: str = "taxdelinquent",
    confidence: float = 0.91,
) -> dict:
    return {
        "signal_id": signal_id,
        "source": "county.example.gov",
        "source_type": source_type,
        "geography": "37183",
        "event_date": "2026-08-01",
        "raw_address": address,
        "parcel_apn": apn,
        "owner_name": "FIXTURE OWNER",
        "detail_json": {
            "source_key": "fixture.county.source",
            "contact_authorized": False,
            "custom_provenance": {"keep": [signal_id]},
        },
        "confidence": confidence,
        "fetched_at": "2026-08-21T21:57:54Z",
    }


def source_run() -> tuple[dict, list[dict]]:
    rows = [
        source_row("signal-1", apn="06-097-51927", confidence=0.93),
        source_row(
            "signal-2",
            apn="0609751927",
            source_type="tax_foreclosure_case",
            confidence=0.62,
        ),
        source_row("signal-unresolved", confidence=0.0),
    ]
    receipt = {
        "schema_version": SOURCE_FACTORY_RUN_RECEIPT_VERSION,
        "run_id": "run-fixture",
        "factory_version": "1.0.0",
        "timing": {
            "started_at": "2026-08-21T21:57:34Z",
            "finished_at": "2026-08-21T21:57:54Z",
        },
        "estate": {
            "normalized_jsonl": "fixture/normalized_signals.jsonl",
            "normalized_sha256": "0" * 64,
            "landed_signal_count": 3,
            "property_count": 1,
            "signal_amplification": 3.0,
            "unresolved_signal_count": 1,
            "identity_kinds": {"parcel": 2, "unresolved": 1},
            "identity_basis": {"parcel": 2, "unknown": 1},
            "multiplicity_histogram": {"2": 1},
            "duplicates_dropped": 0,
        },
        "contact_authorized": False,
    }
    return receipt, rows


def adapt(receipt: dict, rows: list[dict]) -> dict:
    return adapt_source_factory_run(
        receipt,
        rows,
        producer_commit="f857bdbeaf4ce5c366e5837beab98f5767e11edf",
        territory_consumer_commit="b1be5d3afbf2308b565c41c057216ec11e26ddaf",
    )


def test_adapter_builds_one_input_per_property_and_separates_unresolved():
    receipt, rows = source_run()
    adaptation = adapt(receipt, rows)

    assert adaptation["schema_version"] == SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION
    assert len(adaptation["opportunity_inputs"]) == 1
    assert adaptation["unresolved_observations"] == [rows[2]]
    assert adaptation["raw_observation_order"] == [row["signal_id"] for row in rows]
    assert verify_source_factory_adaptation(adaptation)

    opportunity = adaptation["opportunity_inputs"][0]
    assert opportunity["property_population"]["signal_observation_count"] == 2
    assert opportunity["property_population"]["property_count"] == 1
    assert opportunity["property_population"]["property_identity"]["key"] == (
        "37183|P|0609751927"
    )
    assert opportunity["signals"][0]["confidence"] == "HIGH"
    assert opportunity["signals"][1]["confidence"] == "MEDIUM"
    assert opportunity["signals"][0]["signal_type"] == "tax_delinquent"
    assert opportunity["signals"][1]["signal_type"] == "pre_foreclosure"
    assert opportunity["signals"][1]["mapping"]["join_key_state"] == (
        "AUTHORIZED_SOURCE_TYPE_MAPPING"
    )
    assert opportunity["signals"][0]["source_observation"] == rows[0]
    assert opportunity["signals"][1]["source_observation"] == rows[1]
    assert opportunity["world_mode"] == "SOURCE_FACTORY_ADAPTER"
    assert opportunity["source_factory_adapter_notice"] == {
        "raw_signal_observation_count": 2,
        "deduped_property_count": 1,
        "counts_are_not_interchangeable": True,
    }


def test_adapter_receipt_detects_mapped_input_or_raw_observation_tamper():
    receipt, rows = source_run()
    adaptation = adapt(receipt, rows)
    adaptation["opportunity_inputs"][0]["signals"][0]["state"] = "ABSENT"
    assert not verify_source_factory_adaptation(adaptation)

    adaptation = adapt(receipt, rows)
    adaptation["unresolved_observations"][0]["owner_name"] = "CHANGED"
    assert not verify_source_factory_adaptation(adaptation)


def test_tax_sale_preserved_unscored_until_join_authority():
    receipt, rows = source_run()
    rows.insert(
        2,
        source_row(
            "signal-sale",
            apn="0609751927",
            source_type="taxsale",
            confidence=0.55,
        ),
    )
    receipt["estate"]["landed_signal_count"] = 4
    receipt["estate"]["property_count"] = 1
    receipt["estate"]["signal_amplification"] = 4.0
    receipt["estate"]["unresolved_signal_count"] = 1
    receipt["estate"]["identity_kinds"] = {"parcel": 3, "unresolved": 1}
    receipt["estate"]["multiplicity_histogram"] = {"3": 1}

    adaptation = adapt(receipt, rows)
    mapped = {
        signal["signal_id"]: signal
        for signal in adaptation["opportunity_inputs"][0]["signals"]
    }
    assert mapped["signal-sale"]["signal_type"] == "source_factory.taxsale"
    assert mapped["signal-sale"]["mapping"]["mapped"] is False
    assert mapped["signal-sale"]["mapping"]["join_key_state"] == (
        "JOIN_KEY_AUTHORITY_BLOCKED"
    )


def test_adapter_rejects_receipt_cardinality_drift_and_bad_numeric_confidence():
    receipt, rows = source_run()
    wrong_count = deepcopy(receipt)
    wrong_count["estate"]["property_count"] = 3
    wrong_count["estate"]["signal_amplification"] = 1.0
    wrong_count["estate"]["multiplicity_histogram"] = {"1": 3}
    with pytest.raises(OpportunityContractError, match="source_factory.receipt.estate"):
        adapt(wrong_count, rows)

    bad_confidence = deepcopy(rows)
    bad_confidence[0]["confidence"] = "HIGH"
    with pytest.raises(OpportunityContractError, match="numeric from 0 through 1"):
        adapt(receipt, bad_confidence)


def test_adapter_rejects_contact_authorization_in_source_observation():
    receipt, rows = source_run()
    rows[0]["detail_json"]["contact_authorized"] = True
    with pytest.raises(OpportunityContractError, match="contact_authorized"):
        adapt(receipt, rows)
