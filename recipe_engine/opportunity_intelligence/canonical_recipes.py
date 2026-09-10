"""The canonical 30 residential opportunity recipes, as compilable source.

This module is SOURCE, not a new catalog.  Every recipe, requirement and gate
below is transcribed from artifacts that already exist, and each row says which:

* the canonical-30 thesis matrix (lead-recipe-os ``artifacts/CANONICAL_30_RECIPE_MATRIX.tsv``);
* the live DeedSonar recipe registry WHEN clauses (``property_recipe_registry.v1``);
* the GENOME-B ingredient registry (``OPPORTUNITY_INGREDIENT_REGISTRY.tsv`` and
  ``FABLE_INGREDIENT_RECIPE_MATRIX.tsv``) for ingredient ids and source states;
* the process-clock registry for clock ids and states;
* this repository's ``RECIPE_SOURCE_COMPATIBILITY.csv`` / ``RECIPE_NBE_COVERAGE.csv``
  for negative-evidence decisiveness and next-best-evidence questions.

Requirement roles are semantics, not labels:

``REQUIRED``    membership predicate.  The recipe's thesis.  Decides MATCH /
                NO_MATCH / UNKNOWN through ``eligibility.derive_decision``.
``NEGATIVE``    defeater.  Participates in membership only once it is FOUND
                (bound evidence); an unobserved defeater is the normal state of
                the world and never blocks a match.
``QUALIFYING``  evidence needed before an operator may act (authority, stall,
                title state, value).  Never changes membership.
``DISQUALIFIER`` hard stop for qualification (e.g. government owner).  Never
                changes membership: a disqualified property is still a member.
``AMPLIFIER``   optional evidence.  Feeds the score vector only.

Membership predicates follow the registry WHEN clauses unless the canonical
thesis says otherwise; every divergence is written in the recipe's ``notes``.
Thresholds that no artifact defines are ``UNDEFINED`` -- never invented.
"""

from __future__ import annotations

from typing import Any

CANONICAL_RECIPE_SPEC_CONTRACT = "neuruh.recipe-engine.canonical-recipe-spec.v1"
CANONICAL_RECIPE_SPEC_VERSION = "1.0.0"

#: Where every row below came from.  sha256 is of the file bytes at that commit.
SPEC_PROVENANCE: dict[str, dict[str, str]] = {
    "canonical_30_matrix": {
        "repo": "NeuruhAI/lead-recipe-os",
        "commit": "40d48d4d022d4aa9db8fb5e4360266fed4f2a1ef",
        "path": "artifacts/CANONICAL_30_RECIPE_MATRIX.tsv",
        "sha256": "a52965b8d90eaebb1c602388679222ea748a8ee74b62d38e25779841248e46de",
    },
    "ingredient_recipe_matrix": {
        "repo": "NeuruhAI/lead-recipe-os",
        "commit": "40d48d4d022d4aa9db8fb5e4360266fed4f2a1ef",
        "path": "artifacts/FABLE_INGREDIENT_RECIPE_MATRIX.tsv",
        "sha256": "bd80b004f9962ff2d29e51982033b7a76a553b2cc77e14b6cfa1e43839dd59b8",
    },
    "genome_ingredient_registry": {
        "repo": "NeuruhAI/lead-recipe-os",
        "commit": "40d48d4d022d4aa9db8fb5e4360266fed4f2a1ef",
        "path": "artifacts/OPPORTUNITY_INGREDIENT_REGISTRY.tsv",
        "sha256": "6cf2b1df34eb8799c22fb26a6d8f72086ccb994efb4c385ecf44bc686f55f861",
    },
    "process_clock_registry": {
        "repo": "NeuruhAI/lead-recipe-os",
        "commit": "40d48d4d022d4aa9db8fb5e4360266fed4f2a1ef",
        "path": "artifacts/PROCESS_CLOCK_REGISTRY.tsv",
        "sha256": "5a7544b19e215e8eea31eeda6b1a5c02ecb38ffe2f05f23531cefe4678ecf5af",
    },
    "property_recipe_registry": {
        "repo": "NeuruhAI/deedsonar",
        "commit": "b4ca64cab27e78d2307cae409eadb94e9538255d",
        "path": "src/lib/property-intelligence/recipes/registry.ts",
        "sha256": "edd7d8556acb45ae16c7a68502be5fb7fa0ebc397d6734e4d1ad1d66b5edf543",
    },
    "property_signal_registry": {
        "repo": "NeuruhAI/deedsonar",
        "commit": "b4ca64cab27e78d2307cae409eadb94e9538255d",
        "path": "src/lib/property-intelligence/signals/registry.ts",
        "sha256": "d19d53201d5086486be4bbba1671393ceea061851ea0ade14fa12d585d6eaccc",
    },
    "recipe_source_compatibility": {
        "repo": "NeuruhAI/recipe-engine",
        "commit": "6e2fd039f886a0112bca413160d37035fc8868eb",
        "path": "RECIPE_SOURCE_COMPATIBILITY.csv",
        "sha256": "34fbb5f356a14dee3e3925c0e89f1bb91e7695cf9d2e53c6858df1099d48b312",
    },
    "recipe_nbe_coverage": {
        "repo": "NeuruhAI/recipe-engine",
        "commit": "6e2fd039f886a0112bca413160d37035fc8868eb",
        "path": "RECIPE_NBE_COVERAGE.csv",
        "sha256": "decb616ae86f1294459d9ec53de97394eb93403ebdea44ff2841812446054896",
    },
    "recipe_score_vector_coverage": {
        "repo": "NeuruhAI/recipe-engine",
        "commit": "6e2fd039f886a0112bca413160d37035fc8868eb",
        "path": "RECIPE_SCORE_VECTOR_COVERAGE.csv",
        "sha256": "60119dbe8e0adcd30ffeabd06fbbc460a420d92802f820a8372e8afc679a49cd",
    },
}

# --------------------------------------------------------------------------
# ingredients
# --------------------------------------------------------------------------

#: ``source_state`` is repository evidence (registry / GENOME-B), not aspiration.
#: AVAILABLE and PARTIAL have a producer somewhere; the rest do not.
PRODUCER_SOURCE_STATES = ("AVAILABLE", "PARTIAL")
NO_PRODUCER_SOURCE_STATES = ("SOURCE_REQUIRED", "WALLED", "PROPOSED", "MISSING")
SOURCE_STATES = PRODUCER_SOURCE_STATES + NO_PRODUCER_SOURCE_STATES
VALUE_KINDS = ("BOOLEAN", "NUMBER", "DATE", "CATEGORY")
MEASUREMENT_BASES = ("VERIFIED", "ESTIMATED", "PROXY")


def _ingredient(
    genome_id: str,
    value_kind: str,
    source_state: str,
    unlock_family: str,
    question: str,
    *,
    engine_signal_type: str | None = None,
    max_basis: str = "VERIFIED",
    producer: str = "",
    note: str = "",
) -> dict[str, Any]:
    return {
        "genome_ingredient_id": genome_id,
        "value_kind": value_kind,
        "source_state": source_state,
        "unlock_family": unlock_family,
        "nbe_question": question,
        "engine_signal_type": engine_signal_type,
        "max_basis": max_basis,
        "producer": producer,
        "note": note,
    }


#: Ingredient vocabulary.  Keys are DeedSonar ``signals/registry.ts`` ids or the
#: ``signal_id_in_registry`` column of the GENOME-B registry; ``clock.*`` ids are
#: process-clock state (GENOME-B I-CLK-01) named after the recipe-engine clock
#: names in RECIPE_PROCESS_CLOCK_COVERAGE.csv.  ``engine_signal_type`` is the ONLY
#: authorized crosswalk into the eligibility vocabulary (source_factory
#: SOURCE_TYPE_TO_OPPORTUNITY_SIGNAL + the owned scoring recipe); it is
#: one-to-one on purpose so lowering never widens a predicate.
INGREDIENTS: dict[str, dict[str, Any]] = {
    # tax / forced sale
    "tax.delinquent": _ingredient(
        "I-TAX-01", "BOOLEAN", "PARTIAL", "TAX", "Is the parcel on a current published delinquency list?",
        engine_signal_type="tax_delinquent",
        producer="landos-ingest nc-weekly (taxdelinquent); deedsonar tax_delinquent_amount",
    ),
    "tax.delinquent_years": _ingredient(
        "I-TAX-01", "NUMBER", "PARTIAL", "TAX_YEARS_THRESHOLD", "How many tax years are due?",
        producer="deedsonar tax_years_due_count (not on the estate query)",
    ),
    "tax.delinquent_amount": _ingredient(
        "I-TAX-01", "NUMBER", "PARTIAL", "TAX", "What is the current arrears amount?",
        producer="deedsonar tax_delinquent_amount",
    ),
    "tax.foreclosed": _ingredient(
        "I-FC-01", "BOOLEAN", "PARTIAL", "TAX", "Is there a tax foreclosure case on the parcel?",
        engine_signal_type="pre_foreclosure",
        producer="landos-ingest tax_foreclosure_case (source_factory tax_foreclosure_case->pre_foreclosure)",
    ),
    "tax_sale.sale_date": _ingredient(
        "I-FC-02", "DATE", "PARTIAL", "TAX_SALE_CALENDAR",
        "What is the scheduled tax sale date from an authorized source?",
        producer="landos-engine tax_sale_info normalizer (dates parsed, not persisted)",
        note="taxsale/tax_sale source types are JOIN_KEY_AUTHORITY_BLOCKED in source_factory",
    ),
    "tax_sale.redemption_deadline": _ingredient(
        "I-FC-02", "DATE", "PARTIAL", "TAX_SALE_CALENDAR",
        "When does the redemption window expire?",
        producer="landos-engine tax_sale_info normalizer (dates parsed, not persisted)",
    ),
    # succession / estate
    "probate.opened": _ingredient(
        "I-SUC-01", "BOOLEAN", "PARTIAL", "PROBATE_DEPTH",
        "Is there an official probate case linking this parcel and owner?",
        engine_signal_type="probate_or_estate",
        producer="landos-ingest probate_heir flag",
        note="public index no-match is negative evidence, not the opposite legal fact",
    ),
    "probate.closed": _ingredient(
        "I-SUC-01", "BOOLEAN", "SOURCE_REQUIRED", "PROBATE_DEPTH", "Has the estate been closed?",
        note="case status is not persisted anywhere today",
    ),
    "estate.open": _ingredient(
        "I-OID-02", "BOOLEAN", "PARTIAL", "OWNER_ROLL", "Is the owner of record an open estate (HEIRS/ESTATE OF)?",
        producer="owner_condition HEIRS_ESTATE / ESTATE_OPEN",
    ),
    "estate.authority_letters": _ingredient(
        "I-PTY-01", "BOOLEAN", "SOURCE_REQUIRED", "PROBATE_DEPTH",
        "Who holds letters / legal authority for the estate?",
        note="estate_source SOURCE_REQUIRED (RECIPE_NBE_COVERAGE heir_property_acquisition_research)",
    ),
    "death.verified": _ingredient(
        "I-SUC-02", "BOOLEAN", "SOURCE_REQUIRED", "VERIFIED_DEATH",
        "Is there a lawfully sourced verified death event for the owner of record?",
        note="Obituary Signal Parser exists only as a name; manual lane first",
    ),
    "estate_sale.event": _ingredient(
        "I-SUC-03", "BOOLEAN", "PARTIAL", "ESTATE_SALE_JOIN",
        "Is there an estate-sale event joined to this parcel?",
        producer="recipe-engine sale_events daily + estate_sale_identity_join (C7)",
    ),
    "heirship.unresolved": _ingredient(
        "I-SUC-04", "BOOLEAN", "PARTIAL", "PROBATE_DEPTH", "Is heirship fragmented / unresolved?",
        producer="heir_indicator on the NC estate input",
        note="must come from source evidence, never from primary_recipe",
    ),
    "ownership.trust_or_estate": _ingredient(
        "I-OID-02", "BOOLEAN", "PARTIAL", "OWNER_ROLL", "Is the owner of record a trust or an estate?",
        producer="landos-ingest owner-name classifier",
    ),
    "ownership.government": _ingredient(
        "I-OID-02", "BOOLEAN", "AVAILABLE", "OWNER_ROLL", "Is the owner of record a government body?",
        engine_signal_type="government_owner",
        producer="owner_condition GOVERNMENT",
    ),
    # title / entity
    "title.fractional": _ingredient(
        "I-TTL-01", "BOOLEAN", "AVAILABLE", "TITLE", "Is title held in fractional / co-tenant interests?",
        producer="fragmented_ownership_indicator on the NC estate input",
    ),
    "title.defect": _ingredient(
        "I-TTL-02", "BOOLEAN", "WALLED", "TITLE", "Is there a recorded title or deed defect?",
        note="recorded instruments WALLED (gap.deed.recorded_instruments)",
    ),
    "entity.on_title": _ingredient(
        "I-OID-02", "BOOLEAN", "PARTIAL", "OWNER_ROLL", "Is an entity the owner of record?",
        producer="landos-ingest owner-name classifier (LLC/CORP)",
    ),
    "entity.sos_status": _ingredient(
        "I-ENT-01", "CATEGORY", "SOURCE_REQUIRED", "SOS_ENTITY", "What is the entity's Secretary of State status?",
        note="FL Sunbiz free bulk; NC partial; IN/MI/TX/TN paid or prohibited",
    ),
    # owner geography / occupancy / condition
    "absentee.out_of_state": _ingredient(
        "I-OID-04", "BOOLEAN", "PARTIAL", "OWNER_ROLL", "Does the owner mailing address differ from situs (out of state)?",
        engine_signal_type="absentee_owner",
        producer="deedsonar mailing_mismatch + out_of_state_owner_flag",
        note="must derive from address evidence, not inference",
    ),
    "vacancy.vacant": _ingredient(
        "I-VAC-01", "BOOLEAN", "PARTIAL", "CODE_VACANCY", "Is vacancy evidenced by an official source?",
        engine_signal_type="vacancy",
        producer="vacancy tag on NC estate (not queryable); Berrien vacant public parcels",
        note="vacancy must be evidential, never inferred from mailing mismatch",
    ),
    "code.violation": _ingredient(
        "I-CODE-01", "BOOLEAN", "PARTIAL", "CODE_VACANCY", "Is there an open code violation?",
        engine_signal_type="code_violation",
        producer="code tag on NC estate (list import)",
    ),
    "code.demolition_order": _ingredient(
        "I-CODE-01", "BOOLEAN", "PARTIAL", "CODE_VACANCY", "Is there a demolition / unsafe-building order, and is it current?",
        producer="South Bend ArcGIS demolition layer (stale 2025-04-18)",
    ),
    "storm.footprint": _ingredient(
        "I-DIS-01", "BOOLEAN", "PROPOSED", "HAZARD", "Is the parcel inside a verified storm footprint?",
        note="NOAA NCEI / OpenFEMA / hail swaths; no adapter",
    ),
    "storm.damage": _ingredient(
        "I-DIS-01", "BOOLEAN", "PROPOSED", "HAZARD", "Is property damage validated (footprint != damage)?",
    ),
    # debt / liens / listing
    "debt.mortgage_open": _ingredient(
        "I-DEBT-01", "BOOLEAN", "WALLED", "MORTGAGE", "Is a recorded mortgage open, or has a release been recorded?",
        note="RELEASE_NOT_FOUND_IN_COVERAGE never means no release",
    ),
    "liens.tax_lien": _ingredient(
        "I-LIEN-01", "BOOLEAN", "PARTIAL", "LIEN", "What liens / judgments are recorded, in what position?",
        note="tag vocabulary exists; stacked signals empty on sealed NC",
    ),
    "listing.active": _ingredient(
        "I-LST-01", "BOOLEAN", "WALLED", "LISTING", "Is the property actively listed?",
        note="UNKNOWN is not 'not listed'",
    ),
    "listing.failed_36mo": _ingredient(
        "I-LST-01", "BOOLEAN", "WALLED", "LISTING", "Did a listing expire / withdraw within 36 months?",
        note="MLS listing history WALLED (gap.listing_history.mls)",
    ),
    "deed.recorded_event": _ingredient(
        "I-TEN-01", "BOOLEAN", "SOURCE_REQUIRED", "DEED_EVENTS", "Was a new deed or encumbrance recorded?",
        note="roll-vintage owner delta or recorder index (walled)",
    ),
    # land / value / tenure
    "transfer.tenure_years": _ingredient(
        "I-TEN-01", "NUMBER", "SOURCE_REQUIRED", "DEED_TENURE", "How many years since the last arm's-length transfer?",
        note="no NC deed tape; Cass GIS SaleDate available",
    ),
    "equity.estimated_pct": _ingredient(
        "I-EQ-01", "NUMBER", "PARTIAL", "VALUATION", "What is estimated equity (value less debt proxy)?",
        max_basis="ESTIMATED",
        note="ESTIMATED never VERIFIED; kept separate from verified payoff",
    ),
    "valuation.assessed_value": _ingredient(
        "I-VAL-01", "NUMBER", "PARTIAL", "VALUATION", "What is the assessed value?",
        max_basis="ESTIMATED", producer="deedsonar assessed_value",
        note="assessed != market; MI SEV ~50% TCV",
    ),
    "valuation.land_value": _ingredient(
        "I-VAL-01", "NUMBER", "SOURCE_REQUIRED", "VALUATION", "What is the assessed land value?",
        max_basis="ESTIMATED", note="land vs improvement split not on the estate query",
    ),
    "valuation.improvement_value": _ingredient(
        "I-VAL-01", "NUMBER", "SOURCE_REQUIRED", "VALUATION", "What is the assessed improvement value / structure?",
        max_basis="ESTIMATED",
    ),
    "acreage.acres": _ingredient(
        "I-LAND-01", "NUMBER", "PARTIAL", "PARCEL", "What is the parcel acreage?",
        producer="CRM / comps; MI/IN GIS layers via landos",
    ),
    "acreage.ag": _ingredient(
        "I-LAND-01", "BOOLEAN", "PARTIAL", "PARCEL", "Is the parcel in an agricultural land-use class?",
        producer="MI/IN GIS land-use class (Berrien ag>=20ac)",
    ),
    "assemblage.adjacent": _ingredient(
        "I-ADJ-01", "BOOLEAN", "PROPOSED", "PARCEL_GRAPH", "Who owns the adjacent parcels?",
        note="needs a parcel adjacency graph",
    ),
    "zoning.district": _ingredient(
        "I-ZON-01", "CATEGORY", "PARTIAL", "ZONING", "What is the zoning district?",
        note="LandOS v2 dev_fit neutral placeholder",
    ),
    # process clocks (I-CLK-01): state per (object, governing rule); UNKNOWN never = not due
    "clock.tax_delinquency_clock": _ingredient(
        "I-CLK-01", "CATEGORY", "PARTIAL", "TAX_YEARS_THRESHOLD",
        "Where is the parcel on its jurisdiction's delinquency threshold schedule?",
        note="county threshold schedules not normalized",
    ),
    "clock.foreclosure_transition_clock": _ingredient(
        "I-CLK-01", "CATEGORY", "PARTIAL", "TAX",
        "What is the current docket stage, and has it stalled?",
        note="county_foreclosure_docket_publication PARTIAL; transition history not published",
    ),
    "clock.redemption_clock": _ingredient(
        "I-CLK-01", "CATEGORY", "PARTIAL", "TAX_SALE_CALENDAR",
        "Is the redemption window active, approaching expiry, or resolved?",
    ),
    "clock.probate_event_clock": _ingredient(
        "I-CLK-01", "CATEGORY", "PARTIAL", "PROBATE_DEPTH", "Where is the estate in its probate process?",
    ),
    "clock.code_enforcement_clock": _ingredient(
        "I-CLK-01", "CATEGORY", "MISSING", "CODE_VACANCY",
        "Where is the code case (notice / hearing / order / appeal / closed)?",
    ),
}

CLOCK_STATES: dict[str, tuple[str, ...]] = {
    "clock.tax_delinquency_clock": ("START", "APPROACHING", "CROSSED", "RESOLVED", "STALE"),
    "clock.foreclosure_transition_clock": ("FILED", "PROGRESSING", "STALLED", "RESOLVED"),
    "clock.redemption_clock": ("SALE", "REDEMPTION_ACTIVE", "APPROACHING_EXPIRY", "EXPIRED", "RESOLVED"),
    "clock.probate_event_clock": ("OPENED", "EXPECTED_EVENT", "AGING", "STALLED", "CLOSED"),
    "clock.code_enforcement_clock": (
        "OPENED", "NOTICE", "HEARING", "ORDER", "APPEAL", "COMPLY", "DEMOLITION", "CLOSED",
    ),
}

SOS_INACTIVE_STATES = ["ADMIN_DISSOLVED", "DISSOLVED", "INACTIVE", "REVOKED"]

#: Generic clock names (recipe-engine RECIPE_PROCESS_CLOCK_COVERAGE.csv vocabulary)
#: -> jurisdiction clock ids in the process-clock registry, with the state that
#: registry records.  A recipe's urgency is only as real as its clock.
PROCESS_CLOCKS: dict[str, dict[str, Any]] = {
    "tax_delinquency_clock": {"registry_clock_ids": ["PC-MI-TAXFC", "PC-NC-TAXFC"], "implementation_state": "PARTIAL"},
    "foreclosure_transition_clock": {"registry_clock_ids": ["PC-NC-TAXFC"], "implementation_state": "PARTIAL"},
    "tax_sale_clock": {"registry_clock_ids": ["PC-IN-TAXSALE", "PC-MI-TAXFC"], "implementation_state": "PARTIAL"},
    "redemption_clock": {"registry_clock_ids": ["PC-IN-TAXSALE", "PC-MI-TAXFC"], "implementation_state": "PARTIAL"},
    "probate_event_clock": {"registry_clock_ids": ["PC-PROBATE-MI", "PC-PROBATE-NC"], "implementation_state": "PARTIAL"},
    "death_preprobate_clock": {"registry_clock_ids": ["PC-DEATH-PREPROBATE"], "implementation_state": "MISSING"},
    "estate_sale_clock": {"registry_clock_ids": ["PC-ESTATE-SALE"], "implementation_state": "IMPLEMENTED"},
    "code_enforcement_clock": {"registry_clock_ids": ["PC-CODE-ENF"], "implementation_state": "CONTRACT_ONLY"},
    "entity_dissolution_clock": {"registry_clock_ids": ["PC-ENTITY"], "implementation_state": "MISSING"},
    "listing_lifecycle_clock": {"registry_clock_ids": ["PC-LISTING"], "implementation_state": "MISSING"},
    "title_resolution_clock": {"registry_clock_ids": ["PC-DEED-RELEASE"], "implementation_state": "MISSING"},
    "tenure_clock": {"registry_clock_ids": ["PC-OWNERSHIP-TRANSFER"], "implementation_state": "PARTIAL"},
    "ownership_transfer_clock": {"registry_clock_ids": ["PC-OWNERSHIP-TRANSFER"], "implementation_state": "PARTIAL"},
    "storm_event_clock": {"registry_clock_ids": ["PC-STORM"], "implementation_state": "MISSING"},
}

# --------------------------------------------------------------------------
# requirement helpers
# --------------------------------------------------------------------------


def _req(
    requirement_id: str,
    role: str,
    ingredient: str,
    *,
    op: str = "is",
    value: Any = True,
    decisive: bool = False,
    min_confidence: str = "MEDIUM",
    min_basis: str = "ESTIMATED",
    max_age_seconds: int | None = None,
    label: str = "",
) -> dict[str, Any]:
    return {
        "requirement_id": requirement_id,
        "role": role,
        "kind": "PREDICATE",
        "ingredient": ingredient,
        "op": op,
        "value": value,
        "negative_evidence_decisive": decisive,
        "min_confidence": min_confidence,
        "min_basis": min_basis,
        "max_age_seconds": max_age_seconds,
        "label": label,
    }


def _any_of(requirement_id: str, role: str, alternatives: list[dict[str, Any]], *, label: str = "") -> dict[str, Any]:
    return {
        "requirement_id": requirement_id,
        "role": role,
        "kind": "ANY_OF",
        "alternatives": alternatives,
        "label": label,
    }


def _undefined(requirement_id: str, role: str, inputs: list[str], *, label: str) -> dict[str, Any]:
    """A predicate the thesis needs but no artifact defines.  Always UNKNOWN."""
    return {
        "requirement_id": requirement_id,
        "role": role,
        "kind": "UNDEFINED_PREDICATE",
        "inputs": inputs,
        "label": label,
    }


def _alt(ingredient: str, **kwargs: Any) -> dict[str, Any]:
    """An ANY_OF alternative: a predicate without its own role."""
    alt = _req(f"alt.{ingredient}", "REQUIRED", ingredient, **kwargs)
    for key in ("requirement_id", "role"):
        alt.pop(key)
    return alt


def _gate(ingredient: str, op: str, value: Any) -> dict[str, Any]:
    return {"ingredient": ingredient, "op": op, "value": value}


def _not_government() -> dict[str, Any]:
    # RECIPE_SOURCE_COMPATIBILITY: hard_disqualifiers=government_owned_asset on every row.
    return _req(
        "not_government_owned", "DISQUALIFIER", "ownership.government",
        value=False, decisive=True, min_confidence="HIGH",
        label="government-owned asset (hard disqualifier)",
    )


def _deceased_or_estate(requirement_id: str, role: str = "REQUIRED", *, label: str) -> dict[str, Any]:
    return _any_of(requirement_id, role, [
        _alt("estate.open"),
        _alt("probate.opened"),
        _alt("death.verified", min_confidence="HIGH"),
    ], label=label)


def _recipe(
    recipe_n: int,
    recipe_id: str,
    canonical_name: str,
    family: str,
    *,
    requirements: list[dict[str, Any]],
    live_registry_id: str | None,
    materialized_recipe_ids: list[str] | None = None,
    kind: str = "SELLER_OPPORTUNITY",
    process_clock: str | None = None,
    economics: dict[str, Any] | None = None,
    action_constraints: list[str] | None = None,
    manual_checks: list[str] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "recipe_n": recipe_n,
        "recipe_id": recipe_id,
        "canonical_name": canonical_name,
        "family": family,
        "kind": kind,
        "aliases": {
            "live_registry_id": live_registry_id,
            "materialized_recipe_ids": list(materialized_recipe_ids or []),
        },
        "requirements": requirements,
        "process_clock": process_clock,
        "economics": economics or {"state": "UNDEFINED", "gates": [], "note": "economics floor UNDEFINED (composite weights not recovered)"},
        "action_constraints": sorted(action_constraints or []),
        "manual_checks": list(manual_checks or []),
        "notes": list(notes or []),
    }


_EQUITY_65 = dict(op="gte", value=65, min_basis="ESTIMATED", decisive=True)
_TENURE_15 = dict(op="gte", value=15, min_basis="VERIFIED", decisive=True)
_EQUITY_GATE = {"state": "DEFINED", "gates": [_gate("equity.estimated_pct", "gte", 65)],
                "note": "registry threshold equity >= 65%; ESTIMATED basis only"}

# --------------------------------------------------------------------------
# the canonical 30
# --------------------------------------------------------------------------

CANONICAL_RECIPES: list[dict[str, Any]] = [
    _recipe(
        1, "tax_threshold_watch", "TAX THRESHOLD WATCH", "Tax / forced-sale pressure",
        live_registry_id="tax_delinquent", materialized_recipe_ids=["published_tax_distress"],
        requirements=[
            _req("tax_delinquent", "REQUIRED", "tax.delinquent", label="tax delinquency event"),
            _req("threshold_state", "QUALIFYING", "clock.tax_delinquency_clock", op="in",
                 value=["APPROACHING", "CROSSED"], decisive=True,
                 label="delinquency start + jurisdiction rule -> approaching/crossed"),
            _not_government(),
            _req("amp_equity", "AMPLIFIER", "equity.estimated_pct", op="gte", value=65),
            _req("amp_absentee", "AMPLIFIER", "absentee.out_of_state"),
            _req("amp_tenure", "AMPLIFIER", "transfer.tenure_years", op="gte", value=15),
            _req("amp_estate", "AMPLIFIER", "estate.open"),
            _req("amp_code", "AMPLIFIER", "code.violation"),
            _req("amp_vacancy", "AMPLIFIER", "vacancy.vacant"),
            _req("amp_failed_listing", "AMPLIFIER", "listing.failed_36mo"),
        ],
        process_clock="tax_delinquency_clock",
        manual_checks=["recorded debt / release (gap.deed.recorded_instruments WALLED)",
                       "data freshness limit is UNDEFINED; confirm current tax status"],
        notes=["tax.delinquent is not decisive: payment cure is negative evidence, not an auto-disqualifier "
               "(RECIPE_SOURCE_COMPATIBILITY published_tax_distress)",
               "threshold state is QUALIFYING, not membership: county threshold schedules are not normalized"],
    ),
    _recipe(
        2, "tax_pressure_equity", "TAX PRESSURE + EQUITY", "Tax Pressure Equity",
        live_registry_id="tax_pressure_equity", materialized_recipe_ids=["published_tax_distress"],
        requirements=[
            _req("tax_delinquent", "REQUIRED", "tax.delinquent", label="verified delinquency"),
            _req("equity_65", "REQUIRED", "equity.estimated_pct", **_EQUITY_65, label="estimated equity >= 65%"),
            _req("actively_listed", "NEGATIVE", "listing.active", value=False, decisive=True, label="active listing"),
            _req("debt_known", "QUALIFYING", "debt.mortgage_open", value=False, decisive=True, min_confidence="HIGH",
                 label="mortgage / release confirmation"),
            _req("arrears_known", "QUALIFYING", "tax.delinquent_amount", op="exists", value=None,
                 label="current arrears amount"),
            _not_government(),
            _req("amp_tenure", "AMPLIFIER", "transfer.tenure_years", op="gte", value=15),
            _req("amp_absentee", "AMPLIFIER", "absentee.out_of_state"),
            _req("amp_estate", "AMPLIFIER", "estate.open"),
            _req("amp_code", "AMPLIFIER", "code.violation"),
            _req("amp_vacancy", "AMPLIFIER", "vacancy.vacant"),
        ],
        process_clock="tax_delinquency_clock", economics=_EQUITY_GATE,
        notes=["canonical 'off-market state' is a defeater (active listing), not a membership predicate: "
               "listing history is WALLED and UNKNOWN is not 'not listed'"],
    ),
    _recipe(
        3, "tax_out_of_state_equity", "TAX + OUT-OF-STATE + EQUITY", "Absentee Tax",
        live_registry_id="absentee_tax",
        materialized_recipe_ids=["long_term_absentee_potential_equity", "published_tax_distress"],
        requirements=[
            _req("tax_delinquent", "REQUIRED", "tax.delinquent", label="tax delinquency"),
            _req("out_of_state", "REQUIRED", "absentee.out_of_state",
                 label="mailing/situs geography mismatch (address evidence, not inference)"),
            _req("equity_65", "REQUIRED", "equity.estimated_pct", **_EQUITY_65, label="equity"),
            _not_government(),
        ],
        process_clock="tax_delinquency_clock", economics=_EQUITY_GATE,
        manual_checks=["in-state mailing agent edge cases"],
        notes=["registry WHEN uses absentee.long_hold, which is derived from primary_recipe (not eligibility "
               "authority); the canonical thesis is out-of-state, so absentee.out_of_state is the predicate"],
    ),
    _recipe(
        4, "open_probate_property", "OPEN PROBATE + PROPERTY", "Existing estate",
        live_registry_id="probate", materialized_recipe_ids=["probate_heir"],
        requirements=[
            _req("probate_opened", "REQUIRED", "probate.opened", label="probate case"),
            _req("estate_closed", "NEGATIVE", "probate.closed", value=False, decisive=True, label="closed estate"),
            _req("already_conveyed", "NEGATIVE", "deed.recorded_event", value=False, label="property already conveyed"),
            _req("authority", "QUALIFYING", "estate.authority_letters", label="estate authority where known"),
            _not_government(),
            _req("amp_tax", "AMPLIFIER", "tax.delinquent"),
            _req("amp_vacancy", "AMPLIFIER", "vacancy.vacant"),
            _req("amp_equity", "AMPLIFIER", "equity.estimated_pct", op="gte", value=65),
            _req("amp_tenure", "AMPLIFIER", "transfer.tenure_years", op="gte", value=15),
            _req("amp_failed_listing", "AMPLIFIER", "listing.failed_36mo"),
        ],
        process_clock="probate_event_clock",
        action_constraints=["PROBATE_IS_NOT_SELLER_INTENT"],
        notes=["probate.opened is not decisive: public index no-match is negative evidence, not the opposite "
               "legal fact (RECIPE_SOURCE_COMPATIBILITY open_probate_property)"],
    ),
    _recipe(
        5, "pre_probate_verified_death_event_property", "PRE-PROBATE VERIFIED DEATH-EVENT + PROPERTY", "Succession",
        live_registry_id=None,
        requirements=[
            _req("death_verified", "REQUIRED", "death.verified", min_confidence="HIGH",
                 label="lawfully sourced verified death event"),
            _req("probate_already_opened", "NEGATIVE", "probate.opened", value=False, decisive=True,
                 label="probate already opened (routes to R4)"),
            _req("authority", "QUALIFYING", "estate.authority_letters", label="estate authority"),
            _not_government(),
        ],
        process_clock="death_preprobate_clock",
        action_constraints=["NO_OUTREACH_WITHIN_21_DAYS_OF_DEATH", "RESEARCH_TRANSITION_DETECTION_ONLY"],
        manual_checks=["surviving ownership", "deed / title"],
        notes=["no registry row; owner identity resolution is a precondition of the context, not a clause"],
    ),
    _recipe(
        6, "estate_sale_property", "ESTATE SALE → PROPERTY", "Succession",
        live_registry_id=None,
        requirements=[
            _req("estate_sale", "REQUIRED", "estate_sale.event", label="estate-sale event joined to the parcel"),
            _req("decedent_match", "QUALIFYING", "death.verified", label="owner / decedent match"),
            _not_government(),
            _req("amp_tenure", "AMPLIFIER", "transfer.tenure_years", op="gte", value=15),
            _req("amp_probate", "AMPLIFIER", "probate.opened"),
            _req("amp_vacancy", "AMPLIFIER", "vacancy.vacant"),
            _req("amp_tax", "AMPLIFIER", "tax.delinquent"),
        ],
        process_clock="estate_sale_clock",
        action_constraints=["ESTATE_SALE_IS_NOT_PROPERTY_SALE", "NO_CONTACT_BEFORE_SALE_HELD_AND_AUTHORITY"],
        notes=["no registry row; property resolution is the estate_sale_identity_join, so an unjoined event "
               "never reaches this context"],
    ),
    _recipe(
        7, "estate_tax_pressure", "ESTATE + TAX PRESSURE", "Estate Tax Pressure",
        live_registry_id="estate_tax_pressure",
        materialized_recipe_ids=["stalled_tax_foreclosure_heirship", "published_tax_distress"],
        requirements=[
            _deceased_or_estate("estate_evidence", label="estate / probate / death evidence"),
            _req("tax_delinquent", "REQUIRED", "tax.delinquent", label="tax delinquency"),
            _req("estate_closed", "NEGATIVE", "probate.closed", value=False, decisive=True, label="estate closed"),
            _req("authority", "QUALIFYING", "estate.authority_letters", label="authority"),
            _not_government(),
        ],
        process_clock="tax_delinquency_clock",
        manual_checks=["title", "mortgage / equity"],
        notes=["registry WHEN uses ownership.trust_or_estate; the canonical thesis names estate/probate/death "
               "evidence, and a living trust is not an estate"],
    ),
    _recipe(
        8, "stalled_tax_foreclosure_heirship", "STALLED TAX FORECLOSURE + HEIRSHIP", "Existing estate",
        live_registry_id="stalled_tax_foreclosure_heirship",
        materialized_recipe_ids=["stalled_tax_foreclosure_heirship"],
        requirements=[
            _req("tax_foreclosure", "REQUIRED", "tax.foreclosed", label="foreclosure filing / process"),
            _any_of("deceased_or_heirship", "REQUIRED", [
                _alt("heirship.unresolved"),
                _alt("estate.open"),
                _alt("death.verified", min_confidence="HIGH"),
            ], label="owner deceased or fragmented heirship"),
            _req("stalled", "QUALIFYING", "clock.foreclosure_transition_clock", op="in", value=["STALLED"],
                 decisive=True, label="elapsed process clock with no expected transition"),
            _not_government(),
            _req("amp_equity", "AMPLIFIER", "equity.estimated_pct", op="gte", value=65),
            _req("amp_absentee", "AMPLIFIER", "absentee.out_of_state"),
            _req("amp_tenure", "AMPLIFIER", "transfer.tenure_years", op="gte", value=15),
            _req("amp_value", "AMPLIFIER", "valuation.assessed_value", op="gte", value=150000),
        ],
        process_clock="foreclosure_transition_clock",
        manual_checks=["active bankruptcy where relevant (no source)"],
        notes=["membership = production ladder predicate (heir AND tax foreclosure); the stall is the "
               "RECIPE_SOURCE_COMPATIBILITY NBE 'validate stall via latest docket', so it is QUALIFYING",
               "registry WHEN uses tax.delinquent; the thesis and the production ladder both need the "
               "foreclosure case, which is stronger"],
    ),
    _recipe(
        9, "heir_curative", "HEIR CURATIVE", "Heir Curative",
        live_registry_id="heir_curative", materialized_recipe_ids=["heir_property_acquisition_research"],
        requirements=[
            _req("heirship", "REQUIRED", "heirship.unresolved", label="fragmented heirship"),
            _req("conveyed_since", "NEGATIVE", "deed.recorded_event", value=False,
                 label="title conveyed since (a recorded deed is not proof the conveyance was clean)"),
            _req("asset_value", "QUALIFYING", "valuation.assessed_value", op="exists", value=None,
                 label="asset value / equity"),
            _req("authority", "QUALIFYING", "estate.authority_letters", label="letters / authority role"),
            _not_government(),
        ],
        process_clock="probate_event_clock",
        action_constraints=["ADVISORY_ROUTE_ONLY_WITHOUT_AUTHORITY"],
        manual_checks=["title / deed state"],
    ),
    _recipe(
        10, "title_curative", "TITLE CURATIVE", "Title Curative",
        live_registry_id="title_curative",
        materialized_recipe_ids=["tax_foreclosure_fractional_title", "fractional_family_ownership"],
        requirements=[
            _any_of("title_defect", "REQUIRED", [
                _alt("title.fractional"),
                _alt("title.defect"),
            ], label="recorded title / deed defect (fractional interest is one kind)"),
            _req("economic_value", "QUALIFYING", "valuation.assessed_value", op="exists", value=None,
                 label="economic value"),
            _not_government(),
            _req("amp_old_mortgage", "AMPLIFIER", "debt.mortgage_open"),
            _req("amp_entity", "AMPLIFIER", "entity.on_title"),
            _req("amp_deceased", "AMPLIFIER", "heirship.unresolved"),
        ],
        process_clock="title_resolution_clock",
        manual_checks=["transaction friction (undefined)", "recorder instruments (WALLED)"],
    ),
    _recipe(
        11, "code_tax_equity", "CODE + TAX + EQUITY", "Code + Tax",
        live_registry_id="code_tax", materialized_recipe_ids=["published_tax_distress"],
        requirements=[
            _req("code_violation", "REQUIRED", "code.violation", decisive=True, label="open code violation"),
            _req("tax_delinquent", "REQUIRED", "tax.delinquent", label="tax pressure"),
            _req("equity_65", "REQUIRED", "equity.estimated_pct", **_EQUITY_65, label="equity"),
            _not_government(),
            _req("amp_vacancy", "AMPLIFIER", "vacancy.vacant"),
            _req("amp_absentee", "AMPLIFIER", "absentee.out_of_state"),
            _req("amp_estate", "AMPLIFIER", "estate.open"),
        ],
        process_clock="code_enforcement_clock", economics=_EQUITY_GATE,
        manual_checks=["violation severity (minor code noise is not decisive)", "occupancy"],
    ),
    _recipe(
        12, "code_estate", "CODE + ESTATE", "Code + Estate",
        live_registry_id="code_estate",
        requirements=[
            _req("code_violation", "REQUIRED", "code.violation", decisive=True, label="code issue"),
            _deceased_or_estate("estate_evidence", label="estate transition"),
            _req("authority", "QUALIFYING", "estate.authority_letters", label="authority"),
            _not_government(),
        ],
        process_clock="code_enforcement_clock",
        manual_checks=["occupancy", "violation severity", "economics"],
        notes=["registry WHEN uses ownership.trust_or_estate; the thesis is an estate transition"],
    ),
    _recipe(
        13, "failed_listing_high_equity_long_tenure", "FAILED LISTING + HIGH EQUITY + LONG TENURE",
        "Failed Listing Equity",
        live_registry_id="failed_listing_equity",
        requirements=[
            _req("failed_listing", "REQUIRED", "listing.failed_36mo", label="expired / withdrawn / failed listing"),
            _req("equity_65", "REQUIRED", "equity.estimated_pct", **_EQUITY_65, label="equity"),
            _req("tenure_15", "REQUIRED", "transfer.tenure_years", **_TENURE_15, label="tenure >= 15y"),
            _req("relisted_active", "NEGATIVE", "listing.active", value=False, decisive=True, label="re-listed active"),
            _not_government(),
            _req("amp_tax", "AMPLIFIER", "tax.delinquent"),
            _req("amp_estate", "AMPLIFIER", "estate.open"),
            _req("amp_absentee", "AMPLIFIER", "absentee.out_of_state"),
        ],
        process_clock="listing_lifecycle_clock",
        economics={"state": "DEFINED",
                   "gates": [_gate("equity.estimated_pct", "gte", 65), _gate("transfer.tenure_years", "gte", 15)],
                   "note": "equity >= 65%; tenure >= 15y"},
        action_constraints=["LISTING_RIGHTS_REQUIRED"],
    ),
    _recipe(
        14, "long_hold_free_clear", "LONG HOLD FREE & CLEAR", "Long Hold Free & Clear",
        live_registry_id="long_hold_free_clear",
        requirements=[
            _req("tenure_15", "REQUIRED", "transfer.tenure_years", **_TENURE_15, label="long ownership tenure"),
            _req("no_open_mortgage", "REQUIRED", "debt.mortgage_open", value=False, decisive=True,
                 min_confidence="HIGH", label="no active secured debt evidence (release evidence required)"),
            _req("actively_listed", "NEGATIVE", "listing.active", value=False, decisive=True, label="off-market"),
            _not_government(),
        ],
        process_clock="tenure_clock",
        economics={"state": "DEFINED", "gates": [_gate("transfer.tenure_years", "gte", 15)], "note": "tenure >= 15y"},
        action_constraints=["NEVER_ASSERT_FREE_AND_CLEAR_WITHOUT_RELEASE_EVIDENCE"],
    ),
    _recipe(
        15, "long_hold_high_equity", "LONG HOLD HIGH EQUITY", "Long Hold High Equity",
        live_registry_id="long_hold_high_equity",
        requirements=[
            _req("tenure_15", "REQUIRED", "transfer.tenure_years", **_TENURE_15, label="tenure"),
            _req("equity_65", "REQUIRED", "equity.estimated_pct", **_EQUITY_65, label="estimated value less debt"),
            _req("value_known", "QUALIFYING", "valuation.assessed_value", op="exists", value=None,
                 label="estimated value"),
            _not_government(),
        ],
        process_clock="tenure_clock",
        economics={"state": "DEFINED",
                   "gates": [_gate("transfer.tenure_years", "gte", 15), _gate("equity.estimated_pct", "gte", 65)],
                   "note": "tenure >= 15y; equity >= 65%"},
        action_constraints=["KEEP_ESTIMATED_EQUITY_SEPARATE_FROM_VERIFIED_PAYOFF"],
    ),
    _recipe(
        16, "absentee_code_equity", "ABSENTEE + CODE + EQUITY", "Absentee Code",
        live_registry_id="absentee_code",
        requirements=[
            _req("out_of_state", "REQUIRED", "absentee.out_of_state", label="mailing / situs mismatch"),
            _req("code_violation", "REQUIRED", "code.violation", decisive=True, label="code violation"),
            _req("equity_65", "REQUIRED", "equity.estimated_pct", **_EQUITY_65, label="equity"),
            _not_government(),
        ],
        process_clock="code_enforcement_clock", economics=_EQUITY_GATE,
        action_constraints=["NEVER_INFER_INTENT"],
        manual_checks=["violation severity"],
        notes=["registry WHEN uses absentee.long_hold (derived from primary_recipe); the thesis is mailing/situs"],
    ),
    _recipe(
        17, "defunct_entity_on_title", "DEFUNCT ENTITY ON TITLE", "Entity",
        live_registry_id=None,
        requirements=[
            _req("entity_owner", "REQUIRED", "entity.on_title", label="entity / title owner"),
            _req("sos_inactive", "REQUIRED", "entity.sos_status", op="in", value=SOS_INACTIVE_STATES,
                 decisive=True, min_basis="VERIFIED", label="SOS dissolution / inactive status"),
            _not_government(),
            _req("amp_tax", "AMPLIFIER", "tax.delinquent"),
            _req("amp_code", "AMPLIFIER", "code.violation"),
            _req("amp_equity", "AMPLIFIER", "equity.estimated_pct", op="gte", value=65),
        ],
        process_clock="entity_dissolution_clock",
        action_constraints=["DISSOLVED_IS_NOT_NO_AUTHORITY"],
        manual_checks=["successor authority", "title counsel"],
        notes=["no registry row; an ACTIVE / reinstated status contradicts decisively"],
    ),
    _recipe(
        18, "vacant_estate_tax", "VACANT ESTATE + TAX", "Vacant Estate",
        live_registry_id="vacant_estate", materialized_recipe_ids=["published_tax_distress"],
        requirements=[
            _req("vacant", "REQUIRED", "vacancy.vacant", decisive=True, label="vacancy evidence (occupied = opposite)"),
            _deceased_or_estate("estate_evidence", label="estate transition"),
            _req("tax_delinquent", "REQUIRED", "tax.delinquent", label="tax pressure"),
            _not_government(),
        ],
        process_clock="tax_delinquency_clock",
        action_constraints=["VACANCY_MUST_BE_EVIDENTIAL_NOT_MAILING_MISMATCH"],
        notes=["registry WHEN vacant_estate uses ownership.trust_or_estate; the thesis is an estate transition"],
    ),
    _recipe(
        19, "old_mortgage_release_gap", "OLD MORTGAGE / RELEASE GAP", "Old Mortgage",
        live_registry_id="old_mortgage",
        requirements=[
            _req("mortgage_no_release", "REQUIRED", "debt.mortgage_open", decisive=True, min_confidence="LOW",
                 label="old recorded mortgage with no release found in coverage"),
            _req("tenure_15", "REQUIRED", "transfer.tenure_years", **_TENURE_15, label="old (registry tenure >= 15)"),
            _req("property_value", "QUALIFYING", "valuation.assessed_value", op="exists", value=None,
                 label="property value"),
            _not_government(),
        ],
        process_clock="title_resolution_clock",
        action_constraints=["RELEASE_NOT_FOUND_IN_COVERAGE_IS_NOT_NO_RELEASE"],
        notes=["a release found anywhere (debt.mortgage_open ABSENT) contradicts decisively"],
    ),
    _recipe(
        20, "lien_stack_equity", "LIEN STACK + EQUITY", "Lien Stack",
        live_registry_id="lien_stack",
        requirements=[
            _req("liens", "REQUIRED", "liens.tax_lien", decisive=True, label="liens / judgments (released = opposite)"),
            _req("asset_value", "QUALIFYING", "valuation.assessed_value", op="exists", value=None, label="asset value"),
            _req("secured_obligations", "QUALIFYING", "equity.estimated_pct", op="exists", value=None,
                 label="estimated secured obligations"),
            _not_government(),
        ],
        process_clock="title_resolution_clock",
        manual_checks=["lien position / release sequencing"],
    ),
    _recipe(
        21, "tax_sale_rescue_pre_sale", "TAX SALE RESCUE / PRE-SALE", "Tax Sale Rescue",
        live_registry_id="tax_sale_rescue", materialized_recipe_ids=["tax_foreclosure_curative"],
        requirements=[
            _req("sale_scheduled", "REQUIRED", "tax_sale.sale_date", op="after_as_of", value=None,
                 min_basis="VERIFIED", label="verified sale event and date still ahead"),
            _req("equity_known", "QUALIFYING", "equity.estimated_pct", op="exists", value=None, label="equity"),
            _req("redemption_rules", "QUALIFYING", "clock.redemption_clock", op="exists", value=None,
                 label="redemption / process rules"),
            _not_government(),
        ],
        process_clock="tax_sale_clock",
        action_constraints=["FORECLOSURE_RESCUE_LEGAL_REVIEW_REQUIRED"],
        notes=["a past sale date is not decisive: the sale may have been continued, not held",
               "tax sale source types are JOIN_KEY_AUTHORITY_BLOCKED in source_factory"],
    ),
    _recipe(
        22, "redemption_window", "REDEMPTION WINDOW", "Redemption Opportunity",
        live_registry_id="redemption_opportunity", materialized_recipe_ids=["tax_foreclosure_curative"],
        requirements=[
            _req("redemption_active", "REQUIRED", "clock.redemption_clock", op="in",
                 value=["APPROACHING_EXPIRY", "REDEMPTION_ACTIVE"], decisive=True,
                 label="redemption window active (redeemed / expired = opposite)"),
            _req("deadline_known", "QUALIFYING", "tax_sale.redemption_deadline", op="after_as_of", value=None,
                 decisive=True, min_basis="VERIFIED", label="redemption expiration"),
            _req("authority", "QUALIFYING", "estate.authority_letters", label="authority"),
            _not_government(),
        ],
        process_clock="redemption_clock",
        action_constraints=["FORECLOSURE_RESCUE_LEGAL_REVIEW_REQUIRED"],
    ),
    _recipe(
        23, "storm_absentee", "STORM + ABSENTEE", "Storm Absentee",
        live_registry_id="storm_absentee",
        requirements=[
            _req("storm_footprint", "REQUIRED", "storm.footprint", label="verified storm footprint"),
            _req("out_of_state", "REQUIRED", "absentee.out_of_state", label="occupancy / absentee evidence"),
            _req("damage", "QUALIFYING", "storm.damage", decisive=True, label="damage validation"),
            _req("structure", "QUALIFYING", "valuation.improvement_value", op="exists", value=None,
                 label="structure data"),
            _not_government(),
        ],
        process_clock="storm_event_clock",
        action_constraints=["FOOTPRINT_IS_NOT_DAMAGE"],
        notes=["registry WHEN uses storm.damage; the canonical thesis separates footprint (membership) from "
               "damage (validation)"],
    ),
    _recipe(
        24, "storm_estate", "STORM + ESTATE", "Storm Estate",
        live_registry_id="storm_estate",
        requirements=[
            _req("storm_footprint", "REQUIRED", "storm.footprint", label="event footprint"),
            _deceased_or_estate("estate_evidence", label="estate transition"),
            _req("damage", "REQUIRED", "storm.damage", decisive=True, label="property damage evidence"),
            _req("authority", "QUALIFYING", "estate.authority_letters", label="authority"),
            _not_government(),
        ],
        process_clock="storm_event_clock",
        action_constraints=["FOOTPRINT_IS_NOT_DAMAGE"],
    ),
    _recipe(
        25, "agricultural_succession", "AGRICULTURAL SUCCESSION", "Agricultural Succession",
        live_registry_id="agricultural_succession",
        requirements=[
            _req("ag_property", "REQUIRED", "acreage.ag", label="agricultural property"),
            _req("acres_20", "REQUIRED", "acreage.acres", op="gte", value=20, decisive=True, min_basis="VERIFIED",
                 label="acreage >= 20 (registry)"),
            _any_of("succession", "REQUIRED", [
                _alt("ownership.trust_or_estate"),
                _alt("estate.open"),
                _alt("probate.opened"),
                _alt("death.verified", min_confidence="HIGH"),
            ], label="succession / estate transition"),
            _req("land_value", "QUALIFYING", "valuation.land_value", op="exists", value=None,
                 label="land characteristics / economics"),
            _not_government(),
            _req("amp_tenure", "AMPLIFIER", "transfer.tenure_years", op="gte", value=15),
        ],
        process_clock="probate_event_clock",
        economics={"state": "DEFINED", "gates": [_gate("acreage.acres", "gte", 20)], "note": "acreage >= 20"},
        manual_checks=["successor trustee", "operation status"],
        notes=["'long tenure' has no threshold in the thesis, so it is an amplifier only (15y is the registry "
               "convention for other long-hold rows, not this one)"],
    ),
    _recipe(
        26, "assemblage", "ASSEMBLAGE", "Assemblage",
        live_registry_id="assemblage",
        requirements=[
            _req("adjacent", "REQUIRED", "assemblage.adjacent", label="adjacency"),
            _req("zoning", "QUALIFYING", "zoning.district", op="exists", value=None, label="zoning / use"),
            _req("value", "QUALIFYING", "valuation.assessed_value", op="exists", value=None, label="value / economics"),
            _not_government(),
        ],
        manual_checks=["adjacent owner identity", "assembly complexity", "exit demand"],
    ),
    _recipe(
        27, "infill_land_improvement_imbalance", "INFILL / LAND-IMPROVEMENT IMBALANCE", "High Land / Low Improvement",
        live_registry_id="high_land_low_improvement",
        requirements=[
            _undefined("land_improvement_imbalance", "REQUIRED",
                       ["valuation.land_value", "valuation.improvement_value"],
                       label="land value high relative to improvement value (no threshold defined anywhere)"),
            _req("acres_1", "REQUIRED", "acreage.acres", op="gte", value=1, decisive=True, min_basis="VERIFIED",
                 label="acreage >= 1 (registry)"),
            _req("zoning", "QUALIFYING", "zoning.district", op="exists", value=None, label="zoning"),
            _not_government(),
        ],
        economics={"state": "DEFINED", "gates": [_gate("acreage.acres", "gte", 1)], "note": "acreage >= 1"},
        manual_checks=["parcel geometry", "market economics"],
        notes=["the imbalance ratio is a registry ruling, not a source: it stays PREDICATE_UNDEFINED"],
    ),
    _recipe(
        28, "vacant_demolition_order_land_value", "VACANT / DEMOLITION ORDER + LAND VALUE", "Vacant Tax",
        live_registry_id="vacant_tax",
        requirements=[
            _req("demolition_order", "REQUIRED", "code.demolition_order", label="demolition / unsafe order"),
            _req("land_value", "QUALIFYING", "valuation.land_value", op="exists", value=None, label="land value"),
            _req("zoning", "QUALIFYING", "zoning.district", op="exists", value=None, label="zoning"),
            _req("order_state", "QUALIFYING", "clock.code_enforcement_clock", op="in",
                 value=["DEMOLITION", "ORDER"], decisive=True, label="order status (appealed / resolved = opposite)"),
            _not_government(),
            _req("amp_tax", "AMPLIFIER", "tax.delinquent"),
            _req("amp_vacancy", "AMPLIFIER", "vacancy.vacant"),
        ],
        process_clock="code_enforcement_clock",
        action_constraints=["ORDER_MAY_BE_APPEALED_OR_RESOLVED"],
        manual_checks=["owner join (SJC parcels lack owner)", "improvement state"],
        notes=["an order is not decisive on its own: it may be appealed or resolved"],
    ),
    _recipe(
        29, "ownership_transition_watch", "OWNERSHIP TRANSITION WATCH", "Watch",
        live_registry_id=None, kind="WATCH",
        requirements=[
            _any_of("transition_event", "REQUIRED", [
                _alt("deed.recorded_event"),
                _alt("entity.sos_status", op="in", value=SOS_INACTIVE_STATES),
                _alt("estate.open"),
                _alt("debt.mortgage_open"),
                _alt("listing.active"),
            ], label="deed transfer / entity change / estate / mortgage / listing event"),
            _not_government(),
        ],
        process_clock="ownership_transfer_clock",
        notes=["no registry row; DeedSonar watch deltas are membership-level, this is evidence-level"],
    ),
    _recipe(
        30, "data_gap_nbe_recipe", "DATA-GAP NBE RECIPE", "Meta",
        live_registry_id=None, kind="META", requirements=[],
        economics={"state": "UNDEFINED", "gates": [], "note": "expected information value, not seller economics"},
        notes=["not a seller recipe and not a registry row (14_IMPLEMENTATION_SEQUENCE ruling): a projection "
               "over the other recipes' evaluations -- UNKNOWN memberships blocked by exactly one unresolved "
               "membership predicate"],
    ),
]


def canonical_recipe_spec() -> dict[str, Any]:
    """The whole compilable source, as one value (copied by the compiler)."""
    return {
        "schema_version": CANONICAL_RECIPE_SPEC_CONTRACT,
        "spec_version": CANONICAL_RECIPE_SPEC_VERSION,
        "provenance": SPEC_PROVENANCE,
        "ingredients": INGREDIENTS,
        "clock_states": {key: list(value) for key, value in CLOCK_STATES.items()},
        "process_clocks": PROCESS_CLOCKS,
        "recipes": CANONICAL_RECIPES,
    }
