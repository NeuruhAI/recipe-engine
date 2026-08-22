"""Real ``source_factory.run_receipt.v1`` -> opportunity-input adapter.

This module consumes the source factory's run receipt and its normalized JSONL
rows.  It does not infer properties from row counts: the same conservative
parcel/address identity rule is applied to every observation, resolved rows are
grouped into one input per property, and unresolved rows remain in a separate
lane.  Every normalized source row is copied verbatim beside its mapped
opportunity signal so the adaptation is replayable without the source file.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from recipe_engine.closed_loop.identity import digest

from .contracts import (
    INPUT_CONTRACT_VERSION,
    LINEAGE_LIST_FIELDS,
    PROPERTY_POPULATION_CONTRACT_VERSION,
    SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION,
    SOURCE_FACTORY_RUN_RECEIPT_VERSION,
    OpportunityContractError,
    property_identity,
    validate_input,
    validate_source_factory_rollup,
)
from .recipes import land_acquisition_recipe_v1


SOURCE_FACTORY_REQUIRED_SIGNAL_FIELDS = (
    "signal_id",
    "source",
    "source_type",
    "geography",
    "event_date",
    "raw_address",
    "parcel_apn",
    "owner_name",
    "detail_json",
    "confidence",
    "fetched_at",
)

# Only semantically exact mappings score. Other source types remain explicit,
# preserved, and unscored as ``source_factory.<source_type>`` observations.
SOURCE_TYPE_TO_OPPORTUNITY_SIGNAL = {
    "taxdelinquent": "tax_delinquent",
    "probate": "probate_or_estate",
    "vacancy": "vacancy",
    "code_violation": "code_violation",
}

CONFIDENCE_THRESHOLDS = {
    "HIGH": Decimal("0.85"),
    "MEDIUM": Decimal("0.60"),
    "LOW": Decimal("0.0000000000000000000000000001"),
}


def _nonempty(value: Any, path: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise OpportunityContractError(f"{path} must be non-empty")
    return text


def _numeric_confidence(value: Any, path: str) -> Decimal:
    if isinstance(value, bool):
        raise OpportunityContractError(f"{path} must be numeric from 0 through 1")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise OpportunityContractError(
            f"{path} must be numeric from 0 through 1"
        ) from None
    if not number.is_finite() or not Decimal("0") <= number <= Decimal("1"):
        raise OpportunityContractError(f"{path} must be numeric from 0 through 1")
    return number


def confidence_level(value: Any) -> str:
    """Map source-factory numeric confidence to the declared recipe tiers."""
    number = _numeric_confidence(value, "source_factory.signal.confidence")
    if number >= CONFIDENCE_THRESHOLDS["HIGH"]:
        return "HIGH"
    if number >= CONFIDENCE_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    if number > 0:
        return "LOW"
    return "UNKNOWN"


def _validate_receipt(receipt: Any) -> Mapping[str, Any]:
    if not isinstance(receipt, Mapping):
        raise OpportunityContractError("source_factory receipt must be an object")
    if receipt.get("schema_version") != SOURCE_FACTORY_RUN_RECEIPT_VERSION:
        raise OpportunityContractError(
            "source_factory receipt.schema_version must be "
            f"{SOURCE_FACTORY_RUN_RECEIPT_VERSION}"
        )
    _nonempty(receipt.get("run_id"), "source_factory receipt.run_id")
    _nonempty(receipt.get("factory_version"), "source_factory receipt.factory_version")
    if receipt.get("contact_authorized") is not False:
        raise OpportunityContractError(
            "source_factory receipt.contact_authorized must be exactly false"
        )
    timing = receipt.get("timing")
    if not isinstance(timing, Mapping):
        raise OpportunityContractError("source_factory receipt.timing must be an object")
    _nonempty(timing.get("finished_at"), "source_factory receipt.timing.finished_at")
    estate = receipt.get("estate")
    validate_source_factory_rollup(estate)
    _nonempty(estate.get("normalized_sha256"), "source_factory receipt.estate.normalized_sha256")
    _nonempty(estate.get("normalized_jsonl"), "source_factory receipt.estate.normalized_jsonl")
    return receipt


def _validated_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    signal_ids: set[str] = set()
    for index, original in enumerate(rows):
        path = f"source_factory.signals[{index}]"
        if not isinstance(original, Mapping):
            raise OpportunityContractError(f"{path} must be an object")
        missing = [field for field in SOURCE_FACTORY_REQUIRED_SIGNAL_FIELDS if field not in original]
        if missing:
            raise OpportunityContractError(f"{path} is missing required fields {missing}")
        row = deepcopy(dict(original))
        signal_id = _nonempty(row.get("signal_id"), f"{path}.signal_id")
        if signal_id in signal_ids:
            raise OpportunityContractError(f"duplicate source_factory signal_id {signal_id!r}")
        signal_ids.add(signal_id)
        _nonempty(row.get("source"), f"{path}.source")
        _nonempty(row.get("source_type"), f"{path}.source_type")
        _nonempty(row.get("geography"), f"{path}.geography")
        _numeric_confidence(row.get("confidence"), f"{path}.confidence")
        detail = row.get("detail_json")
        if not isinstance(detail, Mapping):
            raise OpportunityContractError(f"{path}.detail_json must be an object")
        if detail.get("contact_authorized") is not False:
            raise OpportunityContractError(
                f"{path}.detail_json.contact_authorized must be exactly false"
            )
        validated.append(row)
    return validated


def _computed_rollup(
    rows: list[Mapping[str, Any]], identities: list[Mapping[str, str]]
) -> dict[str, Any]:
    by_property: dict[str, int] = Counter(
        identity["key"] for identity in identities if identity["kind"] != "unresolved"
    )
    kinds = Counter(identity["kind"] for identity in identities)
    property_count = len(by_property)
    histogram = Counter(by_property.values())
    return {
        "landed_signal_count": len(rows),
        "property_count": property_count,
        "unresolved_signal_count": kinds.get("unresolved", 0),
        "signal_amplification": (
            round(len(rows) / property_count, 4) if property_count else 0.0
        ),
        "identity_kinds": dict(kinds),
        "multiplicity_histogram": {
            str(multiplicity): count
            for multiplicity, count in sorted(histogram.items())
        },
    }


def _assert_receipt_matches_rows(
    estate: Mapping[str, Any], computed: Mapping[str, Any]
) -> None:
    for field in (
        "landed_signal_count",
        "property_count",
        "unresolved_signal_count",
        "signal_amplification",
        "identity_kinds",
        "multiplicity_histogram",
    ):
        if estate.get(field) != computed.get(field):
            raise OpportunityContractError(
                f"source_factory receipt.estate.{field} does not match normalized signals: "
                f"receipt={estate.get(field)!r}, computed={computed.get(field)!r}"
            )


def _mapped_signal(row: Mapping[str, Any], identity: Mapping[str, str]) -> dict[str, Any]:
    source_type = str(row["source_type"])
    signal_type = SOURCE_TYPE_TO_OPPORTUNITY_SIGNAL.get(
        source_type, f"source_factory.{source_type}"
    )
    detail = row["detail_json"]
    source_family = str(
        detail.get("source_key") or row.get("source") or source_type
    ).strip()
    evidence_ref = f"source-factory-signal:{row['signal_id']}"
    return {
        "signal_id": str(row["signal_id"]),
        "signal_type": signal_type,
        "state": "PRESENT",
        "confidence": confidence_level(row["confidence"]),
        "evidence_refs": [evidence_ref],
        "source_families": [source_family],
        "observed_at": row.get("event_date") or row.get("fetched_at"),
        "property_key": identity["key"],
        "geography": row["geography"],
        "parcel_apn": row["parcel_apn"],
        "raw_address": row["raw_address"],
        "source_type": source_type,
        "source_confidence_numeric": deepcopy(row["confidence"]),
        "mapping": {
            "adapter_contract": SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION,
            "mapped": source_type in SOURCE_TYPE_TO_OPPORTUNITY_SIGNAL,
            "rule": (
                f"{source_type}->{signal_type}"
                if source_type in SOURCE_TYPE_TO_OPPORTUNITY_SIGNAL
                else "preserve as explicit unscored source_factory signal type"
            ),
        },
        "source_observation": deepcopy(dict(row)),
    }


def _unknown_readiness(recipe: Mapping[str, Any]) -> dict[str, str]:
    facts = {
        str(fact)
        for values in recipe["readiness"].values()
        for fact in values
    }
    facts.update(str(rule["fact"]) for rule in recipe["action_rules"])
    return {fact: "UNKNOWN" for fact in sorted(facts)}


def _lineage(signals: list[Mapping[str, Any]]) -> dict[str, list[str]]:
    body = {field: [] for field in LINEAGE_LIST_FIELDS}
    body["evidence_refs"] = [
        str(ref) for signal in signals for ref in signal["evidence_refs"]
    ]
    body["source_families"] = sorted({
        str(source) for signal in signals for source in signal["source_families"]
    })
    return body


def adapt_source_factory_run(
    run_receipt: Mapping[str, Any],
    normalized_signals: Iterable[Mapping[str, Any]],
    *,
    producer_commit: str,
    territory_consumer_commit: str | None = None,
) -> dict[str, Any]:
    """Adapt one complete source-factory run into per-property inputs."""
    receipt = _validate_receipt(run_receipt)
    producer_commit = _nonempty(producer_commit, "producer_commit")
    rows = _validated_rows(normalized_signals)
    identities = [
        property_identity(
            county_fips=row["geography"],
            parcel_apn=row["parcel_apn"],
            raw_address=row["raw_address"],
        )
        for row in rows
    ]
    computed = _computed_rollup(rows, identities)
    estate = receipt["estate"]
    _assert_receipt_matches_rows(estate, computed)

    grouped: dict[str, list[tuple[dict[str, Any], dict[str, str]]]] = defaultdict(list)
    unresolved: list[dict[str, Any]] = []
    raw_observation_order: list[str] = []
    for row, identity in zip(rows, identities, strict=True):
        raw_observation_order.append(str(row["signal_id"]))
        if identity["kind"] == "unresolved":
            unresolved.append(deepcopy(row))
        else:
            grouped[identity["key"]].append((row, identity))

    receipt_digest = digest(receipt)
    rollup_digest = digest(estate)
    opportunity_inputs: list[dict[str, Any]] = []
    for property_key in sorted(grouped):
        group = grouped[property_key]
        identity = group[0][1]
        mapped_signals = [_mapped_signal(row, row_identity) for row, row_identity in group]
        recipe = land_acquisition_recipe_v1()
        subject: dict[str, Any] = {
            "subject_id": f"source-factory-property:{property_key}",
            "property_key": property_key,
            "county_fips": identity["county_fips"],
        }
        if identity["kind"] == "parcel":
            subject["parcel_apn"] = identity["basis_value"]
        else:
            subject["raw_address"] = identity["basis_value"]

        provenance: dict[str, Any] = {
            "identity_method": "county_fips+normalized_parcel_apn_else_raw_address",
            "producer_contract": SOURCE_FACTORY_RUN_RECEIPT_VERSION,
            "run_id": receipt["run_id"],
            "factory_version": receipt["factory_version"],
            "producer_commit": producer_commit,
            "source_run_receipt_sha256": receipt_digest,
            "source_rollup_sha256": rollup_digest,
            "identity_component": "strategy/source_factory/identity.py",
            "source_rollup": deepcopy(dict(estate)),
        }
        if territory_consumer_commit:
            provenance["territory_compatibility"] = {
                "consumer_commit": territory_consumer_commit,
                "consumer_component": "web/lib/jurisdiction/parcelIdentity.ts",
                "identity_kinds": ["parcel", "address", "unresolved"],
            }

        population = {
            "schema_version": PROPERTY_POPULATION_CONTRACT_VERSION,
            "scope": "SUBJECT_SIGNAL_SET",
            "property_identity": deepcopy(identity),
            "signal_observation_count": len(mapped_signals),
            "property_count": 1,
            "unresolved_signal_count": 0,
            "signal_amplification": float(len(mapped_signals)),
            "identity_kinds": {identity["kind"]: len(mapped_signals)},
            "dedupe_provenance": provenance,
        }
        input_payload = {
            "schema_version": INPUT_CONTRACT_VERSION,
            "as_of": receipt["timing"]["finished_at"],
            "recipe": recipe,
            "subject": subject,
            "asset": {
                "declared_class": "unknown",
                "source": "source_factory_has_no_asset_classification",
            },
            "signals": mapped_signals,
            "property_population": population,
            "readiness": _unknown_readiness(recipe),
            "research_attempts": [],
            "lineage": _lineage(mapped_signals),
            "extensions": {
                "source_factory": {
                    "adapter_contract": SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION,
                    "run_id": receipt["run_id"],
                    "source_run_receipt_sha256": receipt_digest,
                    "raw_observations_preserved": True,
                }
            },
            "world_mode": "SOURCE_FACTORY_ADAPTER",
            "source_factory_adapter_notice": {
                "raw_signal_observation_count": len(mapped_signals),
                "deduped_property_count": 1,
                "counts_are_not_interchangeable": True,
            },
        }
        validate_input(input_payload)
        opportunity_inputs.append(input_payload)

    input_digests = [digest(payload) for payload in opportunity_inputs]
    ordered_row_digests = [digest(row) for row in rows]
    adapter_receipt_body = {
        "schema_version": SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION,
        "source_contract": SOURCE_FACTORY_RUN_RECEIPT_VERSION,
        "source_run_id": receipt["run_id"],
        "source_run_receipt_sha256": receipt_digest,
        "source_rollup_sha256": rollup_digest,
        "ordered_raw_observations_sha256": digest(ordered_row_digests),
        "opportunity_inputs_sha256": digest(input_digests),
        "signal_observation_count": len(rows),
        "property_count": len(opportunity_inputs),
        "unresolved_signal_count": len(unresolved),
    }
    return {
        "schema_version": SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION,
        "source_run_receipt": deepcopy(dict(receipt)),
        "raw_observation_order": raw_observation_order,
        "opportunity_inputs": opportunity_inputs,
        "unresolved_observations": unresolved,
        "adapter_receipt": {
            **adapter_receipt_body,
            "adapter_receipt_sha256": digest(adapter_receipt_body),
        },
    }


def verify_source_factory_adaptation(adaptation: Mapping[str, Any]) -> bool:
    """Verify receipt bindings and lossless reconstruction of source rows."""
    try:
        if adaptation.get("schema_version") != SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION:
            return False
        receipt = adaptation["source_run_receipt"]
        inputs = adaptation["opportunity_inputs"]
        unresolved = adaptation["unresolved_observations"]
        raw_by_id: dict[str, Mapping[str, Any]] = {}
        for payload in inputs:
            for signal in payload["signals"]:
                raw = signal["source_observation"]
                raw_by_id[str(raw["signal_id"])] = raw
        for raw in unresolved:
            raw_by_id[str(raw["signal_id"])] = raw
        order = list(adaptation["raw_observation_order"])
        if len(raw_by_id) != len(order) or set(raw_by_id) != set(order):
            return False
        ordered_rows = [raw_by_id[signal_id] for signal_id in order]
        adapter_receipt = adaptation["adapter_receipt"]
        body = dict(adapter_receipt)
        claimed = body.pop("adapter_receipt_sha256", None)
        expected = {
            "schema_version": SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION,
            "source_contract": SOURCE_FACTORY_RUN_RECEIPT_VERSION,
            "source_run_id": receipt["run_id"],
            "source_run_receipt_sha256": digest(receipt),
            "source_rollup_sha256": digest(receipt["estate"]),
            "ordered_raw_observations_sha256": digest(
                [digest(row) for row in ordered_rows]
            ),
            "opportunity_inputs_sha256": digest([digest(payload) for payload in inputs]),
            "signal_observation_count": len(ordered_rows),
            "property_count": len(inputs),
            "unresolved_signal_count": len(unresolved),
        }
        return body == expected and claimed == digest(expected)
    except (KeyError, TypeError, ValueError):
        return False


def _sha256_file(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def adapt_source_factory_paths(
    source_factory_root: Path,
    *,
    run_id: str,
    producer_commit: str,
    territory_consumer_commit: str | None = None,
) -> dict[str, Any]:
    """Read and adapt an on-disk source-factory run without writing artifacts."""
    root = source_factory_root.resolve()
    receipt_path = root / "data" / "source_factory" / "runs" / run_id / "run_receipt.json"
    with receipt_path.open(encoding="utf-8") as handle:
        receipt = json.load(handle)
    _validate_receipt(receipt)
    normalized_path = (root / str(receipt["estate"]["normalized_jsonl"])).resolve()
    if root != normalized_path and root not in normalized_path.parents:
        raise OpportunityContractError("normalized_jsonl escapes source_factory_root")
    observed_sha256 = _sha256_file(normalized_path)
    if observed_sha256 != receipt["estate"]["normalized_sha256"]:
        raise OpportunityContractError(
            "normalized JSONL sha256 does not match source_factory receipt.estate"
        )
    rows: list[dict[str, Any]] = []
    with normalized_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise OpportunityContractError(
                    f"normalized JSONL line {line_number} is not valid JSON"
                ) from exc
    return adapt_source_factory_run(
        receipt,
        rows,
        producer_commit=producer_commit,
        territory_consumer_commit=territory_consumer_commit,
    )
