"""Versioned wire contracts for explainable opportunity intelligence.

Recipe Engine owns the *definition* of the recipe and the portable request
shape.  LandOS owns evaluation.  The boundary is deliberately JSON-only so the
two services do not need a shared Python installation to agree on a result.

There are no optional silent defaults at the contract boundary: omitted
readiness facts are interpreted by the evaluator as ``UNKNOWN`` and unknown
signal types must be carried through as unscored observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.identity import digest


RECIPE_CONTRACT_VERSION = "neuruh.opportunity-intelligence.recipe.v1"
INPUT_CONTRACT_VERSION = "neuruh.opportunity-intelligence.input.v1"
RESULT_CONTRACT_VERSION = "neuruh.opportunity-intelligence.result.v1"
RECEIPT_CONTRACT_VERSION = "neuruh.opportunity-intelligence.receipt.v1"
PROPERTY_POPULATION_CONTRACT_VERSION = (
    "neuruh.opportunity-intelligence.property-population.v1"
)
SOURCE_FACTORY_RUN_RECEIPT_VERSION = "source_factory.run_receipt.v1"
SOURCE_FACTORY_ADAPTER_CONTRACT_VERSION = (
    "neuruh.opportunity-intelligence.source-factory-adapter.v1"
)
VERIFICATION_RECEIPT_VERSION = (
    "neuruh.opportunity-intelligence.verification-receipt.v1"
)

CANONICAL_RECIPE_ID = "land_acquisition_explainable"
CANONICAL_RECIPE_VERSION = "1.0.0"
SCORE_VERSION = "landos.opportunity-score.v1"

# Portable copy of the T50 research-result vocabulary accepted by LandOS.  The
# cross-repo verification court asserts that every value below is accepted by
# both sides of the seam and that an undeclared value is rejected by both.
GENERIC_RESEARCH_RESULT_STATES = (
    "FOUND",
    "PARTIAL",
    "NO_MATCH",
    "AMBIGUOUS",
    "INCONCLUSIVE",
    "OFFICIAL_COPY_REQUIRED",
    "OPERATOR_REQUIRED",
    "BLOCKED",
    "NOT_APPLICABLE",
    "DEFERRED",
)
ENGINE_RESEARCH_RESULT_STATE_MAP = {
    "OFFICIAL_ESTATE_CASE_FOUND": "FOUND",
    "PUBLIC_INDEX_NO_MATCH": "NO_MATCH",
    "AMBIGUOUS_MATCH": "AMBIGUOUS",
    "SEARCH_INCONCLUSIVE": "INCONCLUSIVE",
    "HUMAN_VERIFICATION_REQUIRED": "OPERATOR_REQUIRED",
    "OFFICIAL_COPY_REQUIRED": "OFFICIAL_COPY_REQUIRED",
}

ASSET_CLASSES = (
    "waterfront",
    "acreage",
    "vacant_land",
    "residential",
    "commercial",
    "unknown",
)
SIGNAL_STATES = ("PRESENT", "ABSENT", "UNKNOWN", "CONFLICTED")
CONFIDENCE_LEVELS = ("HIGH", "MEDIUM", "LOW", "UNKNOWN")
READINESS_FACT_STATES = ("YES", "NO", "UNKNOWN")
READINESS_STATES = ("READY", "NOT_READY", "UNKNOWN")
PROPERTY_IDENTITY_KINDS = ("parcel", "address", "unresolved")

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_WS = re.compile(r"\s+")
_STREET_SUFFIX = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR",
    "LANE": "LN", "COURT": "CT", "CIRCLE": "CIR", "BOULEVARD": "BLVD",
    "PLACE": "PL", "TERRACE": "TER", "PARKWAY": "PKWY", "HIGHWAY": "HWY",
    "TRAIL": "TRL", "WAY": "WAY",
}
_DIRECTIONAL = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE",
    "SOUTHWEST": "SW",
}

LINEAGE_LIST_FIELDS = (
    "evidence_refs",
    "contact_evidence_refs",
    "source_families",
    "relationship_node_refs",
    "relationship_edge_refs",
    "quarantined_identity_refs",
)


class OpportunityContractError(ValueError):
    """A recipe or request cannot be interpreted without guessing."""


def _nonempty(value: Any, path: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise OpportunityContractError(f"{path} must be non-empty")
    return text


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise OpportunityContractError(f"{path} must be a list")
    return value


def _nonnegative_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OpportunityContractError(f"{path} must be a non-negative integer")
    return value


def _decimal(value: Any, path: str) -> Decimal:
    if isinstance(value, bool):
        raise OpportunityContractError(f"{path} must be a finite non-negative number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise OpportunityContractError(
            f"{path} must be a finite non-negative number"
        ) from None
    if not number.is_finite() or number < 0:
        raise OpportunityContractError(f"{path} must be a finite non-negative number")
    return number


def _round4(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def generic_research_result_state(value: Any) -> str | None:
    """Narrow a T50 generic/engine research result without guessing."""
    if not isinstance(value, str) or not value.strip():
        return None
    key = value.strip().upper()
    if key in GENERIC_RESEARCH_RESULT_STATES:
        return key
    return ENGINE_RESEARCH_RESULT_STATE_MAP.get(key)


def normalize_property_apn(value: Any) -> str:
    """Mirror source-factory/Territory APN normalization; leading zeros survive."""
    return _NON_ALNUM.sub("", str(value or "")).upper()


def normalize_property_address(value: Any) -> str:
    """Mirror the intentionally conservative source-factory address folding."""
    text = _NON_ALNUM.sub(" ", str(value or "")).upper()
    tokens = [token for token in _WS.sub(" ", text).strip().split(" ") if token]
    return " ".join(
        _STREET_SUFFIX.get(token, _DIRECTIONAL.get(token, token))
        for token in tokens
    )


def property_identity(
    *, county_fips: Any, parcel_apn: Any = None, raw_address: Any = None
) -> dict[str, str]:
    """Build the portable form of ``source_factory.identity.PropertyKey``."""
    county = str(county_fips or "").strip()
    apn = normalize_property_apn(parcel_apn)
    if apn:
        return {
            "kind": "parcel", "key": f"{county}|P|{apn}",
            "county_fips": county, "basis_value": apn,
        }
    address = normalize_property_address(raw_address)
    if address:
        return {
            "kind": "address", "key": f"{county}|A|{address}",
            "county_fips": county, "basis_value": address,
        }
    return {
        "kind": "unresolved", "key": f"{county}|U|",
        "county_fips": county, "basis_value": "",
    }


def _validate_source_rollup(rollup: Any, path: str) -> None:
    if not isinstance(rollup, Mapping):
        raise OpportunityContractError(f"{path} must be an object")
    signal_count = _nonnegative_int(
        rollup.get("landed_signal_count"), f"{path}.landed_signal_count"
    )
    property_count = _nonnegative_int(
        rollup.get("property_count"), f"{path}.property_count"
    )
    unresolved = _nonnegative_int(
        rollup.get("unresolved_signal_count"),
        f"{path}.unresolved_signal_count",
    )
    if unresolved > signal_count:
        raise OpportunityContractError(
            f"{path}.unresolved_signal_count cannot exceed landed_signal_count"
        )
    if signal_count and property_count > signal_count:
        raise OpportunityContractError(
            f"{path}.property_count cannot exceed landed_signal_count"
        )
    amplification = _decimal(
        rollup.get("signal_amplification"), f"{path}.signal_amplification"
    )
    expected_amplification = _round4(signal_count, property_count)
    if amplification != expected_amplification:
        raise OpportunityContractError(
            f"{path}.signal_amplification must equal landed_signal_count / "
            f"property_count ({expected_amplification})"
        )

    kinds = rollup.get("identity_kinds")
    if not isinstance(kinds, Mapping) or not set(kinds) <= set(PROPERTY_IDENTITY_KINDS):
        raise OpportunityContractError(
            f"{path}.identity_kinds may contain only parcel/address/unresolved"
        )
    kind_counts = {
        str(kind): _nonnegative_int(count, f"{path}.identity_kinds.{kind}")
        for kind, count in kinds.items()
    }
    if sum(kind_counts.values()) != signal_count:
        raise OpportunityContractError(
            f"{path}.identity_kinds must sum to landed_signal_count"
        )
    if kind_counts.get("unresolved", 0) != unresolved:
        raise OpportunityContractError(
            f"{path}.identity_kinds.unresolved must equal unresolved_signal_count"
        )

    histogram = rollup.get("multiplicity_histogram")
    if not isinstance(histogram, Mapping):
        raise OpportunityContractError(f"{path}.multiplicity_histogram must be an object")
    property_total = 0
    resolved_signal_total = 0
    for multiplicity, count in histogram.items():
        try:
            multiplicity_int = int(str(multiplicity))
        except ValueError:
            raise OpportunityContractError(
                f"{path}.multiplicity_histogram keys must be positive integers"
            ) from None
        if multiplicity_int <= 0 or str(multiplicity_int) != str(multiplicity):
            raise OpportunityContractError(
                f"{path}.multiplicity_histogram keys must be positive integers"
            )
        property_bucket = _nonnegative_int(
            count, f"{path}.multiplicity_histogram.{multiplicity}"
        )
        property_total += property_bucket
        resolved_signal_total += multiplicity_int * property_bucket
    if property_total != property_count:
        raise OpportunityContractError(
            f"{path}.multiplicity_histogram must account for every property"
        )
    if resolved_signal_total != signal_count - unresolved:
        raise OpportunityContractError(
            f"{path}.multiplicity_histogram must account for every resolved signal"
        )


def validate_source_factory_rollup(
    rollup: Any, *, path: str = "source_factory.receipt.estate"
) -> Mapping[str, Any]:
    """Public adapter seam for the exact ``source_factory.run_receipt.v1`` estate."""
    _validate_source_rollup(rollup, path)
    return rollup


def validate_property_population(
    population: Any, *, signals: Sequence[Mapping[str, Any]],
    subject: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Validate distinct signal and property cardinalities without conflation."""
    path = "input.property_population"
    if not isinstance(population, Mapping):
        raise OpportunityContractError(f"{path} must be an object")
    if population.get("schema_version") != PROPERTY_POPULATION_CONTRACT_VERSION:
        raise OpportunityContractError(
            f"{path}.schema_version must be {PROPERTY_POPULATION_CONTRACT_VERSION}"
        )
    if population.get("scope") != "SUBJECT_SIGNAL_SET":
        raise OpportunityContractError(f"{path}.scope must be SUBJECT_SIGNAL_SET")

    signal_count = _nonnegative_int(
        population.get("signal_observation_count"),
        f"{path}.signal_observation_count",
    )
    if signal_count != len(signals):
        raise OpportunityContractError(
            f"{path}.signal_observation_count must equal len(input.signals)"
        )
    property_count = _nonnegative_int(
        population.get("property_count"), f"{path}.property_count"
    )
    unresolved = _nonnegative_int(
        population.get("unresolved_signal_count"),
        f"{path}.unresolved_signal_count",
    )
    if unresolved > signal_count:
        raise OpportunityContractError(
            f"{path}.unresolved_signal_count cannot exceed signal_observation_count"
        )

    identity = population.get("property_identity")
    if not isinstance(identity, Mapping):
        raise OpportunityContractError(f"{path}.property_identity must be an object")
    kind = identity.get("kind")
    if kind not in PROPERTY_IDENTITY_KINDS:
        raise OpportunityContractError(
            f"{path}.property_identity.kind must be parcel/address/unresolved"
        )
    county_fips = _nonempty(
        identity.get("county_fips"), f"{path}.property_identity.county_fips"
    )
    basis_value = str(identity.get("basis_value") or "")
    expected_identity = property_identity(
        county_fips=county_fips,
        parcel_apn=basis_value if kind == "parcel" else None,
        raw_address=basis_value if kind == "address" else None,
    )
    if kind == "unresolved":
        expected_identity = property_identity(county_fips=county_fips)
    if dict(identity) != expected_identity:
        raise OpportunityContractError(
            f"{path}.property_identity does not match the source-factory property-key rules"
        )
    if subject is not None and any(
        subject.get(field) not in (None, "")
        for field in ("county_fips", "geography", "parcel_apn", "raw_address")
    ):
        subject_identity = property_identity(
            county_fips=subject.get("county_fips") or subject.get("geography"),
            parcel_apn=subject.get("parcel_apn"),
            raw_address=subject.get("raw_address"),
        )
        if subject_identity != expected_identity:
            raise OpportunityContractError(
                "input.subject resolves to a different property identity than "
                "input.property_population"
            )
    expected_property_count = 0 if kind == "unresolved" else 1
    if property_count != expected_property_count:
        raise OpportunityContractError(
            f"{path}.property_count must be {expected_property_count} for "
            f"a {kind} subject identity"
        )

    kinds = population.get("identity_kinds")
    if not isinstance(kinds, Mapping) or not set(kinds) <= set(PROPERTY_IDENTITY_KINDS):
        raise OpportunityContractError(
            f"{path}.identity_kinds may contain only parcel/address/unresolved"
        )
    kind_counts = {
        str(identity_kind): _nonnegative_int(
            count, f"{path}.identity_kinds.{identity_kind}"
        )
        for identity_kind, count in kinds.items()
    }
    if sum(kind_counts.values()) != signal_count:
        raise OpportunityContractError(
            f"{path}.identity_kinds must sum to signal_observation_count"
        )
    if kind_counts.get("unresolved", 0) != unresolved:
        raise OpportunityContractError(
            f"{path}.identity_kinds.unresolved must equal unresolved_signal_count"
        )
    if signal_count and kind_counts.get(str(kind), 0) != signal_count:
        raise OpportunityContractError(
            f"{path}.identity_kinds must place every observation on the subject identity"
        )

    amplification = _decimal(
        population.get("signal_amplification"), f"{path}.signal_amplification"
    )
    expected_amplification = _round4(signal_count, property_count)
    if amplification != expected_amplification:
        raise OpportunityContractError(
            f"{path}.signal_amplification must equal signal_observation_count / "
            f"property_count ({expected_amplification})"
        )

    provenance = population.get("dedupe_provenance")
    if not isinstance(provenance, Mapping):
        raise OpportunityContractError(f"{path}.dedupe_provenance must be an object")
    _nonempty(provenance.get("identity_method"), f"{path}.dedupe_provenance.identity_method")
    producer_contract = _nonempty(
        provenance.get("producer_contract"),
        f"{path}.dedupe_provenance.producer_contract",
    )
    if producer_contract == SOURCE_FACTORY_RUN_RECEIPT_VERSION:
        for field in (
            "run_id", "factory_version", "producer_commit", "source_rollup_sha256",
        ):
            _nonempty(
                provenance.get(field), f"{path}.dedupe_provenance.{field}"
            )
        source_rollup = provenance.get("source_rollup")
        _validate_source_rollup(
            source_rollup,
            f"{path}.dedupe_provenance.source_rollup",
        )
        if provenance.get("source_rollup_sha256") != digest(source_rollup):
            raise OpportunityContractError(
                f"{path}.dedupe_provenance.source_rollup_sha256 does not bind "
                "the source_rollup"
            )

    for index, signal in enumerate(signals):
        signal_property_key = signal.get("property_key")
        if signal_property_key is not None and signal_property_key != identity["key"]:
            raise OpportunityContractError(
                f"input.signals[{index}].property_key does not match property_population"
            )
        has_identity_input = any(
            signal.get(field) not in (None, "")
            for field in ("geography", "county_fips", "parcel_apn", "raw_address")
        )
        if has_identity_input:
            observed_identity = property_identity(
                county_fips=signal.get("geography") or signal.get("county_fips"),
                parcel_apn=signal.get("parcel_apn"),
                raw_address=signal.get("raw_address"),
            )
            if observed_identity != expected_identity:
                raise OpportunityContractError(
                    f"input.signals[{index}] resolves to a different property identity"
                )
    return population


def recipe_config_digest(recipe: Mapping[str, Any]) -> str:
    """Digest the effective config, excluding its self-reported digest."""
    body = dict(recipe)
    body.pop("config_digest", None)
    return digest(body)


def validate_recipe(recipe: Mapping[str, Any]) -> Mapping[str, Any]:
    """Fail closed when a recipe cannot be evaluated deterministically."""
    if not isinstance(recipe, Mapping):
        raise OpportunityContractError("recipe must be an object")
    if recipe.get("schema_version") != RECIPE_CONTRACT_VERSION:
        raise OpportunityContractError(
            f"recipe.schema_version must be {RECIPE_CONTRACT_VERSION}"
        )
    _nonempty(recipe.get("recipe_id"), "recipe.recipe_id")
    _nonempty(recipe.get("version"), "recipe.version")
    _nonempty(recipe.get("score_version"), "recipe.score_version")
    if recipe.get("score_version") != SCORE_VERSION:
        raise OpportunityContractError(
            f"recipe.score_version must be {SCORE_VERSION}"
        )

    score = recipe.get("score")
    if not isinstance(score, Mapping):
        raise OpportunityContractError("recipe.score must be an object")
    for key in ("base", "minimum", "maximum"):
        if not isinstance(score.get(key), int):
            raise OpportunityContractError(f"recipe.score.{key} must be an integer")
    if not score["minimum"] <= score["base"] <= score["maximum"]:
        raise OpportunityContractError("recipe.score base must be inside its bounds")

    aliases = recipe.get("asset_aliases")
    if not isinstance(aliases, Mapping):
        raise OpportunityContractError("recipe.asset_aliases must be an object")
    for alias, asset_class in aliases.items():
        _nonempty(alias, "recipe.asset_aliases key")
        if asset_class not in ASSET_CLASSES:
            raise OpportunityContractError(
                f"recipe.asset_aliases.{alias} names unknown class {asset_class!r}"
            )

    rules = _list(recipe.get("signal_rules"), "recipe.signal_rules")
    seen: set[str] = set()
    for index, rule in enumerate(rules):
        path = f"recipe.signal_rules[{index}]"
        if not isinstance(rule, Mapping):
            raise OpportunityContractError(f"{path} must be an object")
        signal_type = _nonempty(rule.get("signal_type"), f"{path}.signal_type")
        _nonempty(rule.get("label"), f"{path}.label")
        _nonempty(rule.get("category"), f"{path}.category")
        if signal_type in seen:
            raise OpportunityContractError(
                f"recipe.signal_rules repeats signal_type {signal_type!r}"
            )
        seen.add(signal_type)
        for key in ("points_if_present", "points_if_absent", "confidence_weight"):
            if not isinstance(rule.get(key), int):
                raise OpportunityContractError(f"{path}.{key} must be an integer")
        if rule["confidence_weight"] <= 0:
            raise OpportunityContractError(f"{path}.confidence_weight must be > 0")
        if not isinstance(rule.get("material"), bool):
            raise OpportunityContractError(f"{path}.material must be boolean")
        sources = _list(rule.get("next_sources", []), f"{path}.next_sources")
        for source_index, source in enumerate(sources):
            source_path = f"{path}.next_sources[{source_index}]"
            if not isinstance(source, Mapping):
                raise OpportunityContractError(f"{source_path} must be an object")
            _nonempty(source.get("source_id"), f"{source_path}.source_id")
            _nonempty(
                source.get("expected_information"),
                f"{source_path}.expected_information",
            )
            if source.get("mode") not in {"AUTOMATIC", "MANUAL"}:
                raise OpportunityContractError(
                    f"{source_path}.mode must be AUTOMATIC or MANUAL"
                )

    confidence = recipe.get("confidence")
    if not isinstance(confidence, Mapping):
        raise OpportunityContractError("recipe.confidence must be an object")
    _nonempty(confidence.get("formula"), "recipe.confidence.formula")
    credits = confidence.get("credits")
    if not isinstance(credits, Mapping) or set(credits) != set(CONFIDENCE_LEVELS):
        raise OpportunityContractError(
            "recipe.confidence.credits must cover every confidence level"
        )
    for level, credit in credits.items():
        if (
            isinstance(credit, bool)
            or not isinstance(credit, (int, float))
            or not 0 <= credit <= 1
        ):
            raise OpportunityContractError(
                f"recipe.confidence.credits.{level} must be between 0 and 1"
            )
    conflicted_credit = confidence.get("conflicted_credit")
    if (
        isinstance(conflicted_credit, bool)
        or not isinstance(conflicted_credit, (int, float))
        or not 0 <= conflicted_credit <= 1
    ):
        raise OpportunityContractError(
            "recipe.confidence.conflicted_credit must be between 0 and 1"
        )
    readiness_weight = confidence.get("readiness_fact_weight")
    if not isinstance(readiness_weight, int) or readiness_weight <= 0:
        raise OpportunityContractError(
            "recipe.confidence.readiness_fact_weight must be a positive integer"
        )

    readiness = recipe.get("readiness")
    if not isinstance(readiness, Mapping):
        raise OpportunityContractError("recipe.readiness must be an object")
    for axis in ("authority", "contact", "deal", "offer"):
        facts = _list(readiness.get(axis), f"recipe.readiness.{axis}")
        if not facts:
            raise OpportunityContractError(f"recipe.readiness.{axis} cannot be empty")
        for fact in facts:
            _nonempty(fact, f"recipe.readiness.{axis} fact")

    action_rules = _list(recipe.get("action_rules"), "recipe.action_rules")
    if not action_rules:
        raise OpportunityContractError("recipe.action_rules cannot be empty")
    seen_action_facts: set[str] = set()
    for index, rule in enumerate(action_rules):
        path = f"recipe.action_rules[{index}]"
        if not isinstance(rule, Mapping):
            raise OpportunityContractError(f"{path} must be an object")
        fact = _nonempty(rule.get("fact"), f"{path}.fact")
        if fact in seen_action_facts:
            raise OpportunityContractError(f"recipe action fact repeated: {fact}")
        seen_action_facts.add(fact)
        _nonempty(rule.get("action"), f"{path}.action")
        _nonempty(rule.get("route"), f"{path}.route")
        states = _list(rule.get("when"), f"{path}.when")
        if not states or not set(states) <= set(READINESS_FACT_STATES):
            raise OpportunityContractError(f"{path}.when contains an unknown state")

    fact_sources = recipe.get("fact_sources")
    if not isinstance(fact_sources, Mapping):
        raise OpportunityContractError("recipe.fact_sources must be an object")
    for fact, sources in fact_sources.items():
        _nonempty(fact, "recipe.fact_sources key")
        for source_index, source in enumerate(
            _list(sources, f"recipe.fact_sources.{fact}")
        ):
            source_path = f"recipe.fact_sources.{fact}[{source_index}]"
            if not isinstance(source, Mapping):
                raise OpportunityContractError(f"{source_path} must be an object")
            _nonempty(source.get("source_id"), f"{source_path}.source_id")
            _nonempty(
                source.get("expected_information"),
                f"{source_path}.expected_information",
            )
            if source.get("mode") not in {"AUTOMATIC", "MANUAL"}:
                raise OpportunityContractError(
                    f"{source_path}.mode must be AUTOMATIC or MANUAL"
                )

    bands = _list(recipe.get("priority_bands"), "recipe.priority_bands")
    if not bands:
        raise OpportunityContractError("recipe.priority_bands cannot be empty")
    for index, band in enumerate(bands):
        path = f"recipe.priority_bands[{index}]"
        if not isinstance(band, Mapping):
            raise OpportunityContractError(f"{path} must be an object")
        for key in ("min_score", "min_confidence"):
            if not isinstance(band.get(key), int):
                raise OpportunityContractError(f"{path}.{key} must be an integer")
            if not 0 <= band[key] <= 100:
                raise OpportunityContractError(f"{path}.{key} must be between 0 and 100")
        _nonempty(band.get("priority"), f"{path}.priority")

    claimed = _nonempty(recipe.get("config_digest"), "recipe.config_digest")
    observed = recipe_config_digest(recipe)
    if claimed != observed:
        raise OpportunityContractError(
            f"recipe.config_digest mismatch: claimed {claimed}, observed {observed}"
        )
    return recipe


def validate_input(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate the portable request while preserving every caller-owned field."""
    if not isinstance(payload, Mapping):
        raise OpportunityContractError("input must be an object")
    if payload.get("schema_version") != INPUT_CONTRACT_VERSION:
        raise OpportunityContractError(
            f"input.schema_version must be {INPUT_CONTRACT_VERSION}"
        )
    for field in (
        "as_of", "recipe", "subject", "asset", "signals",
        "property_population", "readiness", "research_attempts", "lineage",
    ):
        if field not in payload:
            raise OpportunityContractError(f"input.{field} is required")
    _nonempty(payload.get("as_of"), "input.as_of")
    subject = payload.get("subject")
    if not isinstance(subject, Mapping):
        raise OpportunityContractError("input.subject must be an object")
    _nonempty(subject.get("subject_id"), "input.subject.subject_id")
    validate_recipe(payload.get("recipe"))

    asset = payload.get("asset", {})
    if not isinstance(asset, Mapping):
        raise OpportunityContractError("input.asset must be an object")

    signals = _list(payload.get("signals", []), "input.signals")
    seen_signal_ids: set[str] = set()
    for index, signal in enumerate(signals):
        path = f"input.signals[{index}]"
        if not isinstance(signal, Mapping):
            raise OpportunityContractError(f"{path} must be an object")
        signal_id = _nonempty(signal.get("signal_id"), f"{path}.signal_id")
        if signal_id in seen_signal_ids:
            raise OpportunityContractError(f"duplicate signal_id {signal_id!r}")
        seen_signal_ids.add(signal_id)
        _nonempty(signal.get("signal_type"), f"{path}.signal_type")
        if signal.get("state") not in SIGNAL_STATES:
            raise OpportunityContractError(f"{path}.state is not recognized")
        if signal.get("confidence") not in CONFIDENCE_LEVELS:
            raise OpportunityContractError(f"{path}.confidence is not recognized")
        for field_name in ("evidence_refs", "source_families"):
            values = _list(signal.get(field_name, []), f"{path}.{field_name}")
            for value in values:
                _nonempty(value, f"{path}.{field_name} item")

    validate_property_population(
        payload.get("property_population"), signals=signals, subject=subject
    )

    readiness = payload.get("readiness", {})
    if not isinstance(readiness, Mapping):
        raise OpportunityContractError("input.readiness must be an object")
    for fact, state in readiness.items():
        _nonempty(fact, "input.readiness key")
        if state not in READINESS_FACT_STATES:
            raise OpportunityContractError(
                f"input.readiness.{fact} must be YES, NO, or UNKNOWN"
            )

    attempts = _list(payload.get("research_attempts", []), "input.research_attempts")
    for index, attempt in enumerate(attempts):
        path = f"input.research_attempts[{index}]"
        if not isinstance(attempt, Mapping):
            raise OpportunityContractError(f"{path} must be an object")
        _nonempty(attempt.get("source_id"), f"{path}.source_id")
        state = _nonempty(attempt.get("result_state"), f"{path}.result_state")
        if generic_research_result_state(state) is None:
            raise OpportunityContractError(
                f"{path}.result_state {state!r} is not in the T50 contract"
            )
        if "evidence_refs" in attempt:
            for value in _list(attempt["evidence_refs"], f"{path}.evidence_refs"):
                _nonempty(value, f"{path}.evidence_refs item")

    lineage = payload.get("lineage", {})
    if not isinstance(lineage, Mapping):
        raise OpportunityContractError("input.lineage must be an object")
    for field_name in LINEAGE_LIST_FIELDS:
        values = _list(lineage.get(field_name, []), f"input.lineage.{field_name}")
        for value in values:
            _nonempty(value, f"input.lineage.{field_name} item")
    for field_name, values in lineage.items():
        if field_name in LINEAGE_LIST_FIELDS:
            continue
        for value in _list(values, f"input.lineage.{field_name}"):
            _nonempty(value, f"input.lineage.{field_name} item")

    if "extensions" in payload and not isinstance(payload["extensions"], Mapping):
        raise OpportunityContractError("input.extensions must be an object")
    if "resolution_context" in payload and not isinstance(
        payload["resolution_context"], Mapping
    ):
        raise OpportunityContractError("input.resolution_context must be an object")
    return payload


@dataclass(frozen=True)
class FixtureCase:
    """A named, non-live generalization court input and its expected spine."""

    fixture_id: str
    payload: Mapping[str, Any]
    expected_asset_class: str
    expected_next_action: str
    expected_score: int

    def __post_init__(self) -> None:
        _nonempty(self.fixture_id, "fixture_id")
        if self.expected_asset_class not in ASSET_CLASSES:
            raise OpportunityContractError(
                f"fixture {self.fixture_id} expects unknown asset class"
            )
        validate_input(self.payload)
