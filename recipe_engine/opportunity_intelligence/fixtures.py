"""Named, non-live generalization fixtures for the cross-repo contract.

The Willow fixture is intentionally a *cardinality/losslessness court*, not a
copy of the real case.  Its stable placeholder refs prove that the intelligence
layer carries arbitrary T50 evidence, contacts, sources, graph structure and
quarantined identities without dropping or reclassifying them.  The actual
case replay remains in DeedSonar, where the protected evidence already lives.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from recipe_engine.closed_loop.identity import digest

from .contracts import (
    FixtureCase,
    INPUT_CONTRACT_VERSION,
    LINEAGE_LIST_FIELDS,
    PROPERTY_POPULATION_CONTRACT_VERSION,
    SOURCE_FACTORY_RUN_RECEIPT_VERSION,
    property_identity,
)
from .recipes import land_acquisition_recipe_v1


AS_OF = "2026-08-21T00:00:00Z"


def _refs(prefix: str, count: int) -> list[str]:
    return [f"fixture:{prefix}:{index:03d}" for index in range(1, count + 1)]


def _lineage(**overrides: list[str]) -> dict[str, list[str]]:
    body = {field: [] for field in LINEAGE_LIST_FIELDS}
    body.update(overrides)
    return body


def _signal(
    signal_id: str,
    signal_type: str,
    state: str,
    confidence: str,
    *,
    evidence_refs: Iterable[str] = (),
    source_families: Iterable[str] = (),
    observed_at: str = AS_OF,
    note: str = "",
) -> dict[str, Any]:
    return {
        "signal_id": signal_id,
        "signal_type": signal_type,
        "state": state,
        "confidence": confidence,
        "evidence_refs": list(evidence_refs),
        "source_families": list(source_families),
        "observed_at": observed_at,
        "note": note,
    }


def _fixture_property_population(
    subject: Mapping[str, Any], signal_count: int
) -> dict[str, Any]:
    identity = property_identity(
        county_fips=subject.get("county_fips"),
        parcel_apn=subject.get("parcel_apn"),
        raw_address=subject.get("raw_address"),
    )
    property_count = 0 if identity["kind"] == "unresolved" else 1
    return {
        "schema_version": PROPERTY_POPULATION_CONTRACT_VERSION,
        "scope": "SUBJECT_SIGNAL_SET",
        "property_identity": identity,
        "signal_observation_count": signal_count,
        "property_count": property_count,
        "unresolved_signal_count": signal_count if property_count == 0 else 0,
        "signal_amplification": (
            round(signal_count / property_count, 4) if property_count else 0.0
        ),
        "identity_kinds": {identity["kind"]: signal_count},
        "dedupe_provenance": {
            "identity_method": "county_fips+normalized_parcel_apn_else_raw_address",
            "producer_contract": PROPERTY_POPULATION_CONTRACT_VERSION,
            "producer": "SYNTHETIC_GENERALIZATION_FIXTURE",
        },
    }


def _payload(
    fixture_id: str,
    *,
    asset: Mapping[str, Any],
    signals: Iterable[Mapping[str, Any]],
    readiness: Mapping[str, str],
    research_attempts: Iterable[Mapping[str, Any]] = (),
    lineage: Mapping[str, list[str]] | None = None,
    fixture_notice: Mapping[str, Any] | None = None,
    subject: Mapping[str, Any] | None = None,
    property_population: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    signal_rows = [dict(s) for s in signals]
    subject_body = dict(subject or {
        "subject_id": f"fixture:subject:{fixture_id}",
        "county_fips": "99999",
        "parcel_apn": f"fixture-apn:{fixture_id}",
    })
    return {
        "schema_version": INPUT_CONTRACT_VERSION,
        "as_of": AS_OF,
        "world_mode": "FIXTURE",
        "fixture_id": fixture_id,
        "fixture_notice": dict(fixture_notice or {
            "kind": "GENERALIZATION_FIXTURE",
            "actual_case_payload": False,
        }),
        "recipe": land_acquisition_recipe_v1(),
        "subject": subject_body,
        "asset": dict(asset),
        "signals": signal_rows,
        "property_population": deepcopy(dict(
            property_population
            or _fixture_property_population(subject_body, len(signal_rows))
        )),
        "readiness": dict(readiness),
        "research_attempts": [dict(a) for a in research_attempts],
        "lineage": deepcopy(dict(lineage or _lineage())),
    }


def _willow_cardinality_fixture() -> FixtureCase:
    evidence = _refs("t50-evidence", 54)
    contact_evidence = _refs("t50-contact-evidence", 31)
    source_families = _refs("t50-source-family", 22)
    nodes = _refs("t50-relationship-node", 10)
    edges = _refs("t50-relationship-edge", 15)
    quarantined = _refs("t50-quarantined-identity", 7)
    payload = _payload(
        "t50_120_willow_cardinality_contract",
        asset={"declared_class": "unknown"},
        signals=[
            _signal(
                "fixture:willow:probate",
                "probate_or_estate",
                "ABSENT",
                "HIGH",
                evidence_refs=(evidence[0],),
                source_families=(source_families[0],),
                note="Fixture-only negative result; not the real Willow finding.",
            ),
        ],
        readiness={
            "identity_verified": "YES",
            "legal_authority_verified": "UNKNOWN",
            "contact_point_verified": "UNKNOWN",
            "contact_authorized": "UNKNOWN",
            "value_established": "UNKNOWN",
            "underwriting_complete": "UNKNOWN",
            "risk_review_complete": "UNKNOWN",
            "offer_authorized": "UNKNOWN",
        },
        research_attempts=[{
            "attempt_id": "fixture:willow:attempt:001",
            "source_id": "PUBLIC_PROBATE_INDEX",
            "source_family": source_families[0],
            "subject_ref": "fixture:willow:subject",
            "result_state": "NO_MATCH",
            "evidence_refs": [evidence[0]],
            "notes": "Contract-only placeholder; no real subject information.",
        }],
        lineage=_lineage(
            evidence_refs=evidence,
            contact_evidence_refs=contact_evidence,
            source_families=source_families,
            relationship_node_refs=nodes,
            relationship_edge_refs=edges,
            quarantined_identity_refs=quarantined,
        ),
        fixture_notice={
            "kind": "T50_CARDINALITY_AND_LOSSLESSNESS_ONLY",
            "actual_case_payload": False,
            "actual_case_replay_owner": "DEEDSONAR",
            "actual_protected_payload_replay_status": "DELEGATED_TO_DEEDSONAR_NOT_RUN_HERE",
            "asserted_cardinalities": {
                "evidence_refs": 54,
                "contact_evidence_refs": 31,
                "source_families": 22,
                "relationship_node_refs": 10,
                "relationship_edge_refs": 15,
                "quarantined_identity_refs": 7,
            },
        },
    )
    return FixtureCase(
        fixture_id="t50_120_willow_cardinality_contract",
        payload=payload,
        expected_asset_class="unknown",
        expected_next_action="ESTABLISH_LEGAL_AUTHORITY",
        expected_score=0,
    )


def _source_factory_property_compatibility_fixture() -> FixtureCase:
    """One Wake property with 23 observations plus the accepted estate rollup.

    The 23-row subject payload is deliberately small enough to review.  The
    full 14,233/6,052 source-run cardinalities are carried as receipt
    provenance, not represented by invented signal bodies.
    """
    identity = property_identity(
        county_fips="37183", parcel_apn="06-097-51927"
    )
    signals: list[dict[str, Any]] = []
    for observation in range(1, 24):
        signal = _signal(
            f"fixture:source-factory:wake-tax:{observation:02d}",
            "tax_delinquent",
            "PRESENT",
            "HIGH",
            evidence_refs=(f"fixture:source-factory:evidence:{observation:02d}",),
            source_families=("nc.wake.taxdelinquent.delq853",),
            note="Synthetic observation body; cardinalities/provenance mirror the accepted receipt.",
        )
        signal.update({
            "geography": "37183",
            "parcel_apn": "06-097-51927" if observation % 2 else "0609751927",
            "property_key": identity["key"],
            "source_observation_ordinal": observation,
        })
        signals.append(signal)

    source_rollup = {
        "landed_signal_count": 14233,
        "property_count": 6052,
        "unresolved_signal_count": 0,
        "signal_amplification": 2.3518,
        "identity_kinds": {"parcel": 14233},
        "identity_basis": {"parcel": 12320, "tax_account": 1913},
        "multiplicity_histogram": {
            "1": 4285, "2": 708, "3": 313, "4": 136, "5": 72,
            "6": 55, "7": 52, "8": 81, "9": 32, "10": 37,
            "11": 70, "12": 16, "13": 18, "14": 16, "15": 9,
            "16": 13, "17": 10, "18": 28, "19": 8, "20": 6,
            "21": 6, "22": 9, "23": 72,
        },
        "duplicates_dropped": 0,
        "quarantined_row_count": 15,
    }
    population = {
        "schema_version": PROPERTY_POPULATION_CONTRACT_VERSION,
        "scope": "SUBJECT_SIGNAL_SET",
        "property_identity": identity,
        "signal_observation_count": 23,
        "property_count": 1,
        "unresolved_signal_count": 0,
        "signal_amplification": 23.0,
        "identity_kinds": {"parcel": 23},
        "dedupe_provenance": {
            "identity_method": "county_fips+normalized_parcel_apn_else_raw_address",
            "producer_contract": SOURCE_FACTORY_RUN_RECEIPT_VERSION,
            "run_id": "run-20260821T215734Z",
            "factory_version": "1.0.0",
            "producer_commit": "f857bdbeaf4ce5c366e5837beab98f5767e11edf",
            "source_rollup_sha256": digest(source_rollup),
            "identity_component": "strategy/source_factory/identity.py",
            "territory_compatibility": {
                "consumer_commit": "b1be5d3afbf2308b565c41c057216ec11e26ddaf",
                "consumer_component": "web/lib/jurisdiction/parcelIdentity.ts",
                "identity_kinds": ["parcel", "address", "unresolved"],
            },
            "source_rollup": source_rollup,
        },
    }
    payload = _payload(
        "source_factory_wake_23_signals_one_property",
        subject={
            "subject_id": "fixture:source-factory:wake-property",
            "county_fips": "37183",
            "parcel_apn": "06-097-51927",
        },
        asset={"declared_class": "vacant_land", "source": "synthetic_fixture"},
        signals=signals,
        property_population=population,
        readiness={
            "identity_verified": "YES", "legal_authority_verified": "YES",
            "contact_point_verified": "YES", "contact_authorized": "YES",
            "value_established": "YES", "underwriting_complete": "YES",
            "risk_review_complete": "YES", "offer_authorized": "UNKNOWN",
        },
        fixture_notice={
            "kind": "SOURCE_FACTORY_PROPERTY_IDENTITY_COMPATIBILITY",
            "actual_case_payload": False,
            "signal_bodies": "SYNTHETIC",
            "source_run_cardinalities": "ACCEPTED_RECEIPT_PROVENANCE",
            "assertion": "14233 SIGNAL observations are 6052 PROPERTY identities, never 14233 properties",
        },
    )
    return FixtureCase(
        fixture_id="source_factory_wake_23_signals_one_property",
        payload=payload,
        expected_asset_class="vacant_land",
        expected_next_action="REQUEST_OFFER_AUTHORIZATION",
        expected_score=22,
    )


def generalization_fixtures() -> tuple[FixtureCase, ...]:
    """All six asset classes plus an upstream property-identity court."""
    willow = _willow_cardinality_fixture()
    source_factory = _source_factory_property_compatibility_fixture()

    vacant = FixtureCase(
        fixture_id="vacant_land_stacked_distress",
        payload=_payload(
            "vacant_land_stacked_distress",
            asset={"declared_class": "vacant_land", "source": "county_class_code"},
            signals=[
                _signal("v-tax", "tax_delinquent", "PRESENT", "HIGH", source_families=("COUNTY_TAX_ROLL",)),
                _signal("v-vac", "vacancy", "PRESENT", "MEDIUM", source_families=("FIELD_OBSERVATION",)),
                _signal("v-abs", "absentee_owner", "PRESENT", "HIGH", source_families=("COUNTY_PARCEL_ROLL",)),
                _signal("v-hold", "long_hold", "PRESENT", "HIGH", source_families=("RECORDED_DEED",)),
                _signal("v-value", "value_spread", "PRESENT", "HIGH", source_families=("LANDOS_VALUATION",)),
                _signal("v-title", "title_conflict", "ABSENT", "HIGH", source_families=("RECORDED_DEED",)),
                _signal("v-market", "marketability_risk", "ABSENT", "MEDIUM", source_families=("LAND_DD",)),
                _signal("v-gov", "government_owner", "ABSENT", "HIGH", source_families=("COUNTY_PARCEL_ROLL",)),
            ],
            readiness={
                "identity_verified": "YES", "legal_authority_verified": "YES",
                "contact_point_verified": "YES", "contact_authorized": "YES",
                "value_established": "YES", "underwriting_complete": "YES",
                "risk_review_complete": "YES", "offer_authorized": "UNKNOWN",
            },
        ),
        expected_asset_class="vacant_land",
        expected_next_action="REQUEST_OFFER_AUTHORIZATION",
        expected_score=76,
    )

    residential = FixtureCase(
        fixture_id="residential_estate_authority_unknown",
        payload=_payload(
            "residential_estate_authority_unknown",
            asset={"property_type": "single_family"},
            signals=[
                _signal("r-estate", "probate_or_estate", "PRESENT", "HIGH", source_families=("PROBATE_INDEX",)),
                _signal("r-abs", "absentee_owner", "PRESENT", "HIGH", source_families=("COUNTY_PARCEL_ROLL",)),
                _signal("r-hold", "long_hold", "PRESENT", "MEDIUM", source_families=("RECORDED_DEED",)),
                _signal("r-value", "value_spread", "PRESENT", "MEDIUM", source_families=("LANDOS_VALUATION",)),
                _signal("r-title", "title_conflict", "ABSENT", "MEDIUM", source_families=("RECORDED_DEED",)),
                _signal("r-risk", "marketability_risk", "ABSENT", "MEDIUM", source_families=("LAND_DD",)),
                _signal("r-gov", "government_owner", "ABSENT", "HIGH", source_families=("COUNTY_PARCEL_ROLL",)),
            ],
            readiness={
                "identity_verified": "YES", "legal_authority_verified": "UNKNOWN",
                "contact_point_verified": "YES", "contact_authorized": "UNKNOWN",
                "value_established": "YES", "underwriting_complete": "UNKNOWN",
                "risk_review_complete": "UNKNOWN", "offer_authorized": "UNKNOWN",
            },
        ),
        expected_asset_class="residential",
        expected_next_action="ESTABLISH_LEGAL_AUTHORITY",
        expected_score=60,
    )

    commercial = FixtureCase(
        fixture_id="commercial_entity_title_conflict",
        payload=_payload(
            "commercial_entity_title_conflict",
            asset={"declared_class": "commercial", "property_type": "industrial"},
            signals=[
                _signal("c-tax", "tax_delinquent", "PRESENT", "HIGH", source_families=("COUNTY_TAX_ROLL",)),
                _signal("c-value", "value_spread", "PRESENT", "MEDIUM", source_families=("LANDOS_VALUATION",)),
                _signal("c-title", "title_conflict", "PRESENT", "HIGH", source_families=("RECORDED_DEED",)),
                _signal("c-risk", "marketability_risk", "ABSENT", "MEDIUM", source_families=("LAND_DD",)),
                _signal("c-gov", "government_owner", "ABSENT", "HIGH", source_families=("COUNTY_PARCEL_ROLL",)),
            ],
            readiness={
                "identity_verified": "YES", "legal_authority_verified": "NO",
                "contact_point_verified": "YES", "contact_authorized": "UNKNOWN",
                "value_established": "YES", "underwriting_complete": "NO",
                "risk_review_complete": "NO", "offer_authorized": "NO",
            },
        ),
        expected_asset_class="commercial",
        expected_next_action="ESTABLISH_LEGAL_AUTHORITY",
        expected_score=16,
    )

    acreage = FixtureCase(
        fixture_id="acreage_economics_underwriting_open",
        payload=_payload(
            "acreage_economics_underwriting_open",
            asset={"declared_class": "acreage", "acres": 42.5},
            signals=[
                _signal("a-abs", "absentee_owner", "PRESENT", "HIGH", source_families=("COUNTY_PARCEL_ROLL",)),
                _signal("a-hold", "long_hold", "PRESENT", "HIGH", source_families=("RECORDED_DEED",)),
                _signal("a-value", "value_spread", "PRESENT", "MEDIUM", source_families=("LANDOS_VALUATION",)),
                _signal("a-title", "title_conflict", "ABSENT", "HIGH", source_families=("RECORDED_DEED",)),
                _signal("a-gov", "government_owner", "ABSENT", "HIGH", source_families=("COUNTY_PARCEL_ROLL",)),
            ],
            readiness={
                "identity_verified": "YES", "legal_authority_verified": "YES",
                "contact_point_verified": "YES", "contact_authorized": "YES",
                "value_established": "YES", "underwriting_complete": "UNKNOWN",
                "risk_review_complete": "UNKNOWN", "offer_authorized": "UNKNOWN",
            },
        ),
        expected_asset_class="acreage",
        expected_next_action="COMPLETE_UNDERWRITING",
        expected_score=42,
    )

    waterfront = FixtureCase(
        fixture_id="waterfront_risk_review_failed",
        payload=_payload(
            "waterfront_risk_review_failed",
            asset={"property_type": "waterfront"},
            signals=[
                _signal("w-value", "value_spread", "PRESENT", "MEDIUM", source_families=("LANDOS_VALUATION",)),
                _signal("w-risk", "marketability_risk", "PRESENT", "HIGH", source_families=("LAND_DD",)),
                _signal("w-title", "title_conflict", "ABSENT", "HIGH", source_families=("RECORDED_DEED",)),
                _signal("w-gov", "government_owner", "ABSENT", "HIGH", source_families=("COUNTY_PARCEL_ROLL",)),
            ],
            readiness={
                "identity_verified": "YES", "legal_authority_verified": "YES",
                "contact_point_verified": "YES", "contact_authorized": "YES",
                "value_established": "YES", "underwriting_complete": "YES",
                "risk_review_complete": "NO", "offer_authorized": "NO",
            },
        ),
        expected_asset_class="waterfront",
        expected_next_action="COMPLETE_RISK_REVIEW",
        expected_score=6,
    )

    return willow, source_factory, vacant, residential, commercial, acreage, waterfront


def fixture_by_id(fixture_id: str) -> FixtureCase:
    for fixture in generalization_fixtures():
        if fixture.fixture_id == fixture_id:
            return fixture
    raise KeyError(fixture_id)
