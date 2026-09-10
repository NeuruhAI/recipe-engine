"""Score vector and rank for every (opportunity, recipe) membership under a strategy.

The NC curative ranker (``scripts/rank_nc_curative_raw.py``) computes ONE
additive ``curative_priority_score`` per property and ranks it only under the
property's primary recipe.  Since multi-membership emission a property can
satisfy several recipes, and every secondary membership arrives unscored and
unranked.  The score itself is a single number: the receipts list the factors
that fired, but not which dimension they measure, which inputs were UNKNOWN,
what argues against the opportunity, or what the economics rest on.  And a
blank input reads as ``False`` (``truthy(None) is False``), so "we do not know
the foreclosure status" and "there is no foreclosure" score the same and look
the same.

This module is the missing seam between membership and ranking:

* ``score_membership`` evaluates ONE membership (opportunity row + recipe id)
  under ONE strategy and returns its score vector, evidence, counter-evidence
  and economics.  Inputs are tri-state: a blank indicator is UNKNOWN, a factor
  that could still fire on an UNKNOWN input is UNDETERMINED, and a dimension
  with no known input has no value (never 0).
* ``rank`` orders a universe of scored memberships under the strategy and
  answers ``rank(opportunity, recipe, strategy)`` for each: global rank of the
  opportunity, rank within the recipe, cohort size, universe id.
* A strategy is a versioned, sha-pinned policy document
  (``strategies/<strategy_id>.json``): factor deltas, optional per-recipe
  deltas, cap, tiers and tie-break order.  Factor DETECTION is canonical code
  (the ranker's predicates and thresholds); a strategy only weighs facts, it
  cannot invent one.

The existing ranking receipts are the proof.  Under the canonical strategy
(``nc_curative_rank.v3``) every membership whose row carries a received
``curative_priority_score`` / ``score_reasons`` / ``trigger_count`` is checked
against them and refused on any drift, and ranking the primary memberships
alone reproduces the received ``rank``, ``recipe_rank``,
``recipe_membership_count`` and ``ranking_universe_id`` (the parity court runs
on the shuffled real 20,734-row ranking).

Nothing here writes, contacts, spends, or authorizes.  Contactability,
liquidity and evidence confidence are owned elsewhere and stay UNWIRED.
Receipt hashes use the Lead Recipe OS consumer canon (``content_hash``);
``ranking_universe_id`` keeps the ranker's own digest so it stays comparable.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from recipe_engine.closed_loop.identity import digest

from .contracts import OpportunityContractError
from .eligibility import content_hash
from .oppack_receipts import DEFAULT_RECIPE_VERSION, build_why_this_matched, build_why_this_ranked


STRATEGY_CONTRACT = "neuruh.recipe-engine.scoring-strategy.v1"
RECEIPT_CONTRACT = "neuruh.recipe-engine.membership-score.v1"
SCORE_VECTOR_CONTRACT = "neuruh.lead-recipe-os.score-vector.v1"

PRODUCER_OWNER = "recipe-engine"
PRODUCER_IDENTITY = "recipe_engine.opportunity_intelligence.membership_scoring"
PRODUCER_VERSION = "recipe-engine/membership-scoring@1.0.0"

CANONICAL_STRATEGY_ID = "nc_curative_rank.v3"
STRATEGY_DIR = Path(__file__).resolve().parent / "strategies"

#: strategy_id -> sha256 of its document.  A strategy id is immutable: a
#: changed weight is a new id, never an edit.  Loading a drifted document fails.
STRATEGY_REGISTRY: Mapping[str, str] = {
    CANONICAL_STRATEGY_ID: "1226adff8407071902006e0089086bb9a9dfbb84b372595e83ba3a9d2d055171",
}


class ScoringError(OpportunityContractError):
    """A strategy, membership or received receipt cannot be trusted as stated."""


class ReceiptDriftError(ScoringError):
    """The canonical strategy disagrees with a receipt the ranker already emitted."""


# --------------------------------------------------------------------------
# tri-state inputs
# --------------------------------------------------------------------------

KNOWN = "KNOWN"
UNKNOWN = "UNKNOWN"

FIRED = "FIRED"
NOT_FIRED = "NOT_FIRED"
UNDETERMINED = "UNDETERMINED"

_TRUE = frozenset({"1", "true", "yes", "y"})  # scripts/rank_nc_curative_raw.py::truthy
_FALSE = frozenset({"0", "false", "no", "n"})

FLAG_INPUTS = (
    "tax_foreclosure_indicator",
    "heir_indicator",
    "fragmented_ownership_indicator",
    "out_of_state",
)
NUMERIC_INPUTS = ("years_owned", "assessed_value", "tax_balance", "assessed_value_less_tax")
MATERIAL_INPUTS = ("source_types",) + FLAG_INPUTS + NUMERIC_INPUTS


def _flag(value: Any) -> dict[str, Any]:
    """True / False only when the input says so.  Blank or unparseable is UNKNOWN."""
    if isinstance(value, bool):
        return {"state": KNOWN, "value": value}
    text = str(value if value is not None else "").strip().lower()
    if text in _TRUE:
        return {"state": KNOWN, "value": True}
    if text in _FALSE:
        return {"state": KNOWN, "value": False}
    return {"state": UNKNOWN, "value": None}


def _number(value: Any, *, integral: bool = False) -> dict[str, Any]:
    """The ranker's ``num`` / ``integer`` parse, with non-finite and blank as UNKNOWN."""
    if value in (None, ""):
        return {"state": UNKNOWN, "value": None}
    try:
        number = float(value)
    except (TypeError, ValueError):
        return {"state": UNKNOWN, "value": None}
    if not math.isfinite(number):
        return {"state": UNKNOWN, "value": None}
    if integral:
        return {"state": KNOWN, "value": int(number)}
    return {"state": KNOWN, "value": int(number) if number.is_integer() else number}


def _source_types(value: Any) -> dict[str, Any]:
    types = sorted({part for part in str(value or "").split("|") if part})
    if not types:
        return {"state": UNKNOWN, "value": None}
    return {"state": KNOWN, "value": types}


def read_inputs(row: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Every material input of one opportunity row, each KNOWN with a value or UNKNOWN."""
    inputs: dict[str, dict[str, Any]] = {"source_types": _source_types(row.get("source_types"))}
    for name in FLAG_INPUTS:
        inputs[name] = _flag(row.get(name))
    inputs["years_owned"] = _number(row.get("years_owned"), integral=True)
    for name in ("assessed_value", "tax_balance", "assessed_value_less_tax"):
        inputs[name] = _number(row.get(name))
    return inputs


# --------------------------------------------------------------------------
# canonical factor detection (the ranker's predicates, tri-state)
# --------------------------------------------------------------------------

#: factor_id -> score-vector dimension.  Order is the ranker's reason order.
FACTOR_DIMENSION: Mapping[str, str] = {
    "published_tax_distress": "urgency",
    "active_tax_foreclosure": "urgency",
    "heir_or_estate_signal": "signal_strength",
    "ownership_fragmentation_proxy": "signal_strength",
    "out_of_state_owner": "signal_strength",
    "ownership_20_plus_years": "signal_strength",
    "ownership_10_plus_years": "signal_strength",
    "assessed_value_250k_plus_proxy": "economic_value",
    "assessed_value_150k_plus_proxy": "economic_value",
    "assessed_value_75k_plus_proxy": "economic_value",
    "tax_balance_10k_plus": "economic_value",
    "tax_balance_5k_plus": "economic_value",
    "tax_balance_1k_plus": "economic_value",
    "multi_trigger_3_plus": "signal_strength",
}

#: First-match bands: (group id, dimension, input, ((factor_id, floor), ...)).
_BANDS: tuple[tuple[str, str, str, tuple[tuple[str, float], ...]], ...] = (
    ("ownership_tenure", "signal_strength", "years_owned",
     (("ownership_20_plus_years", 20), ("ownership_10_plus_years", 10))),
    ("assessed_value_proxy", "economic_value", "assessed_value",
     (("assessed_value_250k_plus_proxy", 250000), ("assessed_value_150k_plus_proxy", 150000),
      ("assessed_value_75k_plus_proxy", 75000))),
    ("tax_balance", "economic_value", "tax_balance",
     (("tax_balance_10k_plus", 10000), ("tax_balance_5k_plus", 5000), ("tax_balance_1k_plus", 1000))),
)

#: The ranker's trigger classes and the source family each is derived from
#: (``scripts/rank_nc_curative_raw_v3.py::TRIGGER_FAMILY``, asserted equal).
TRIGGER_FAMILY: Mapping[str, str] = {
    "tax_distress": "county_tax_collector_publication",
    "court_action": "county_foreclosure_docket_publication",
    "heir_or_estate": "assessor_parcel_roll",
    "ownership_fragmentation": "assessor_parcel_roll",
    "absentee_or_out_of_state": "assessor_parcel_roll",
    "long_term_ownership": "assessor_parcel_roll",
    "high_value_property": "assessor_parcel_roll",
}

_FACTOR_FAMILY: Mapping[str, str] = {
    "published_tax_distress": TRIGGER_FAMILY["tax_distress"],
    "active_tax_foreclosure": TRIGGER_FAMILY["court_action"],
    "heir_or_estate_signal": TRIGGER_FAMILY["heir_or_estate"],
    "ownership_fragmentation_proxy": TRIGGER_FAMILY["ownership_fragmentation"],
    "out_of_state_owner": TRIGGER_FAMILY["absentee_or_out_of_state"],
    "ownership_tenure": TRIGGER_FAMILY["long_term_ownership"],
    "assessed_value_proxy": TRIGGER_FAMILY["high_value_property"],
    "tax_balance": TRIGGER_FAMILY["tax_distress"],
}


def _has(inputs: Mapping[str, Mapping[str, Any]], source_type: str) -> bool:
    types = inputs["source_types"]
    return types["state"] == KNOWN and source_type in types["value"]


def _is_true(inputs: Mapping[str, Mapping[str, Any]], name: str) -> bool:
    return inputs[name]["state"] == KNOWN and inputs[name]["value"] is True


def _flag_factor(factor_id: str, inputs: Mapping[str, Mapping[str, Any]], name: str) -> dict[str, Any]:
    fact = inputs[name]
    state = FIRED if fact["value"] is True else NOT_FIRED if fact["state"] == KNOWN else UNDETERMINED
    return {"factor_id": factor_id, "state": state, "inputs": {name: dict(fact)}}


def _trigger_states(inputs: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """``scripts/rank_nc_curative_raw.py::triggers`` with UNKNOWN kept apart from False."""

    def band(name: str, floor: float) -> str:
        fact = inputs[name]
        if fact["state"] != KNOWN:
            return UNDETERMINED
        return FIRED if fact["value"] >= floor else NOT_FIRED

    def flag(name: str) -> str:
        return _flag_factor("", inputs, name)["state"]

    court = FIRED if (_is_true(inputs, "tax_foreclosure_indicator") or _has(inputs, "tax_foreclosure_case")) else (
        NOT_FIRED if inputs["tax_foreclosure_indicator"]["state"] == KNOWN and inputs["source_types"]["state"] == KNOWN
        else UNDETERMINED
    )
    tax = UNDETERMINED if inputs["source_types"]["state"] != KNOWN else (
        FIRED if _has(inputs, "taxdelinquent") else NOT_FIRED
    )
    return {
        "tax_distress": tax,
        "court_action": court,
        "heir_or_estate": flag("heir_indicator"),
        "ownership_fragmentation": flag("fragmented_ownership_indicator"),
        "absentee_or_out_of_state": flag("out_of_state"),
        "long_term_ownership": band("years_owned", 20),
        "high_value_property": band("assessed_value", 150000),
    }


def evaluate_factors(inputs: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every canonical factor as FIRED / NOT_FIRED / UNDETERMINED, in the ranker's order."""
    records: list[dict[str, Any]] = []
    triggers = _trigger_states(inputs)

    records.append({
        "factor_id": "published_tax_distress",
        "state": triggers["tax_distress"],
        "inputs": {"source_types": dict(inputs["source_types"])},
    })
    records.append({
        "factor_id": "active_tax_foreclosure",
        "state": triggers["court_action"],
        "inputs": {
            "tax_foreclosure_indicator": dict(inputs["tax_foreclosure_indicator"]),
            "source_types": dict(inputs["source_types"]),
        },
    })
    records.append(_flag_factor("heir_or_estate_signal", inputs, "heir_indicator"))
    records.append(_flag_factor("ownership_fragmentation_proxy", inputs, "fragmented_ownership_indicator"))
    records.append(_flag_factor("out_of_state_owner", inputs, "out_of_state"))

    for group, _dimension, name, bands in _BANDS:
        fact = inputs[name]
        record: dict[str, Any] = {"factor_id": group, "state": UNDETERMINED, "inputs": {name: dict(fact)}}
        if fact["state"] == KNOWN:
            record["state"] = NOT_FIRED
            for factor_id, floor in bands:
                if fact["value"] >= floor:
                    record["factor_id"] = factor_id
                    record["state"] = FIRED
                    break
        record["group"] = group
        records.append(record)

    fired = sorted(t for t, s in triggers.items() if s == FIRED)
    undetermined = sorted(t for t, s in triggers.items() if s == UNDETERMINED)
    if len(fired) >= 3:
        multi = FIRED
    elif len(fired) + len(undetermined) >= 3:
        multi = UNDETERMINED
    else:
        multi = NOT_FIRED
    records.append({
        "factor_id": "multi_trigger_3_plus",
        "state": multi,
        "inputs": {"triggers_fired": fired, "triggers_undetermined": undetermined},
    })
    return records


def _record_dimension(record: Mapping[str, Any]) -> str:
    group = record.get("group")
    if group:
        return next(dim for g, dim, _name, _bands in _BANDS if g == group)
    return FACTOR_DIMENSION[record["factor_id"]]


def _record_family(record: Mapping[str, Any]) -> list[str]:
    if record["factor_id"] == "multi_trigger_3_plus":
        return sorted({TRIGGER_FAMILY[t] for t in record["inputs"]["triggers_fired"]})
    return [_FACTOR_FAMILY[record.get("group") or record["factor_id"]]]


def _record_unknown_inputs(record: Mapping[str, Any]) -> list[str]:
    if record["factor_id"] == "multi_trigger_3_plus":
        return []
    return sorted(name for name, fact in record["inputs"].items() if fact["state"] == UNKNOWN)


# --------------------------------------------------------------------------
# strategy documents
# --------------------------------------------------------------------------

TIE_BREAK_KEYS = (
    "composite_desc",
    "trigger_count_desc",
    "assessed_value_less_tax_desc_unknown_last",
    "assessed_value_desc_unknown_last",
    "tax_balance_desc_unknown_last",
    "county_fips_asc",
    "parcel_apn_asc",
)

_STRATEGY_KEYS = frozenset({
    "schema_version", "strategy_id", "description", "score_name", "score_semantics",
    "recipe_version", "cap", "factor_deltas", "recipe_factor_deltas", "tiers",
    "tie_break", "provenance",
})


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Strategy:
    strategy_id: str
    document: Mapping[str, Any]
    sha256: str

    @property
    def cap(self) -> int:
        return int(self.document["cap"])

    @property
    def recipe_version(self) -> str:
        return str(self.document["recipe_version"])

    @property
    def recipe_conditioned(self) -> bool:
        return bool(self.document["recipe_factor_deltas"])

    def delta(self, factor_id: str, recipe_id: str) -> int:
        override = self.document["recipe_factor_deltas"].get(recipe_id, {})
        if factor_id in override:
            return int(override[factor_id])
        return int(self.document["factor_deltas"][factor_id])

    def tier(self, composite: int) -> str:
        for band in self.document["tiers"]:
            if band["min"] is None or composite >= band["min"]:
                return str(band["tier"])
        raise ScoringError(f"strategy {self.strategy_id} has no tier for {composite}")


def validate_strategy(document: Mapping[str, Any]) -> None:
    """Refuse a strategy that could invent a fact, a float score or an untiered value."""
    if not isinstance(document, Mapping):
        raise ScoringError("strategy must be an object")
    unknown = set(document) - _STRATEGY_KEYS
    missing = _STRATEGY_KEYS - {"description", "provenance"} - set(document)
    if unknown or missing:
        raise ScoringError(f"strategy keys: unknown={sorted(unknown)} missing={sorted(missing)}")
    if document["schema_version"] != STRATEGY_CONTRACT:
        raise ScoringError(f"strategy schema_version must be {STRATEGY_CONTRACT}")

    def integer(value: Any, path: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ScoringError(f"{path} must be an integer")

    integer(document["cap"], "cap")
    deltas = document["factor_deltas"]
    if set(deltas) != set(FACTOR_DIMENSION):
        raise ScoringError(
            "factor_deltas must weigh exactly the canonical factors "
            f"(missing={sorted(set(FACTOR_DIMENSION) - set(deltas))}, "
            f"unknown={sorted(set(deltas) - set(FACTOR_DIMENSION))})"
        )
    for factor_id, delta in deltas.items():
        integer(delta, f"factor_deltas.{factor_id}")
    for recipe_id, override in document["recipe_factor_deltas"].items():
        if not isinstance(override, Mapping) or not override:
            raise ScoringError(f"recipe_factor_deltas.{recipe_id} must be a non-empty object")
        for factor_id, delta in override.items():
            if factor_id not in FACTOR_DIMENSION:
                raise ScoringError(f"recipe_factor_deltas.{recipe_id}.{factor_id} is not a canonical factor")
            integer(delta, f"recipe_factor_deltas.{recipe_id}.{factor_id}")
    tiers = document["tiers"]
    if not tiers or tiers[-1].get("min") is not None:
        raise ScoringError("tiers must end with a floorless tier")
    floors = [band["min"] for band in tiers[:-1]]
    for floor in floors:
        integer(floor, "tiers[].min")
    if floors != sorted(floors, reverse=True):
        raise ScoringError("tier floors must descend")
    tie_break = list(document["tie_break"])
    if not tie_break or tie_break[0] != "composite_desc":
        raise ScoringError("tie_break must lead with composite_desc")
    if len(set(tie_break)) != len(tie_break) or set(tie_break) - set(TIE_BREAK_KEYS):
        raise ScoringError(f"tie_break keys must be distinct members of {TIE_BREAK_KEYS}")


def build_strategy(document: Mapping[str, Any]) -> Strategy:
    """An unregistered strategy, e.g. one under review.  Never canonical."""
    validate_strategy(document)
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    strategy_id = str(document["strategy_id"])
    sha = _sha256_bytes(raw)
    if strategy_id in STRATEGY_REGISTRY and STRATEGY_REGISTRY[strategy_id] != sha:
        raise ScoringError(f"strategy id {strategy_id} is registered to a different document")
    return Strategy(strategy_id, document, sha)


def load_strategy(strategy_id: str = CANONICAL_STRATEGY_ID) -> Strategy:
    """A registered strategy, verified against its pinned sha256."""
    expected = STRATEGY_REGISTRY.get(strategy_id)
    if expected is None:
        raise ScoringError(f"strategy {strategy_id!r} is not registered")
    path = STRATEGY_DIR / f"{strategy_id}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("strategy_id") != strategy_id:
        raise ScoringError(f"{path.name} declares strategy_id {document.get('strategy_id')!r}")
    strategy = build_strategy(document)
    if strategy.sha256 != expected:
        raise ScoringError(f"strategy {strategy_id} drifted: {strategy.sha256} != pinned {expected}")
    return strategy


# --------------------------------------------------------------------------
# score one membership
# --------------------------------------------------------------------------

#: The ten dimensions of RECIPE_SCORE_VECTOR_COVERAGE.csv and who owns the
#: ones this module cannot evaluate.  Order is the census order.
SCORE_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("signal_strength", "recipe_engine"),
    ("strategic_fit", "recipe_engine"),
    ("urgency", "recipe_engine_plus_process_clock"),
    ("downside_risk", "recipe_engine_plus_governance"),
    ("evidence_confidence", "landos_engine"),
    ("source_independence_diversity", "landos_ingest_plus_landos_engine"),
    ("data_completeness", "landos_ingest_plus_deedsonar"),
    ("economic_value", "landos_engine"),
    ("contactability", "contact_intelligence_plus_governance"),
    ("liquidity", "outcome_observer_plus_deedsonar"),
)
_FACTOR_DIMENSIONS = ("signal_strength", "urgency", "economic_value")
_UNWIRED_DIMENSIONS = {
    "evidence_confidence": "LandOS evaluator contract; not re-implemented in Recipe Engine.",
    "contactability": "Contact authorization is never inferred from phone presence; owned by contact intelligence + governance.",
    "liquidity": "No canonical liquidity runtime; owned by the outcome observer.",
}
_NOT_EVALUATED_DIMENSIONS = {
    "strategic_fit": "Membership is asserted by the why-matched receipt; this strategy puts no weight on recipe fit.",
    "downside_risk": "Title-conflict and disqualifier sources are not wired; contradictions are listed as counter-evidence instead.",
}
DISQUALIFIERS = ("government_owned_asset", "contact_without_permission", "source_rights_prohibit_automation")


def _opportunity_key(row: Mapping[str, Any]) -> str:
    fips = str(row.get("county_fips") or "").strip()
    apn = str(row.get("parcel_apn") or "").strip()
    if not fips or not apn:
        raise ScoringError("an opportunity needs county_fips and parcel_apn")
    return f"{fips}|{apn}"


def _sort_numeric(fact: Mapping[str, Any]) -> tuple[int, float]:
    """``scripts/rank_nc_curative_raw.py::_sort_numeric_key``: UNKNOWN sorts after any known value."""
    if fact["state"] != KNOWN:
        return 1, 0.0
    return 0, float(fact["value"])


@dataclass
class ScoredMembership:
    """One membership's receipt body (no rank yet) plus the values it sorts on."""

    receipt: dict[str, Any]
    strategy: Strategy
    composite: int
    trigger_count: int
    sort_numeric: Mapping[str, tuple[int, float]] = field(default_factory=dict)

    @property
    def opportunity_key(self) -> str:
        return self.receipt["opportunity"]["opportunity_key"]

    @property
    def recipe_id(self) -> str:
        return self.receipt["recipe"]["recipe_id"]


def _received_contributions(row: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    text = str(row.get("score_reasons") or "").strip()
    if not text and "score_reasons" not in row:
        return None
    out: list[dict[str, Any]] = []
    for token in (part for part in text.split("|") if part):
        factor_id, _, delta = token.rpartition(":")
        out.append({"factor_id": factor_id, "delta": int(delta)})
    return out


def _check_received(row: Mapping[str, Any], *, composite: int, contributions: list[dict[str, Any]],
                    trigger_count: int, tier: str) -> str:
    """Canonical strategy only: the ranker's own receipts must agree, or scoring stops."""
    received_score = row.get("curative_priority_score")
    received = _received_contributions(row)
    if received_score in (None, "") and received is None:
        return "NO_RECEIVED_RECEIPT"
    key = row.get("county_fips"), row.get("parcel_apn")
    if received_score not in (None, "") and int(float(received_score)) != composite:
        raise ReceiptDriftError(f"{key}: received score {received_score} != {composite}")
    if received is not None and received != contributions:
        raise ReceiptDriftError(f"{key}: received score_reasons {received} != {contributions}")
    if row.get("trigger_count") not in (None, "") and int(float(row["trigger_count"])) != trigger_count:
        raise ReceiptDriftError(f"{key}: received trigger_count {row['trigger_count']} != {trigger_count}")
    if row.get("priority_tier") not in (None, "") and row["priority_tier"] != tier:
        raise ReceiptDriftError(f"{key}: received priority_tier {row['priority_tier']} != {tier}")
    return "EQUAL"


def _sha_text(text: str) -> str | None:
    text = text.strip()
    return _sha256_bytes(text.encode("utf-8")) if text else None


def score_membership(
    row: Mapping[str, Any],
    recipe_id: str,
    *,
    strategy: Strategy,
    is_primary: bool | None = None,
    match_rank: int | None = None,
    recipe_version: str | None = None,
    why_matched_receipt: str | None = None,
) -> ScoredMembership:
    """Score vector, evidence, counter-evidence and economics for ONE (opportunity, recipe)."""
    recipe_id = str(recipe_id or "").strip()
    if not recipe_id:
        raise ScoringError("a membership needs a recipe_id")
    opportunity_key = _opportunity_key(row)
    inputs = read_inputs(row)
    records = evaluate_factors(inputs)

    evidence: list[dict[str, Any]] = []
    contributions: list[dict[str, Any]] = []
    dimension_values: dict[str, int] = {dim: 0 for dim in _FACTOR_DIMENSIONS}
    dimension_records: dict[str, list[dict[str, Any]]] = {dim: [] for dim in _FACTOR_DIMENSIONS}
    for record in records:
        dimension = _record_dimension(record)
        dimension_records[dimension].append(record)
        if record["state"] != FIRED:
            continue
        delta = strategy.delta(record["factor_id"], recipe_id)
        dimension_values[dimension] += delta
        contributions.append({"factor_id": record["factor_id"], "delta": delta})
        evidence.append({
            "factor_id": record["factor_id"],
            "dimension": dimension,
            "delta": delta,
            "inputs": record["inputs"],
            "source_families": _record_family(record),
        })

    uncapped = sum(dimension_values.values())
    composite = min(uncapped, strategy.cap)
    tier = strategy.tier(composite)
    trigger_count = len(next(r for r in records if r["factor_id"] == "multi_trigger_3_plus")["inputs"]["triggers_fired"])

    components: list[dict[str, Any]] = []
    known_inputs = sum(1 for name in MATERIAL_INPUTS if inputs[name]["state"] == KNOWN)
    for dimension, owner in SCORE_DIMENSIONS:
        component: dict[str, Any] = {"name": dimension, "coverage_owner": owner}
        if dimension in _FACTOR_DIMENSIONS:
            dim_records = dimension_records[dimension]
            unknown_inputs = sorted({name for r in dim_records for name in _record_unknown_inputs(r)})
            dim_inputs = {name for r in dim_records for name in r["inputs"] if r["factor_id"] != "multi_trigger_3_plus"}
            # A cross-dimension factor (multi_trigger) can fire on other dimensions'
            # inputs, so "no known input" only means UNKNOWN when nothing fired here.
            all_unknown = (
                bool(dim_inputs)
                and all(inputs[name]["state"] == UNKNOWN for name in dim_inputs)
                and not any(r["state"] == FIRED for r in dim_records)
            )
            block = {"dimension": dimension, "strategy_sha256": strategy.sha256, "factors": dim_records}
            component.update({
                "organ": PRODUCER_OWNER,
                "evaluation_state": "UNKNOWN" if all_unknown else "EVALUATED",
                "value": None if all_unknown else dimension_values[dimension],
                "in_composite": True,
                "factors_fired": [r["factor_id"] for r in dim_records if r["state"] == FIRED],
                "factors_undetermined": [r["factor_id"] for r in dim_records if r["state"] == UNDETERMINED],
                "unknown_inputs": unknown_inputs,
                "receipt_sha256": content_hash(block, omit=()),
            })
        elif dimension == "data_completeness":
            unknown_inputs = sorted(name for name in MATERIAL_INPUTS if inputs[name]["state"] == UNKNOWN)
            block = {"dimension": dimension, "inputs": {name: inputs[name]["state"] for name in MATERIAL_INPUTS}}
            component.update({
                "organ": PRODUCER_OWNER,
                "evaluation_state": "EVALUATED",
                "value": round(known_inputs / len(MATERIAL_INPUTS), 4),
                "in_composite": False,
                "unknown_inputs": unknown_inputs,
                "receipt_sha256": content_hash(block, omit=()),
            })
        elif dimension == "source_independence_diversity":
            count = _number(row.get("independent_source_family_count"), integral=True)
            families = sorted(part for part in str(row.get("source_families") or "").split("|") if part)
            if count["state"] == KNOWN:
                block = {"dimension": dimension, "count": count["value"], "families": families}
                component.update({
                    "organ": PRODUCER_OWNER,
                    "evaluation_state": "EVALUATED",
                    "value": count["value"],
                    "in_composite": False,
                    "source_families": families,
                    "receipt_sha256": content_hash(block, omit=()),
                })
            else:
                component.update({
                    "organ": PRODUCER_OWNER,
                    "evaluation_state": "NOT_EVALUATED",
                    "value": None,
                    "in_composite": False,
                    "reason": "Row carries no independent_source_family_count (ranker v3 annotation).",
                })
        elif dimension in _UNWIRED_DIMENSIONS:
            component.update({
                "organ": owner,
                "evaluation_state": "UNWIRED",
                "value": None,
                "in_composite": False,
                "reason": _UNWIRED_DIMENSIONS[dimension],
            })
        else:
            component.update({
                "organ": PRODUCER_OWNER,
                "evaluation_state": "NOT_EVALUATED",
                "value": None,
                "in_composite": False,
                "reason": _NOT_EVALUATED_DIMENSIONS[dimension],
            })
        components.append(component)

    received_parity = "NOT_APPLICABLE"
    if strategy.strategy_id == CANONICAL_STRATEGY_ID:
        received_parity = _check_received(
            row, composite=composite, contributions=contributions, trigger_count=trigger_count, tier=tier
        )

    membership_row = dict(row)
    membership_row["recipe_id"] = recipe_id
    if why_matched_receipt is None or not str(why_matched_receipt).strip():
        why_matched_receipt = json.dumps(
            build_why_this_matched(membership_row), sort_keys=True, separators=(",", ":")
        )
    matched = json.loads(why_matched_receipt)
    if matched.get("recipe_id") != recipe_id:
        raise ScoringError(f"{opportunity_key}: why-matched receipt is for {matched.get('recipe_id')!r}, not {recipe_id!r}")
    ranked = build_why_this_ranked(membership_row)

    negative_evidence = [
        {"field": name, "observed": False, "meaning": "NOT_OBSERVED_IN_SOURCE_NOT_A_LEGAL_FACT"}
        for name in FLAG_INPUTS
        if inputs[name]["state"] == KNOWN and inputs[name]["value"] is False
    ]
    unknown_inputs = sorted(name for name in MATERIAL_INPUTS if inputs[name]["state"] == UNKNOWN)
    equity_status = str(row.get("equity_status") or "").strip()
    received_proxy = str(row.get("potential_equity_proxy") or "").strip() or None
    economics: dict[str, Any] = {
        "basis": "PROXY_NOT_VERIFIED_EQUITY",
        "assessed_value": dict(inputs["assessed_value"]),
        "tax_balance": dict(inputs["tax_balance"]),
        "assessed_value_less_tax": dict(inputs["assessed_value_less_tax"]),
        "tax_years": (
            {"state": KNOWN, "value": sorted({y for y in str(row.get("tax_years")).split("|") if y})}
            if str(row.get("tax_years") or "").strip() else {"state": UNKNOWN, "value": None}
        ),
        "market_value": {"state": UNKNOWN, "value": None},
        "verified_equity": {"state": UNKNOWN, "value": None, "equity_status": equity_status or None},
        "potential_equity_proxy": received_proxy,
    }
    if received_proxy == "UNKNOWN" and inputs["assessed_value"]["state"] == KNOWN:
        economics["potential_equity_proxy_note"] = (
            "The ranker reports UNKNOWN for a KNOWN assessed_value below its 75,000 floor; read assessed_value."
        )

    membership_class = None if is_primary is None else ("PRIMARY" if is_primary else "SECONDARY")
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_CONTRACT,
        "producer": {"owner": PRODUCER_OWNER, "identity": PRODUCER_IDENTITY, "version": PRODUCER_VERSION},
        "opportunity": {
            "opportunity_key": opportunity_key,
            "county_fips": str(row.get("county_fips") or "").strip(),
            "parcel_apn": str(row.get("parcel_apn") or "").strip(),
            "property_key": str(row.get("property_key") or "").strip() or None,
            "source_url": str(row.get("source_url") or "").strip() or None,
        },
        "recipe": {
            "recipe_id": recipe_id,
            "recipe_version": str(recipe_version or row.get("recipe_version") or strategy.recipe_version),
            "membership": membership_class,
            "match_rank": match_rank,
            "input_hash": matched.get("input_hash"),
            "why_matched_receipt_sha256": _sha_text(why_matched_receipt),
        },
        "strategy": {
            "strategy_id": strategy.strategy_id,
            "strategy_sha256": strategy.sha256,
            "score_name": strategy.document["score_name"],
            "score_semantics": strategy.document["score_semantics"],
            "recipe_conditioned": strategy.recipe_conditioned,
        },
        "score_vector": {"components": components},
        "composite": {
            "value": composite,
            "uncapped": uncapped,
            "cap": strategy.cap,
            "cap_applied": uncapped > strategy.cap,
            "tier": tier,
            "component_contributions": contributions,
            "undetermined_factors": [r["factor_id"] for r in records if r["state"] == UNDETERMINED],
        },
        "evidence": evidence,
        "counter_evidence": {
            "negative_evidence": negative_evidence,
            "contradictions": list(ranked.get("contradictions") or []),
            "unknown_inputs": unknown_inputs,
            "unknown_material_factors": sorted(set(ranked.get("unknown_material_factors") or [])),
            "disqualifiers": [{"disqualifier_id": d, "evaluation_state": "NOT_EVALUATED"} for d in DISQUALIFIERS],
        },
        "economics": economics,
        "received": {
            "parity": received_parity,
            "why_ranked_receipt_sha256": _sha_text(str(row.get("why_ranked_receipt") or "")),
            "ranking_universe_id": str(row.get("ranking_universe_id") or "").strip() or None,
        },
    }
    return ScoredMembership(
        receipt=receipt,
        strategy=strategy,
        composite=composite,
        trigger_count=trigger_count,
        sort_numeric={
            "assessed_value_less_tax": _sort_numeric(inputs["assessed_value_less_tax"]),
            "assessed_value": _sort_numeric(inputs["assessed_value"]),
            "tax_balance": _sort_numeric(inputs["tax_balance"]),
        },
    )


# --------------------------------------------------------------------------
# rank(opportunity, recipe, strategy)
# --------------------------------------------------------------------------

def _sort_key(item: ScoredMembership) -> tuple[Any, ...]:
    key: list[Any] = []
    opportunity = item.receipt["opportunity"]
    for name in item.strategy.document["tie_break"]:
        if name == "composite_desc":
            key.append(-item.composite)
        elif name == "trigger_count_desc":
            key.append(-item.trigger_count)
        elif name.endswith("_desc_unknown_last"):
            unknown, value = item.sort_numeric[name[: -len("_desc_unknown_last")]]
            key.extend((unknown, -value))
        elif name == "county_fips_asc":
            key.append(opportunity["county_fips"])
        elif name == "parcel_apn_asc":
            key.append(opportunity["parcel_apn"])
    match_rank = item.receipt["recipe"]["match_rank"]
    key.extend((match_rank if match_rank is not None else 0, item.recipe_id))
    return tuple(key)


@dataclass
class RankedUniverse:
    strategy: Strategy
    ranking_universe_id: str
    receipts: list[dict[str, Any]]
    _index: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict, repr=False)

    def get(self, opportunity_key: str, recipe_id: str) -> dict[str, Any] | None:
        """rank(opportunity, recipe, strategy) — None when the pair is not a member."""
        return self._index.get((opportunity_key, recipe_id))


def rank(memberships: Iterable[ScoredMembership], *, ranking_run_id: str | None = None) -> RankedUniverse:
    """Order one universe of memberships under ONE strategy and seal every receipt.

    ``global_rank`` is the opportunity's position (its best membership);
    ``recipe_rank`` is 1-based within ``(recipe_id, recipe_version)``;
    ``ranking_universe_id`` is the ranker's own digest over the ordered members,
    so a primary-only universe under the canonical strategy reproduces it.
    """
    items = list(memberships)
    if not items:
        raise ScoringError("cannot rank an empty universe")
    strategies = {item.strategy.sha256 for item in items}
    if len(strategies) != 1:
        raise ScoringError("one universe ranks under exactly one strategy")
    strategy = items[0].strategy
    seen: set[tuple[str, str]] = set()
    for item in items:
        pair = (item.opportunity_key, item.recipe_id)
        if pair in seen:
            raise ScoringError(f"duplicate membership {pair}")
        seen.add(pair)

    items.sort(key=_sort_key)
    universe_id = digest({
        "ranking_version": strategy.strategy_id,
        "recipe_version": strategy.recipe_version,
        "members": [
            [item.receipt["opportunity"]["county_fips"], item.receipt["opportunity"]["parcel_apn"],
             item.recipe_id, item.composite]
            for item in items
        ],
    })

    def partition(item: ScoredMembership) -> tuple[str, str]:
        return item.recipe_id, item.receipt["recipe"]["recipe_version"]

    totals: Counter[tuple[str, str]] = Counter(partition(item) for item in items)
    recipe_seen: Counter[tuple[str, str]] = Counter()
    opportunity_rank: dict[str, int] = {}
    receipts: list[dict[str, Any]] = []
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        part = partition(item)
        recipe_seen[part] += 1
        if item.opportunity_key not in opportunity_rank:
            opportunity_rank[item.opportunity_key] = len(opportunity_rank) + 1
        receipt = dict(item.receipt)
        receipt["rank"] = {
            "global_rank": opportunity_rank[item.opportunity_key],
            "recipe_rank": recipe_seen[part],
            "recipe_membership_count": totals[part],
            "ranking_universe_id": universe_id,
            "ranking_run_id": ranking_run_id,
            "tie_break": list(strategy.document["tie_break"]) + ["match_rank_asc", "recipe_id_asc"],
        }
        receipt["content_sha256"] = content_hash(receipt)
        receipts.append(receipt)
        index[(item.opportunity_key, item.recipe_id)] = receipt
    return RankedUniverse(strategy=strategy, ranking_universe_id=universe_id, receipts=receipts, _index=index)


# --------------------------------------------------------------------------
# consumer projections
# --------------------------------------------------------------------------

def validate_membership_score_receipt(receipt: Mapping[str, Any]) -> None:
    """Re-hash and re-check the semantics a consumer relies on."""
    if receipt.get("schema_version") != RECEIPT_CONTRACT:
        raise ScoringError("not a membership-score receipt")
    if content_hash(receipt) != receipt.get("content_sha256"):
        raise ScoringError("membership-score receipt content_sha256 does not match its body")
    names = [c["name"] for c in receipt["score_vector"]["components"]]
    if names != [name for name, _owner in SCORE_DIMENSIONS]:
        raise ScoringError("score vector must carry the ten census dimensions in order")
    for component in receipt["score_vector"]["components"]:
        if component["evaluation_state"] != "EVALUATED" and component["value"] is not None:
            raise ScoringError(f"{component['name']}: {component['evaluation_state']} is not a low score")
        if component["evaluation_state"] == "EVALUATED" and (
            component["value"] is None or not component.get("receipt_sha256")
        ):
            raise ScoringError(f"{component['name']}: EVALUATED requires a value and a receipt")
    in_composite = sum(
        c["value"] for c in receipt["score_vector"]["components"] if c["in_composite"] and c["value"] is not None
    )
    composite = receipt["composite"]
    if in_composite != composite["uncapped"] or min(in_composite, composite["cap"]) != composite["value"]:
        raise ScoringError("composite is not the capped sum of its in-composite dimensions")


def to_score_vector(
    receipt: Mapping[str, Any],
    *,
    tenant_id: str,
    universe_id: str,
    opportunity_id: str | None = None,
    mode: str = "FIXTURE",
) -> dict[str, Any]:
    """Project onto Lead Recipe OS ``score-vector.v1`` so its kernel re-hashes it identically.

    Every declared ScoreComponent field is emitted (the kernel dumps defaults),
    values are floats (the kernel types them ``float``) and extras ride along.
    """
    validate_membership_score_receipt(receipt)
    if mode not in {"FIXTURE", "LIVE"}:
        raise ScoringError("score-vector mode must be FIXTURE or LIVE")
    components = []
    for component in receipt["score_vector"]["components"]:
        projected = {key: value for key, value in component.items() if key not in {"value", "receipt_sha256"}}
        projected.update({
            "value": None if component["value"] is None else float(component["value"]),
            "formula": (
                f"{receipt['strategy']['strategy_id']}:{component['name']}" if component["in_composite"] else None
            ),
            "mode": mode,
            "receipt_sha256": component.get("receipt_sha256"),
        })
        components.append(projected)
    vector = {
        "schema_version": SCORE_VECTOR_CONTRACT,
        "tenant_id": tenant_id,
        "universe_id": universe_id,
        "opportunity_id": opportunity_id or receipt["opportunity"]["opportunity_key"],
        "components": components,
    }
    vector["content_sha256"] = content_hash(vector)
    return vector


def membership_projection_row(receipt: Mapping[str, Any], *, why_matched_receipt: str = "") -> dict[str, Any]:
    """One loader row for DeedSonar ``candidate_recipe_membership`` (class is the loader's call)."""
    rank_block = receipt["rank"]
    return {
        "property_key": receipt["opportunity"]["property_key"] or "",
        "county_fips": receipt["opportunity"]["county_fips"],
        "parcel_apn": receipt["opportunity"]["parcel_apn"],
        "recipe_id": receipt["recipe"]["recipe_id"],
        "recipe_version": receipt["recipe"]["recipe_version"],
        "is_primary": "" if receipt["recipe"]["membership"] is None else str(receipt["recipe"]["membership"] == "PRIMARY").lower(),
        "match_rank": "" if receipt["recipe"]["match_rank"] is None else receipt["recipe"]["match_rank"],
        "scoring_version": receipt["strategy"]["strategy_id"],
        "recipe_score": receipt["composite"]["value"],
        "priority_tier": receipt["composite"]["tier"],
        "recipe_rank": rank_block["recipe_rank"],
        "recipe_membership_count": rank_block["recipe_membership_count"],
        "global_rank": rank_block["global_rank"],
        "ranking_universe_id": rank_block["ranking_universe_id"],
        "ranking_run_id": rank_block["ranking_run_id"] or "",
        "unknown_inputs": "|".join(receipt["counter_evidence"]["unknown_inputs"]),
        "undetermined_factors": "|".join(receipt["composite"]["undetermined_factors"]),
        "membership_score_receipt_sha256": receipt["content_sha256"],
        "why_matched_receipt": why_matched_receipt,
    }


MEMBERSHIP_PROJECTION_COLUMNS: tuple[str, ...] = (
    "property_key", "county_fips", "parcel_apn", "recipe_id", "recipe_version", "is_primary",
    "match_rank", "scoring_version", "recipe_score", "priority_tier", "recipe_rank",
    "recipe_membership_count", "global_rank", "ranking_universe_id", "ranking_run_id",
    "unknown_inputs", "undetermined_factors", "membership_score_receipt_sha256", "why_matched_receipt",
)


__all__ = [
    "CANONICAL_STRATEGY_ID",
    "MEMBERSHIP_PROJECTION_COLUMNS",
    "RECEIPT_CONTRACT",
    "RankedUniverse",
    "ReceiptDriftError",
    "SCORE_DIMENSIONS",
    "STRATEGY_CONTRACT",
    "STRATEGY_REGISTRY",
    "ScoredMembership",
    "ScoringError",
    "Strategy",
    "build_strategy",
    "evaluate_factors",
    "load_strategy",
    "membership_projection_row",
    "rank",
    "read_inputs",
    "score_membership",
    "to_score_vector",
    "validate_membership_score_receipt",
    "validate_strategy",
]
