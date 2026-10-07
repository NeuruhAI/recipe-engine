"""Recipe compiler: the canonical 30 -> executable per-recipe evaluation.

For every canonical recipe the compiler produces one object that says, from
source, its

    INPUT SIGNALS -> ELIGIBILITY -> SCORE VECTOR -> REQUIRED EVIDENCE
    -> MISSING EVIDENCE -> NBE -> ECONOMICS -> QUALIFICATION

and an evaluator that runs every compiled recipe INDEPENDENTLY against one
candidate's evidence.  It is the seam between the canonical recipe source
(:mod:`.canonical_recipes`) and the P1 eligibility producer (:mod:`.eligibility`);
it does not replace either.

Contracts, not conventions:

* Membership is decided by ``eligibility.derive_decision`` -- the one place the
  MATCH / NO_MATCH / UNKNOWN semantics live.  Boolean predicates are evaluated
  by ``eligibility._evaluate_clause`` unchanged; numeric, date and category
  predicates produce clause records of the same shape.
* UNKNOWN is never NO_MATCH.  A numeric contradiction is decisive only when
  the recipe says so AND every contradicting measurement is VERIFIED: an
  estimate below a threshold is negative evidence, not the opposite fact.
* Every recipe is evaluated on its own.  There is no ``primary_recipe``, no
  rank, no scalar score, and no ordering between recipes except recipe_n.
* Qualification is not authorization: every evaluation says
  ``action_authority: NOT_EVALUATED``.
* A recipe is lowered to an ``eligibility-policy.v1`` only when every
  membership predicate is a boolean on an ingredient with an authorized,
  one-to-one engine signal type.  Anything else would widen or drop a
  predicate, so it stays a compiled evaluation -- never a partial policy.
* Pure.  No I/O, no clock: ``evaluated_at`` is the context's ``as_of``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime
import math
from typing import Any

from .canonical_recipes import (
    CANONICAL_RECIPE_SPEC_CONTRACT,
    MEASUREMENT_BASES,
    NO_PRODUCER_SOURCE_STATES,
    SOURCE_STATES,
    VALUE_KINDS,
    canonical_recipe_spec,
)
from .contracts import CONFIDENCE_LEVELS
from .eligibility import (
    CANONICAL_SIGNAL_TYPES,
    FORBIDDEN_AUTHORITY_KEYS,
    EligibilityError,
    _evaluate_clause,
    _evidence_support,
    build_eligibility_policy,
    content_hash,
    derive_decision,
    evaluate_eligibility,
    membership_key,
    parse_timestamp,
    render_timestamp,
    validate_eligibility_context,
)

COMPILED_RECIPE_CONTRACT = "neuruh.recipe-engine.compiled-recipe.v1"
COMPILED_RECIPE_SET_CONTRACT = "neuruh.recipe-engine.compiled-recipe-set.v1"
RECIPE_EVALUATION_INPUT_CONTRACT = "neuruh.recipe-engine.recipe-evaluation-input.v1"
RECIPE_EVALUATION_CONTRACT = "neuruh.recipe-engine.recipe-evaluation.v1"
RECIPE_EVALUATION_SET_CONTRACT = "neuruh.recipe-engine.recipe-evaluation-set.v1"
COMPILER_VERSION = "recipe-engine/recipe-compiler@1.0.0"
PRODUCER_OWNER = "recipe-engine"

ROLES = ("REQUIRED", "NEGATIVE", "QUALIFYING", "DISQUALIFIER", "AMPLIFIER")
REQUIREMENT_KINDS = ("PREDICATE", "ANY_OF", "UNDEFINED_PREDICATE")
RECIPE_KINDS = ("SELLER_OPPORTUNITY", "WATCH", "META")
OPS_BY_VALUE_KIND = {
    "BOOLEAN": ("is",),
    "NUMBER": ("between", "exists", "gte", "lte"),
    "DATE": ("after_as_of", "exists"),
    "CATEGORY": ("exists", "in"),
}
CONFIDENCE_FLOORS = ("HIGH", "MEDIUM", "LOW")
_BASIS_RANK = {"VERIFIED": 3, "ESTIMATED": 2, "PROXY": 1}
_CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}

COMPILE_STATES = ("EXECUTABLE", "PARTIAL", "SOURCE_BLOCKED", "PREDICATE_UNDEFINED", "META")
QUALIFICATION_STATES = (
    "QUALIFIED",
    "NEEDS_EVIDENCE",
    "NOT_QUALIFIED",
    "DISQUALIFIED",
    "NOT_ELIGIBLE",
    "SOURCE_BLOCKED",
    "PREDICATE_UNDEFINED",
    "NOT_APPLICABLE",
)

#: Score vector dimensions and their owning organ (RECIPE_SCORE_VECTOR_COVERAGE.csv).
#: The compiler evaluates the ones the recipe itself can answer and leaves the
#: rest with their owner.  There is no total.
SCORE_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("strategic_fit", "recipe-engine"),
    ("signal_strength", "recipe-engine"),
    ("urgency", "recipe-engine"),
    ("downside_risk", "recipe-engine"),
    ("evidence_confidence", "landos-engine"),
    ("source_independence_diversity", "recipe-engine"),
    ("data_completeness", "recipe-engine"),
    ("economic_value", "landos-engine"),
    ("contactability", "neuruh-contact-intelligence"),
    ("liquidity", "neuruh-liquidity-engine"),
)

#: Next-best-evidence planner ranking (0-1), NOT an opportunity score.  How
#: likely a gap is to be resolved from where its ingredient's source stands.
#: Versioned policy; the formula is emitted with every gap.
NBE_RESOLVABILITY = {
    "AVAILABLE": 1.0,
    "PARTIAL": 0.75,
    "SOURCE_REQUIRED": 0.4,
    "WALLED": 0.2,
    "PROPOSED": 0.2,
    "MISSING": 0.1,
}
NBE_IMPACT = {"MEMBERSHIP": 1.0, "QUALIFICATION": 0.5, "ECONOMICS": 0.25}
NBE_FORMULA = "impact[blocks] / unresolved_in_scope * resolvability[source_state]"

_MEASUREMENT_KEYS = frozenset({
    "measurement_id", "ingredient", "value", "unit", "basis", "confidence",
    "evidence_refs", "observed_at", "note",
})
_INPUT_KEYS = frozenset({"schema_version", "context", "measurements"})


class RecipeCompilerError(EligibilityError):
    """The recipe source, compiled set or evaluation input cannot be trusted."""


# --------------------------------------------------------------------------
# spec validation
# --------------------------------------------------------------------------

def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_predicate(pred: Mapping[str, Any], path: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    ingredient = pred.get("ingredient")
    if ingredient not in spec["ingredients"]:
        raise RecipeCompilerError(f"{path}.ingredient {ingredient!r} is not in the ingredient vocabulary")
    info = spec["ingredients"][ingredient]
    op = pred.get("op")
    if op not in OPS_BY_VALUE_KIND[info["value_kind"]]:
        raise RecipeCompilerError(f"{path}.op {op!r} is not valid for a {info['value_kind']} ingredient")
    value = pred.get("value")
    if op == "is" and not isinstance(value, bool):
        raise RecipeCompilerError(f"{path}.value must be a boolean for op 'is'")
    if op in ("gte", "lte") and not _number(value):
        raise RecipeCompilerError(f"{path}.value must be a finite number for op {op!r}")
    if op == "between":
        if not (isinstance(value, list) and len(value) == 2 and all(map(_number, value)) and value[0] <= value[1]):
            raise RecipeCompilerError(f"{path}.value must be [low, high] for op 'between'")
    if op == "in":
        if not (isinstance(value, list) and value and all(isinstance(v, str) and v for v in value)):
            raise RecipeCompilerError(f"{path}.value must be a non-empty list of states for op 'in'")
        allowed = spec.get("clock_states", {}).get(ingredient)
        if allowed is not None and not set(value) <= set(allowed):
            raise RecipeCompilerError(f"{path}.value names states outside {ingredient} {allowed}")
        value = sorted(value)
    if op in ("exists", "after_as_of") and value is not None:
        raise RecipeCompilerError(f"{path}.value must be null for op {op!r}")
    decisive = pred.get("negative_evidence_decisive")
    if not isinstance(decisive, bool):
        raise RecipeCompilerError(f"{path}.negative_evidence_decisive must be an explicit boolean")
    if op == "exists" and decisive:
        raise RecipeCompilerError(f"{path}: 'exists' can never be contradicted, so it cannot be decisive")
    floor = pred.get("min_confidence")
    if floor not in CONFIDENCE_FLOORS:
        raise RecipeCompilerError(f"{path}.min_confidence must be HIGH, MEDIUM or LOW")
    basis = pred.get("min_basis")
    if basis not in MEASUREMENT_BASES:
        raise RecipeCompilerError(f"{path}.min_basis must be one of {list(MEASUREMENT_BASES)}")
    if info["value_kind"] != "BOOLEAN" and _BASIS_RANK[basis] > _BASIS_RANK[info["max_basis"]]:
        raise RecipeCompilerError(
            f"{path} requires {basis} {ingredient} but that ingredient is at best {info['max_basis']}: "
            "the predicate could never be satisfied"
        )
    max_age = pred.get("max_age_seconds")
    if max_age is not None and (isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0):
        raise RecipeCompilerError(f"{path}.max_age_seconds must be a positive integer or null")
    return {
        "ingredient": ingredient,
        "op": op,
        "value": value,
        "negative_evidence_decisive": decisive,
        "min_confidence": floor,
        "min_basis": basis,
        "max_age_seconds": max_age,
    }


def _validate_requirement(req: Mapping[str, Any], path: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    requirement_id = req.get("requirement_id")
    if not isinstance(requirement_id, str) or not requirement_id:
        raise RecipeCompilerError(f"{path}.requirement_id must be a non-empty string")
    role = req.get("role")
    if role not in ROLES:
        raise RecipeCompilerError(f"{path}.role must be one of {list(ROLES)}")
    kind = req.get("kind")
    if kind not in REQUIREMENT_KINDS:
        raise RecipeCompilerError(f"{path}.kind must be one of {list(REQUIREMENT_KINDS)}")
    out: dict[str, Any] = {
        "requirement_id": requirement_id,
        "role": role,
        "kind": kind,
        "label": str(req.get("label") or ""),
    }
    if kind == "PREDICATE":
        out.update(_validate_predicate(req, path, spec))
        if role in ("NEGATIVE", "DISQUALIFIER") and out["op"] != "is":
            raise RecipeCompilerError(f"{path}: {role} requirements must be boolean 'is' predicates")
    elif kind == "ANY_OF":
        if role not in ("REQUIRED", "QUALIFYING"):
            raise RecipeCompilerError(f"{path}: ANY_OF is only valid for REQUIRED or QUALIFYING")
        alternatives = req.get("alternatives")
        if not isinstance(alternatives, list) or len(alternatives) < 2:
            raise RecipeCompilerError(f"{path}.alternatives must list at least two predicates")
        alts = [_validate_predicate(a, f"{path}.alternatives[{i}]", spec) for i, a in enumerate(alternatives)]
        ingredients = [a["ingredient"] for a in alts]
        if len(set(ingredients)) != len(ingredients):
            raise RecipeCompilerError(f"{path}.alternatives name an ingredient twice")
        out["alternatives"] = alts
    else:
        if role not in ("REQUIRED", "QUALIFYING"):
            raise RecipeCompilerError(f"{path}: UNDEFINED_PREDICATE is only valid for REQUIRED or QUALIFYING")
        inputs = req.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise RecipeCompilerError(f"{path}.inputs must name the ingredients the predicate would read")
        for item in inputs:
            if item not in spec["ingredients"]:
                raise RecipeCompilerError(f"{path}.inputs names unknown ingredient {item!r}")
        out["inputs"] = sorted(inputs)
    return out


def validate_recipe_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed on any recipe source that cannot compile deterministically."""
    if not isinstance(spec, Mapping):
        raise RecipeCompilerError("spec must be an object")
    if spec.get("schema_version") != CANONICAL_RECIPE_SPEC_CONTRACT:
        raise RecipeCompilerError(f"spec.schema_version must be {CANONICAL_RECIPE_SPEC_CONTRACT}")
    spec = deepcopy(dict(spec))
    for name, info in spec.get("ingredients", {}).items():
        if info.get("value_kind") not in VALUE_KINDS:
            raise RecipeCompilerError(f"ingredient {name}: value_kind must be one of {list(VALUE_KINDS)}")
        if info.get("source_state") not in SOURCE_STATES:
            raise RecipeCompilerError(f"ingredient {name}: source_state must be one of {list(SOURCE_STATES)}")
        if info.get("max_basis") not in MEASUREMENT_BASES:
            raise RecipeCompilerError(f"ingredient {name}: max_basis must be one of {list(MEASUREMENT_BASES)}")
        engine = info.get("engine_signal_type")
        if engine is not None:
            if engine not in CANONICAL_SIGNAL_TYPES:
                raise RecipeCompilerError(f"ingredient {name}: engine_signal_type {engine!r} is not canonical")
            if info["value_kind"] != "BOOLEAN":
                raise RecipeCompilerError(f"ingredient {name}: only BOOLEAN ingredients may lower to a signal type")
        if not str(info.get("genome_ingredient_id") or "").startswith("I-"):
            raise RecipeCompilerError(f"ingredient {name}: genome_ingredient_id must be a GENOME-B I- id")
    engines = [i["engine_signal_type"] for i in spec["ingredients"].values() if i.get("engine_signal_type")]
    if len(engines) != len(set(engines)):
        raise RecipeCompilerError("engine_signal_type crosswalk must be one-to-one (lowering must never widen)")

    recipes = spec.get("recipes")
    if not isinstance(recipes, list) or not recipes:
        raise RecipeCompilerError("spec.recipes must be a non-empty list")
    seen_n: set[int] = set()
    seen_id: set[str] = set()
    normalized = []
    for index, recipe in enumerate(recipes):
        path = f"spec.recipes[{index}]"
        n, rid = recipe.get("recipe_n"), recipe.get("recipe_id")
        if isinstance(n, bool) or not isinstance(n, int) or n in seen_n:
            raise RecipeCompilerError(f"{path}.recipe_n must be a unique integer")
        if not isinstance(rid, str) or not rid or rid in seen_id:
            raise RecipeCompilerError(f"{path}.recipe_id must be a unique non-empty string")
        seen_n.add(n)
        seen_id.add(rid)
        if recipe.get("kind") not in RECIPE_KINDS:
            raise RecipeCompilerError(f"{path}.kind must be one of {list(RECIPE_KINDS)}")
        reqs = [_validate_requirement(r, f"{path}.requirements[{i}]", spec)
                for i, r in enumerate(recipe.get("requirements") or [])]
        ids = [r["requirement_id"] for r in reqs]
        if len(ids) != len(set(ids)):
            raise RecipeCompilerError(f"{path}: duplicate requirement_id")
        if recipe["kind"] == "META":
            if reqs:
                raise RecipeCompilerError(f"{path}: a META recipe is a projection and declares no requirements")
        elif not any(r["role"] == "REQUIRED" for r in reqs):
            raise RecipeCompilerError(f"{path}: a recipe needs at least one REQUIRED membership predicate")
        clock = recipe.get("process_clock")
        if clock is not None and clock not in spec.get("process_clocks", {}):
            raise RecipeCompilerError(f"{path}.process_clock {clock!r} is not a known process clock")
        economics = recipe.get("economics") or {}
        if economics.get("state") not in ("DEFINED", "UNDEFINED"):
            raise RecipeCompilerError(f"{path}.economics.state must be DEFINED or UNDEFINED")
        gates = []
        for gi, gate in enumerate(economics.get("gates") or []):
            gates.append(_validate_predicate(
                {**gate, "negative_evidence_decisive": False, "min_confidence": "LOW",
                 "min_basis": "PROXY", "max_age_seconds": None},
                f"{path}.economics.gates[{gi}]", spec,
            ))
        if economics["state"] == "DEFINED" and not gates:
            raise RecipeCompilerError(f"{path}.economics: a DEFINED gate needs at least one predicate")
        if economics["state"] == "UNDEFINED" and gates:
            raise RecipeCompilerError(f"{path}.economics: an UNDEFINED gate cannot carry predicates")
        normalized.append({
            **{k: deepcopy(recipe[k]) for k in (
                "recipe_n", "recipe_id", "canonical_name", "family", "kind", "aliases",
                "process_clock", "action_constraints", "manual_checks", "notes")},
            "requirements": reqs,
            "economics": {"state": economics["state"], "gates": gates, "note": str(economics.get("note") or "")},
        })
    normalized.sort(key=lambda r: r["recipe_n"])
    spec["recipes"] = normalized
    return spec


# --------------------------------------------------------------------------
# compilation
# --------------------------------------------------------------------------

def _requirement_ingredients(req: Mapping[str, Any]) -> list[str]:
    if req["kind"] == "PREDICATE":
        return [req["ingredient"]]
    if req["kind"] == "ANY_OF":
        return [a["ingredient"] for a in req["alternatives"]]
    return list(req["inputs"])


def _has_producer(ingredients: Sequence[str], spec: Mapping[str, Any], *, any_of: bool) -> bool:
    states = [spec["ingredients"][i]["source_state"] not in NO_PRODUCER_SOURCE_STATES for i in ingredients]
    return any(states) if any_of else all(states)


def _compile_state(recipe: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[str, list[str]]:
    if recipe["kind"] == "META":
        return "META", ["projection over the other recipes' evaluations"]
    reasons: list[str] = []
    membership = [r for r in recipe["requirements"] if r["role"] == "REQUIRED"]
    undefined = [r["requirement_id"] for r in membership if r["kind"] == "UNDEFINED_PREDICATE"]
    if undefined:
        return "PREDICATE_UNDEFINED", [f"membership predicate {rid} has no defined threshold" for rid in undefined]
    blocked = []
    partial = []
    for req in membership:
        ingredients = _requirement_ingredients(req)
        if not _has_producer(ingredients, spec, any_of=req["kind"] == "ANY_OF"):
            blocked.append(f"{req['requirement_id']}: no producer for {'|'.join(ingredients)}")
        elif any(spec["ingredients"][i]["source_state"] != "AVAILABLE" for i in ingredients):
            partial.append(f"{req['requirement_id']}: partial coverage for {'|'.join(ingredients)}")
    if blocked:
        return "SOURCE_BLOCKED", blocked
    if partial:
        return "PARTIAL", partial
    reasons.append("every membership predicate has an AVAILABLE producer")
    return "EXECUTABLE", reasons


def _lowering(recipe: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Whether the membership predicates ARE an eligibility-policy.v1, exactly."""
    if recipe["kind"] == "META":
        return "NOT_LOWERED", ["META recipe"]
    reasons: list[str] = []
    for req in recipe["requirements"]:
        if req["role"] == "NEGATIVE":
            reasons.append(f"{req['requirement_id']}: NEGATIVE defeaters have no eligibility-policy.v1 form")
        if req["role"] != "REQUIRED":
            continue
        if req["kind"] != "PREDICATE":
            reasons.append(f"{req['requirement_id']}: {req['kind']} has no eligibility-policy.v1 form")
            continue
        if req["op"] != "is":
            reasons.append(f"{req['requirement_id']}: {req['op']} predicate on {req['ingredient']} is not boolean")
            continue
        if not spec["ingredients"][req["ingredient"]].get("engine_signal_type"):
            reasons.append(f"{req['requirement_id']}: {req['ingredient']} has no authorized engine signal type")
    return ("NOT_LOWERED", reasons) if reasons else ("LOWERED", ["every membership predicate is a crosswalked boolean"])


def _nbe_plan(recipe: Mapping[str, Any], spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    plan = []
    for req in recipe["requirements"]:
        if req["role"] == "AMPLIFIER":
            continue
        for ingredient in _requirement_ingredients(req):
            info = spec["ingredients"][ingredient]
            plan.append({
                "requirement_id": req["requirement_id"],
                "role": req["role"],
                "ingredient": ingredient,
                "genome_ingredient_id": info["genome_ingredient_id"],
                "question": info["nbe_question"],
                "source_state": info["source_state"],
                "unlock_family": info["unlock_family"],
                "producer": info["producer"],
            })
    return plan


def compile_recipe(recipe: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    """One validated recipe -> one compiled recipe (content-addressed)."""
    ingredients = sorted({i for req in recipe["requirements"] for i in _requirement_ingredients(req)}
                         | {g["ingredient"] for g in recipe["economics"]["gates"]})
    compile_state, compile_reasons = _compile_state(recipe, spec)
    lowering_state, lowering_reasons = _lowering(recipe, spec)
    by_role = {role: [r["requirement_id"] for r in recipe["requirements"] if r["role"] == role] for role in ROLES}
    clock = recipe["process_clock"]
    body = {
        "schema_version": COMPILED_RECIPE_CONTRACT,
        "compiler_version": COMPILER_VERSION,
        "spec_version": spec["spec_version"],
        "recipe_n": recipe["recipe_n"],
        "recipe_id": recipe["recipe_id"],
        "canonical_name": recipe["canonical_name"],
        "family": recipe["family"],
        "kind": recipe["kind"],
        "aliases": recipe["aliases"],
        "input_signals": [
            {
                "ingredient": name,
                "genome_ingredient_id": spec["ingredients"][name]["genome_ingredient_id"],
                "value_kind": spec["ingredients"][name]["value_kind"],
                "source_state": spec["ingredients"][name]["source_state"],
                "engine_signal_type": spec["ingredients"][name]["engine_signal_type"],
            }
            for name in ingredients
        ],
        "requirements": recipe["requirements"],
        "eligibility": {
            "membership_predicates": by_role["REQUIRED"],
            "defeaters": by_role["NEGATIVE"],
            "decision_semantics": "recipe_engine.opportunity_intelligence.eligibility.derive_decision",
        },
        "required_evidence": [
            {
                "requirement_id": r["requirement_id"],
                "role": r["role"],
                "ingredients": _requirement_ingredients(r),
                "label": r["label"],
            }
            for r in recipe["requirements"] if r["role"] in ("REQUIRED", "QUALIFYING", "DISQUALIFIER")
        ],
        "score_vector": {
            "dimensions": [{"name": name, "organ": organ} for name, organ in SCORE_DIMENSIONS],
            "amplifiers": by_role["AMPLIFIER"],
            "total": None,
        },
        "nbe_plan": _nbe_plan(recipe, spec),
        "economics": recipe["economics"],
        "process_clock": None if clock is None else {"clock": clock, **spec["process_clocks"][clock]},
        "qualification_policy": {
            "qualifying": by_role["QUALIFYING"],
            "disqualifiers": by_role["DISQUALIFIER"],
            "action_constraints": recipe["action_constraints"],
            "manual_checks": recipe["manual_checks"],
        },
        "compile_state": compile_state,
        "compile_reasons": compile_reasons,
        "p1_lowering": {"state": lowering_state, "reasons": lowering_reasons},
        "notes": recipe["notes"],
    }
    body["recipe_digest"] = content_hash(body, omit=())
    return body


def compile_canonical_recipes(spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The canonical recipe source -> the compiled set every consumer reads."""
    validated = validate_recipe_spec(spec if spec is not None else canonical_recipe_spec())
    recipes = [compile_recipe(recipe, validated) for recipe in validated["recipes"]]
    body = {
        "schema_version": COMPILED_RECIPE_SET_CONTRACT,
        "compiler_version": COMPILER_VERSION,
        "spec_version": validated["spec_version"],
        "spec_digest": content_hash(validated, omit=()),
        "provenance": validated["provenance"],
        "ingredients": validated["ingredients"],
        "recipes": recipes,
        "summary": {
            "recipe_count": len(recipes),
            "compile_states": {
                state: sorted(r["recipe_id"] for r in recipes if r["compile_state"] == state)
                for state in COMPILE_STATES
            },
            "p1_lowered": sorted(r["recipe_id"] for r in recipes if r["p1_lowering"]["state"] == "LOWERED"),
        },
    }
    body["set_digest"] = content_hash(body, omit=())
    return body


def validate_compiled_set(compiled: Mapping[str, Any]) -> dict[str, Any]:
    """A stranger's check: every digest binds its body."""
    if compiled.get("schema_version") != COMPILED_RECIPE_SET_CONTRACT:
        raise RecipeCompilerError(f"compiled.schema_version must be {COMPILED_RECIPE_SET_CONTRACT}")
    for recipe in compiled["recipes"]:
        if content_hash(recipe, omit=("recipe_digest",)) != recipe["recipe_digest"]:
            raise RecipeCompilerError(f"compiled recipe {recipe.get('recipe_id')!r} digest mismatch")
    if content_hash(compiled, omit=("set_digest",)) != compiled["set_digest"]:
        raise RecipeCompilerError("compiled set digest mismatch")
    return dict(compiled)


def lower_to_eligibility_policy(
    compiled_recipe: Mapping[str, Any],
    ingredients: Mapping[str, Mapping[str, Any]],
    *,
    tenant_id: str,
    validity_seconds: int = 86400,
) -> dict[str, Any] | None:
    """The exact ``eligibility-policy.v1`` for a LOWERED recipe, else ``None``."""
    if compiled_recipe["p1_lowering"]["state"] != "LOWERED":
        return None
    clauses = []
    for req in compiled_recipe["requirements"]:
        if req["role"] != "REQUIRED":
            continue
        clauses.append({
            "clause_id": req["requirement_id"],
            "signal_type": ingredients[req["ingredient"]]["engine_signal_type"],
            "expect": "PRESENT" if req["value"] else "ABSENT",
            "negative_evidence_decisive": req["negative_evidence_decisive"],
            "min_confidence": req["min_confidence"],
            "max_age_seconds": req["max_age_seconds"],
        })
    return build_eligibility_policy(
        tenant_id=tenant_id,
        recipe_id=compiled_recipe["recipe_id"],
        recipe_version_id=f"{compiled_recipe['recipe_id']}@compiled.{compiled_recipe['recipe_digest'][:12]}",
        version=compiled_recipe["spec_version"],
        clauses=clauses,
        ingredients=sorted(i["ingredient"] for i in compiled_recipe["input_signals"]),
        validity_seconds=validity_seconds,
    )


# --------------------------------------------------------------------------
# evaluation input
# --------------------------------------------------------------------------

def _parse_measurement_value(value: Any, value_kind: str, path: str) -> Any:
    if value_kind == "NUMBER":
        if not _number(value):
            raise RecipeCompilerError(f"{path}.value must be a finite number")
        return value
    if value_kind == "CATEGORY":
        if not isinstance(value, str) or not value.strip():
            raise RecipeCompilerError(f"{path}.value must be a non-empty string")
        return value.strip().upper()
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError as exc:
        raise RecipeCompilerError(f"{path}.value must be an ISO date") from exc
    return parsed.isoformat()


def validate_evaluation_input(
    payload: Mapping[str, Any], ingredients: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``recipe-evaluation-input.v1`` -> (canonical P1 context, canonical measurements).

    Boolean ingredients arrive as P1 context signals (``signal_type`` = the
    ingredient id); numeric, date and category ingredients arrive as
    measurements bound to the same evidence.  A value in the wrong channel is
    refused rather than guessed at."""
    if not isinstance(payload, Mapping):
        raise RecipeCompilerError("evaluation input must be an object")
    present = sorted(FORBIDDEN_AUTHORITY_KEYS & set(map(str, payload.keys())))
    if present:
        raise RecipeCompilerError(f"evaluation input carries {present}: not eligibility authority")
    unknown = sorted(set(map(str, payload.keys())) - _INPUT_KEYS)
    if unknown:
        raise RecipeCompilerError(f"evaluation input has unrecognized fields {unknown}")
    if payload.get("schema_version") != RECIPE_EVALUATION_INPUT_CONTRACT:
        raise RecipeCompilerError(f"evaluation input schema_version must be {RECIPE_EVALUATION_INPUT_CONTRACT}")
    context = validate_eligibility_context(payload.get("context") or {})
    for signal in context["signals"]:
        info = ingredients.get(signal["signal_type"])
        if info is not None and info["value_kind"] != "BOOLEAN":
            raise RecipeCompilerError(
                f"signal {signal['signal_id']} carries {info['value_kind']} ingredient "
                f"{signal['signal_type']!r}; send it as a measurement"
            )
    as_of = parse_timestamp(context["as_of"], "context.as_of")
    evidence_ids = {e["evidence_id"] for e in context["evidence"]}
    measurements: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(payload.get("measurements") or []):
        path = f"measurements[{index}]"
        if not isinstance(raw, Mapping):
            raise RecipeCompilerError(f"{path} must be an object")
        forbidden = sorted(FORBIDDEN_AUTHORITY_KEYS & set(map(str, raw.keys())))
        if forbidden:
            raise RecipeCompilerError(f"{path} carries {forbidden}: not eligibility authority")
        extra = sorted(set(map(str, raw.keys())) - _MEASUREMENT_KEYS)
        if extra:
            raise RecipeCompilerError(f"{path} has unrecognized fields {extra}")
        mid = raw.get("measurement_id")
        if not isinstance(mid, str) or not mid or mid in measurements:
            raise RecipeCompilerError(f"{path}.measurement_id must be a unique non-empty string")
        ingredient = raw.get("ingredient")
        info = ingredients.get(ingredient)
        if info is None:
            raise RecipeCompilerError(f"{path}.ingredient {ingredient!r} is not in the ingredient vocabulary")
        if info["value_kind"] == "BOOLEAN":
            raise RecipeCompilerError(f"{path}: {ingredient} is BOOLEAN; send it as a context signal")
        basis = raw.get("basis")
        if basis not in MEASUREMENT_BASES:
            raise RecipeCompilerError(f"{path}.basis must be one of {list(MEASUREMENT_BASES)}")
        if _BASIS_RANK[basis] > _BASIS_RANK[info["max_basis"]]:
            raise RecipeCompilerError(f"{path}: {ingredient} cannot be {basis} (at best {info['max_basis']})")
        if raw.get("confidence") not in CONFIDENCE_LEVELS:
            raise RecipeCompilerError(f"{path}.confidence must be one of {list(CONFIDENCE_LEVELS)}")
        refs = sorted(set(raw.get("evidence_refs") or []))
        for ref in refs:
            if ref not in evidence_ids:
                raise RecipeCompilerError(f"{path}.evidence_refs names unbound evidence {ref!r}")
        observed = parse_timestamp(raw.get("observed_at"), f"{path}.observed_at")
        if observed > as_of:
            raise RecipeCompilerError(f"{path}.observed_at is after context.as_of (future measurement)")
        measurements[mid] = {
            "measurement_id": mid,
            "ingredient": ingredient,
            "value": _parse_measurement_value(raw.get("value"), info["value_kind"], path),
            "unit": None if raw.get("unit") is None else str(raw["unit"]),
            "basis": basis,
            "confidence": raw["confidence"],
            "evidence_refs": refs,
            "observed_at": render_timestamp(observed),
            "note": str(raw.get("note") or ""),
        }
    return context, [measurements[k] for k in sorted(measurements)]


def lower_context(context: Mapping[str, Any], ingredients: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Rename crosswalked ingredient signals to their engine signal type (one-to-one)."""
    lowered = deepcopy(dict(context))
    for signal in lowered["signals"]:
        engine = (ingredients.get(signal["signal_type"]) or {}).get("engine_signal_type")
        if engine:
            signal["signal_type"] = engine
    return lowered


# --------------------------------------------------------------------------
# clause evaluation
# --------------------------------------------------------------------------

def _predicate_holds(op: str, target: Any, value: Any, as_of: datetime) -> bool:
    if op == "exists":
        return True
    if op == "gte":
        return value >= target
    if op == "lte":
        return value <= target
    if op == "between":
        return target[0] <= value <= target[1]
    if op == "in":
        return value in target
    if op == "after_as_of":
        return date.fromisoformat(value) > as_of.date()
    raise RecipeCompilerError(f"unsupported op {op!r}")


def _evaluate_measure(
    pred: Mapping[str, Any],
    clause_id: str,
    measurements: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    """Numeric / date / category predicate -> a record shaped like a P1 clause."""
    considered = [m for m in measurements if m["ingredient"] == pred["ingredient"]]
    floor = _CONFIDENCE_RANK[pred["min_confidence"]]
    basis_floor = _BASIS_RANK[pred["min_basis"]]
    known: dict[bool, list[tuple[Mapping[str, Any], list[str]]]] = {True: [], False: []}
    codes: set[str] = set()
    for m in considered:
        code = None
        if m["confidence"] == "UNKNOWN":
            code = "SIGNAL_UNKNOWN"
        elif _CONFIDENCE_RANK[m["confidence"]] < floor:
            code = "CONFIDENCE_BELOW_FLOOR"
        elif _BASIS_RANK[m["basis"]] < basis_floor:
            code = "BASIS_BELOW_FLOOR"
        elif not m["evidence_refs"]:
            code = "NO_EVIDENCE_LINEAGE"
        if code is not None:
            codes.add(code)
            continue
        supporting = []
        for ref in m["evidence_refs"]:
            ev_code = _evidence_support(evidence_by_id[ref], as_of=as_of, max_age_seconds=pred["max_age_seconds"])
            if ev_code is None:
                supporting.append(ref)
            else:
                codes.add(ev_code)
        if supporting:
            known[_predicate_holds(pred["op"], pred["value"], m["value"], as_of)].append((m, supporting))
    record: dict[str, Any] = {
        "clause_id": clause_id,
        "signal_type": pred["ingredient"],
        "expect": "PRESENT",
        "predicate": {"op": pred["op"], "value": pred["value"]},
        "min_confidence": pred["min_confidence"],
        "min_basis": pred["min_basis"],
        "max_age_seconds": pred["max_age_seconds"],
        "signal_ids": sorted(m["measurement_id"] for m in considered),
    }
    if known[True] and known[False]:
        record.update(outcome="UNKNOWN", observed_state="CONFLICTED", reason_codes=["SIGNAL_CONFLICTED"],
                      evidence_ids=[], negative_evidence_decisive=pred["negative_evidence_decisive"])
        return record
    if not known[True] and not known[False]:
        record.update(outcome="UNKNOWN", observed_state=None, reason_codes=sorted(codes) or ["NO_SIGNAL"],
                      evidence_ids=[], negative_evidence_decisive=pred["negative_evidence_decisive"])
        return record
    holds = bool(known[True])
    evidence_ids = sorted({ref for _, refs in known[holds] for ref in refs})
    if holds:
        record.update(outcome="SATISFIED", observed_state="PRESENT", reason_codes=["SATISFIED_PREDICATE"],
                      evidence_ids=evidence_ids, negative_evidence_decisive=pred["negative_evidence_decisive"])
        return record
    # An estimate below a threshold is negative evidence, never the opposite fact.
    decisive = pred["negative_evidence_decisive"] and all(m["basis"] == "VERIFIED" for m, _ in known[False])
    reason = ["CONTRADICTED_PREDICATE"] + ([] if decisive else ["CONTRADICTION_NOT_DECISIVE"])
    record.update(outcome="CONTRADICTED", observed_state="ABSENT", reason_codes=reason,
                  evidence_ids=evidence_ids, negative_evidence_decisive=decisive)
    return record


def _evaluate_predicate(
    pred: Mapping[str, Any],
    clause_id: str,
    context: Mapping[str, Any],
    measurements: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    ingredients: Mapping[str, Mapping[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    if ingredients[pred["ingredient"]]["value_kind"] == "BOOLEAN":
        clause = {
            "clause_id": clause_id,
            "signal_type": pred["ingredient"],
            "expect": "PRESENT" if pred["value"] else "ABSENT",
            "negative_evidence_decisive": pred["negative_evidence_decisive"],
            "min_confidence": pred["min_confidence"],
            "max_age_seconds": pred["max_age_seconds"],
        }
        record, _ = _evaluate_clause(clause, context["signals"], evidence_by_id, as_of=as_of)
        record["predicate"] = {"op": "is", "value": pred["value"]}
        return record
    return _evaluate_measure(pred, clause_id, measurements, evidence_by_id, as_of)


def _evaluate_requirement(
    req: Mapping[str, Any],
    context: Mapping[str, Any],
    measurements: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    ingredients: Mapping[str, Mapping[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    rid = req["requirement_id"]
    if req["kind"] == "PREDICATE":
        record = _evaluate_predicate(req, rid, context, measurements, evidence_by_id, ingredients, as_of)
    elif req["kind"] == "UNDEFINED_PREDICATE":
        record = {
            "clause_id": rid, "signal_type": "|".join(req["inputs"]), "expect": "PRESENT",
            "predicate": {"op": "undefined", "value": None}, "negative_evidence_decisive": False,
            "outcome": "UNKNOWN", "observed_state": None, "reason_codes": ["PREDICATE_UNDEFINED"],
            "evidence_ids": [], "signal_ids": [],
        }
    else:
        alternatives = [
            _evaluate_predicate(alt, f"{rid}[{alt['ingredient']}]", context, measurements, evidence_by_id,
                                ingredients, as_of)
            for alt in req["alternatives"]
        ]
        satisfied = [a for a in alternatives if a["outcome"] == "SATISFIED"]
        contradicted = [a for a in alternatives if a["outcome"] == "CONTRADICTED"]
        record = {
            "clause_id": rid, "signal_type": "|".join(a["signal_type"] for a in alternatives),
            "expect": "PRESENT", "predicate": {"op": "any_of", "value": None},
            "alternatives": alternatives,
            "signal_ids": sorted({s for a in alternatives for s in a["signal_ids"]}),
        }
        if satisfied:
            record.update(outcome="SATISFIED", observed_state="PRESENT", reason_codes=["SATISFIED_ANY_OF"],
                          evidence_ids=sorted({e for a in satisfied for e in a["evidence_ids"]}),
                          negative_evidence_decisive=False)
        elif len(contradicted) == len(alternatives):
            decisive = all(a["negative_evidence_decisive"] for a in contradicted)
            record.update(outcome="CONTRADICTED", observed_state="ABSENT",
                          reason_codes=["CONTRADICTED_ALL_ALTERNATIVES"]
                          + ([] if decisive else ["CONTRADICTION_NOT_DECISIVE"]),
                          evidence_ids=sorted({e for a in contradicted for e in a["evidence_ids"]}),
                          negative_evidence_decisive=decisive)
        else:
            codes = sorted({c for a in alternatives if a["outcome"] != "SATISFIED" for c in a["reason_codes"]})
            record.update(outcome="UNKNOWN", observed_state=None, reason_codes=codes, evidence_ids=[],
                          negative_evidence_decisive=False)
    record["role"] = req["role"]
    record["kind"] = req["kind"]
    record["label"] = req["label"]
    record["in_decision"] = req["role"] == "REQUIRED" or (req["role"] == "NEGATIVE" and record["outcome"] != "UNKNOWN")
    return record


# --------------------------------------------------------------------------
# per-recipe projections: score vector, evidence, NBE, economics, qualification
# --------------------------------------------------------------------------

def _component(name: str, organ: str, state: str, value: Any, formula: str, **observed: Any) -> dict[str, Any]:
    if state != "EVALUATED":
        value = None  # NOT_EVALUATED / UNKNOWN is not LOW_SCORE: the value stays null
    return {"name": name, "organ": organ, "evaluation_state": state, "value": value,
            "formula": formula, "observed": observed}


def _days_until(value: str, as_of: datetime) -> int:
    return (date.fromisoformat(value) - as_of.date()).days


def _score_vector(
    compiled: Mapping[str, Any],
    decision: str,
    records: Sequence[Mapping[str, Any]],
    measurements: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    economics: Mapping[str, Any],
    ingredients: Mapping[str, Mapping[str, Any]],
    as_of: datetime,
) -> list[dict[str, Any]]:
    by_role: dict[str, list[Mapping[str, Any]]] = {role: [] for role in ROLES}
    for record in records:
        by_role[record["role"]].append(record)
    components = []

    fit_state = "UNKNOWN" if decision == "UNKNOWN" else "EVALUATED"
    components.append(_component(
        "strategic_fit", "recipe-engine", fit_state, 1.0 if decision == "MATCH" else 0.0,
        "membership decision: MATCH=1, NO_MATCH=0, UNKNOWN=null", decision=decision))

    amps = by_role["AMPLIFIER"]
    present = sorted(a["clause_id"] for a in amps if a["outcome"] == "SATISFIED")
    unknown = sorted(a["clause_id"] for a in amps if a["outcome"] == "UNKNOWN")
    if not amps:
        state = "NOT_EVALUATED"
    else:
        state = "UNKNOWN" if unknown else "EVALUATED"
    components.append(_component(
        "signal_strength", "recipe-engine", state, len(present),
        "count of declared amplifiers satisfied by bound evidence (null while any is UNKNOWN)",
        amplifiers_present=present, amplifiers_unknown=unknown, amplifiers_declared=len(amps)))

    deadlines = sorted(
        (m["ingredient"], m["value"]) for m in measurements
        if ingredients[m["ingredient"]]["value_kind"] == "DATE"
        and m["ingredient"] in {i["ingredient"] for i in compiled["input_signals"]}
    )
    clock = compiled["process_clock"]
    clock_states = sorted({m["value"] for m in measurements if clock and m["ingredient"] == f"clock.{clock['clock']}"})
    if deadlines:
        days = min(_days_until(value, as_of) for _, value in deadlines)
        components.append(_component(
            "urgency", "recipe-engine", "EVALUATED", days, "days from as_of to the nearest recipe deadline",
            deadlines=[{"ingredient": i, "date": v} for i, v in deadlines], clock=clock, clock_states=clock_states))
    else:
        components.append(_component(
            "urgency", "recipe-engine", "UNKNOWN" if clock else "NOT_EVALUATED", None,
            "days to the nearest recipe deadline; no deadline measured", clock=clock, clock_states=clock_states))

    risk = by_role["DISQUALIFIER"] + [r for r in by_role["NEGATIVE"]]
    found = sorted(r["clause_id"] for r in risk if r["outcome"] == "CONTRADICTED")
    risk_unknown = sorted(r["clause_id"] for r in risk if r["outcome"] == "UNKNOWN")
    components.append(_component(
        "downside_risk", "recipe-engine", "UNKNOWN" if risk_unknown else "EVALUATED", len(found),
        "count of disqualifiers and defeaters found (null while any is UNKNOWN)",
        found=found, unknown=risk_unknown))

    components.append(_component(
        "evidence_confidence", "landos-engine", "NOT_EVALUATED", None,
        "owned by the LandOS evaluator contract; not re-implemented in the recipe compiler"))

    known = [r for r in records if r["outcome"] != "UNKNOWN" and r["role"] != "AMPLIFIER"]
    families = sorted({evidence_by_id[e]["source_family"] for r in known for e in r["evidence_ids"]})
    components.append(_component(
        "source_independence_diversity", "recipe-engine", "EVALUATED" if known else "UNKNOWN", len(families),
        "distinct source families behind known (satisfied or contradicted) findings",
        source_families=families))

    scoped = [r for r in records if r["role"] != "AMPLIFIER"]
    known_count = sum(1 for r in scoped if r["outcome"] != "UNKNOWN")
    components.append(_component(
        "data_completeness", "recipe-engine", "EVALUATED" if scoped else "NOT_EVALUATED",
        round(known_count / len(scoped), 4) if scoped else None,
        "known / declared membership, defeater, qualifying and disqualifier findings",
        known=known_count, declared=len(scoped)))

    if economics["state"] == "UNDEFINED":
        econ_state, econ_value = "NOT_EVALUATED", None
    elif economics["outcome"] == "UNKNOWN":
        econ_state, econ_value = "UNKNOWN", None
    else:
        econ_state, econ_value = "EVALUATED", 1.0 if economics["outcome"] == "PASS" else 0.0
    components.append(_component(
        "economic_value", "landos-engine", econ_state, econ_value,
        "recipe economics gate: PASS=1, FAIL=0; valuation itself is owned by LandOS",
        gate_state=economics["state"], gate_outcome=economics["outcome"], basis=economics["basis"]))

    components.append(_component(
        "contactability", "neuruh-contact-intelligence", "NOT_EVALUATED", None,
        "never inferred from phone presence and never a disqualifier"))
    components.append(_component(
        "liquidity", "neuruh-liquidity-engine", "UNWIRED", None,
        "no canonical liquidity runtime in this repository"))
    return components


def _economics(
    compiled: Mapping[str, Any],
    context: Mapping[str, Any],
    measurements: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    ingredients: Mapping[str, Mapping[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    spec = compiled["economics"]
    gates = []
    for index, gate in enumerate(spec["gates"]):
        record = _evaluate_predicate(gate, f"economics[{index}]", context, measurements, evidence_by_id,
                                     ingredients, as_of)
        gates.append(record)
    if spec["state"] == "UNDEFINED":
        outcome = "UNDEFINED"
    elif any(g["outcome"] == "CONTRADICTED" for g in gates):
        outcome = "FAIL"
    elif all(g["outcome"] == "SATISFIED" for g in gates):
        outcome = "PASS"
    else:
        outcome = "UNKNOWN"
    used = {e for g in gates for e in g["evidence_ids"]}
    used_measurements = [m for m in measurements
                         if any(g["signal_type"] == m["ingredient"] for g in gates)
                         and set(m["evidence_refs"]) & used]
    basis = None
    if used_measurements:
        basis = min((m["basis"] for m in used_measurements), key=lambda b: _BASIS_RANK[b])
    facts = [
        {"measurement_id": m["measurement_id"], "ingredient": m["ingredient"], "value": m["value"],
         "unit": m["unit"], "basis": m["basis"]}
        for m in measurements
        if m["ingredient"] in ("equity.estimated_pct", "tax.delinquent_amount", "valuation.assessed_value",
                               "valuation.land_value", "valuation.improvement_value")
    ]
    return {"state": spec["state"], "outcome": outcome, "basis": basis, "gates": gates,
            "facts": facts, "note": spec["note"]}


def _resolvability(record: Mapping[str, Any], ingredients: Mapping[str, Mapping[str, Any]]) -> tuple[float, str]:
    if record["kind"] == "UNDEFINED_PREDICATE":
        return 0.0, "PREDICATE_UNDEFINED"
    names = [a["signal_type"] for a in record["alternatives"]] if record["kind"] == "ANY_OF" else [record["signal_type"]]
    best = max(names, key=lambda n: NBE_RESOLVABILITY[ingredients[n]["source_state"]])
    state = ingredients[best]["source_state"]
    return NBE_RESOLVABILITY[state], state


def _next_best_evidence(
    compiled: Mapping[str, Any],
    decision: str,
    records: Sequence[Mapping[str, Any]],
    economics: Mapping[str, Any],
    ingredients: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Missing evidence, ranked.  A known exclusion is a finding: nothing to plan."""
    if decision == "NO_MATCH":
        return []
    scopes: dict[str, list[Mapping[str, Any]]] = {"MEMBERSHIP": [], "QUALIFICATION": [], "ECONOMICS": []}
    for record in records:
        if record["outcome"] != "UNKNOWN":
            continue
        if record["role"] == "REQUIRED" and decision == "UNKNOWN":
            scopes["MEMBERSHIP"].append(record)
        elif record["role"] in ("QUALIFYING", "DISQUALIFIER"):
            scopes["QUALIFICATION"].append(record)
    if economics["state"] == "DEFINED":
        membership_ingredients = {r["signal_type"] for r in records if r["role"] == "REQUIRED"}
        for gate in economics["gates"]:
            if gate["outcome"] == "UNKNOWN" and gate["signal_type"] not in membership_ingredients:
                scopes["ECONOMICS"].append({**gate, "role": "ECONOMICS", "kind": "PREDICATE", "label": "economics gate"})
    plan_by_ingredient = {p["ingredient"]: p for p in compiled["nbe_plan"]}
    gaps = []
    for scope, items in scopes.items():
        for record in items:
            resolvability, source_state = _resolvability(record, ingredients)
            gain = round(NBE_IMPACT[scope] / len(items) * resolvability, 4)
            names = [a["signal_type"] for a in record.get("alternatives", [])] or record["signal_type"].split("|")
            gaps.append({
                "gap_id": "gap_" + content_hash({"recipe_id": compiled["recipe_id"], "clause_id": record["clause_id"],
                                                 "scope": scope}, omit=())[:24],
                "requirement_id": record["clause_id"],
                "role": record["role"],
                "blocks": scope,
                "decides_membership": scope == "MEMBERSHIP" and len(items) == 1,
                "evaluation_state": "CONFLICTED" if record.get("observed_state") == "CONFLICTED" else "UNKNOWN",
                "reason_codes": list(record["reason_codes"]),
                "ingredients": names,
                "questions": sorted({(plan_by_ingredient.get(n) or {"question": ingredients[n]["nbe_question"]})["question"]
                                     for n in names if n in ingredients}),
                "source_state": source_state,
                "unlock_families": sorted({ingredients[n]["unlock_family"] for n in names if n in ingredients}),
                "information_gain": gain,
                "formula": NBE_FORMULA,
                "label": record["label"],
            })
    order = {"MEMBERSHIP": 0, "QUALIFICATION": 1, "ECONOMICS": 2}
    gaps.sort(key=lambda g: (-g["information_gain"], order[g["blocks"]], g["requirement_id"]))
    for order_index, gap in enumerate(gaps, start=1):
        gap["nbe_order"] = order_index  # planner order within this recipe, never an opportunity rank
    return gaps


def _qualification(
    compiled: Mapping[str, Any],
    decision: str,
    records: Sequence[Mapping[str, Any]],
    economics: Mapping[str, Any],
    ingredients: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    policy = compiled["qualification_policy"]
    base = {
        "action_constraints": policy["action_constraints"],
        "manual_checks": policy["manual_checks"],
        "economics_basis": economics["basis"],
        "action_authority": "NOT_EVALUATED",
    }

    def result(state: str, reasons: list[str], blocking: list[str]) -> dict[str, Any]:
        return {"state": state, "reasons": reasons, "blocking_requirements": sorted(blocking), **base}

    if compiled["kind"] == "META":
        return result("NOT_APPLICABLE", ["META projection"], [])
    if decision == "NO_MATCH":
        return result("NOT_ELIGIBLE", ["membership contradicted by decisive bound evidence"], [])
    unresolved = [r for r in records if r["role"] == "REQUIRED" and r["outcome"] != "SATISFIED"]
    if decision == "UNKNOWN":
        blocking = [r["clause_id"] for r in unresolved]
        if any(r["kind"] == "UNDEFINED_PREDICATE" for r in unresolved):
            return result("PREDICATE_UNDEFINED", ["a membership predicate has no defined threshold"], blocking)

        def producible(record: Mapping[str, Any]) -> bool:
            names = [a["signal_type"] for a in record["alternatives"]] if record["kind"] == "ANY_OF" else [record["signal_type"]]
            return any(ingredients[n]["source_state"] not in NO_PRODUCER_SOURCE_STATES for n in names)

        unproducible = [r["clause_id"] for r in unresolved if not producible(r)]
        if unproducible:
            # MATCH is unreachable until a source exists; NO_MATCH may still be learned.
            return result("SOURCE_BLOCKED", [f"no producer exists for membership predicate {c}" for c in unproducible],
                          blocking)
        return result("NEEDS_EVIDENCE", ["membership unresolved"], blocking)

    disq = [r for r in records if r["role"] == "DISQUALIFIER"]
    qual = [r for r in records if r["role"] == "QUALIFYING"]
    hard = [r["clause_id"] for r in disq if r["outcome"] == "CONTRADICTED" and r["negative_evidence_decisive"]]
    if hard:
        return result("DISQUALIFIED", [f"hard disqualifier found: {c}" for c in hard], hard)
    if economics["outcome"] == "FAIL":
        return result("NOT_QUALIFIED", ["economics below the recipe gate"], [])
    refused = [r["clause_id"] for r in qual if r["outcome"] == "CONTRADICTED" and r["negative_evidence_decisive"]]
    if refused:
        return result("NOT_QUALIFIED", [f"qualifying evidence contradicted: {c}" for c in refused], refused)
    open_items = [r["clause_id"] for r in disq + qual if r["outcome"] != "SATISFIED"]
    reasons = [f"unresolved: {c}" for c in open_items]
    if economics["outcome"] == "UNKNOWN":
        reasons.append("economics gate inputs unknown")
    if open_items or economics["outcome"] == "UNKNOWN":
        return result("NEEDS_EVIDENCE", reasons, open_items)
    return result("QUALIFIED", ["membership, disqualifiers, qualifying evidence and economics all resolved"], [])


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------

def evaluate_compiled_recipe(
    compiled: Mapping[str, Any],
    context: Mapping[str, Any],
    measurements: Sequence[Mapping[str, Any]],
    ingredients: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """ONE compiled recipe against ONE canonical context.  Pure."""
    if compiled["kind"] == "META":
        raise RecipeCompilerError(f"{compiled['recipe_id']} is a META projection, not an evaluable recipe")
    as_of = parse_timestamp(context["as_of"], "context.as_of")
    evidence_by_id = {e["evidence_id"]: e for e in context["evidence"]}
    records = [
        _evaluate_requirement(req, context, measurements, evidence_by_id, ingredients, as_of)
        for req in compiled["requirements"]
    ]
    decision, reason = derive_decision([r for r in records if r["in_decision"]])
    economics = _economics(compiled, context, measurements, evidence_by_id, ingredients, as_of)
    used = sorted({e for r in records if r["outcome"] != "UNKNOWN" for e in r["evidence_ids"]}
                  | {e for g in economics["gates"] for e in g["evidence_ids"]})
    body: dict[str, Any] = {
        "schema_version": RECIPE_EVALUATION_CONTRACT,
        "producer_owner": PRODUCER_OWNER,
        "compiler_version": COMPILER_VERSION,
        "mode": context["mode"],
        "tenant_id": context["tenant_id"],
        "universe_id": context["universe_id"],
        "opportunity_id": context["opportunity_id"],
        "asset_id": context["asset_id"],
        "recipe_n": compiled["recipe_n"],
        "recipe_id": compiled["recipe_id"],
        "recipe_digest": compiled["recipe_digest"],
        "membership_key": membership_key(
            context["tenant_id"], context["universe_id"], context["opportunity_id"], compiled["recipe_id"]),
        "context_digest": content_hash(dict(context), omit=()),
        "decision": decision,
        "reason": reason,
        "clauses": records,
        "required_evidence": [
            {"requirement_id": r["clause_id"], "role": r["role"], "outcome": r["outcome"],
             "evidence_ids": r["evidence_ids"], "label": r["label"]}
            for r in records if r["role"] in ("REQUIRED", "QUALIFYING", "DISQUALIFIER")
        ],
        "missing_evidence": [
            {"requirement_id": r["clause_id"], "role": r["role"], "reason_codes": r["reason_codes"],
             "ingredients": r["signal_type"].split("|"), "label": r["label"]}
            for r in records if r["role"] in ("REQUIRED", "QUALIFYING", "DISQUALIFIER") and r["outcome"] == "UNKNOWN"
        ],
        "next_best_evidence": _next_best_evidence(compiled, decision, records, economics, ingredients),
        "economics": economics,
        "evidence_digests": {e: evidence_by_id[e]["content_sha256"] for e in used},
        "evaluated_at": render_timestamp(as_of),
    }
    body["score_vector"] = _score_vector(compiled, decision, records, measurements, evidence_by_id,
                                         economics, ingredients, as_of)
    body["qualification"] = _qualification(compiled, decision, records, economics, ingredients)
    body["content_sha256"] = content_hash(body)
    return body


def _data_gap_projection(meta: Mapping[str, Any], evaluations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """R30: UNKNOWN memberships blocked by exactly one unresolved membership predicate."""
    items = []
    for ev in evaluations:
        if ev["decision"] != "UNKNOWN":
            continue
        unresolved = [c for c in ev["clauses"] if c["role"] == "REQUIRED" and c["outcome"] != "SATISFIED"]
        if len(unresolved) != 1:
            continue
        gap = next((g for g in ev["next_best_evidence"] if g["decides_membership"]), None)
        items.append({
            "recipe_n": ev["recipe_n"],
            "recipe_id": ev["recipe_id"],
            "requirement_id": unresolved[0]["clause_id"],
            "ingredients": unresolved[0]["signal_type"].split("|"),
            "gap_id": None if gap is None else gap["gap_id"],
            "source_state": None if gap is None else gap["source_state"],
            "questions": [] if gap is None else gap["questions"],
        })
    return {"recipe_n": meta["recipe_n"], "recipe_id": meta["recipe_id"], "recipe_digest": meta["recipe_digest"],
            "kind": "META", "blocked_by_one_fact": items}


def evaluate_canonical_recipes(
    evaluation_input: Mapping[str, Any],
    compiled_set: Mapping[str, Any] | None = None,
    *,
    lowered_validity_seconds: int = 86400,
) -> dict[str, Any]:
    """Every compiled recipe, independently, against one candidate.

    Returns every membership (MATCH / NO_MATCH / UNKNOWN) -- never a primary --
    plus, for LOWERED recipes, the P1 eligibility receipt and a parity check
    that the compiled decision is the P1 decision."""
    compiled_set = validate_compiled_set(compiled_set if compiled_set is not None else compile_canonical_recipes())
    ingredients = compiled_set["ingredients"]
    context, measurements = validate_evaluation_input(evaluation_input, ingredients)
    lowered_context = lower_context(context, ingredients)
    evaluations = []
    p1_receipts = []
    meta = []
    for compiled in compiled_set["recipes"]:
        if compiled["kind"] == "META":
            meta.append(compiled)
            continue
        evaluation = evaluate_compiled_recipe(compiled, context, measurements, ingredients)
        policy = lower_to_eligibility_policy(
            compiled, ingredients, tenant_id=context["tenant_id"], validity_seconds=lowered_validity_seconds)
        if policy is not None:
            receipt = evaluate_eligibility(lowered_context, policy).to_dict()
            if receipt["decision"] != evaluation["decision"]:
                raise RecipeCompilerError(
                    f"{compiled['recipe_id']}: compiled decision {evaluation['decision']} != "
                    f"P1 decision {receipt['decision']} -- the lowering changed the predicate"
                )
            p1_receipts.append(receipt)
        evaluations.append(evaluation)
    body: dict[str, Any] = {
        "schema_version": RECIPE_EVALUATION_SET_CONTRACT,
        "producer_owner": PRODUCER_OWNER,
        "compiler_version": COMPILER_VERSION,
        "compiled_set_digest": compiled_set["set_digest"],
        "context_digest": content_hash(dict(context), omit=()),
        "tenant_id": context["tenant_id"],
        "universe_id": context["universe_id"],
        "opportunity_id": context["opportunity_id"],
        "asset_id": context["asset_id"],
        "evaluated_at": context["as_of"],
        "memberships": [
            {"recipe_n": e["recipe_n"], "recipe_id": e["recipe_id"], "membership_key": e["membership_key"],
             "decision": e["decision"], "qualification": e["qualification"]["state"]}
            for e in evaluations
        ],
        "evaluations": evaluations,
        "p1_eligibility_receipts": p1_receipts,
        "data_gap_nbe": [_data_gap_projection(m, evaluations) for m in meta],
    }
    body["content_sha256"] = content_hash(body)
    return body


# --------------------------------------------------------------------------
# consumer projections (lead-recipe-os kernel shapes)
# --------------------------------------------------------------------------

_LRO_EVALUATION_STATES = frozenset({
    "NOT_EVALUATED", "EVALUATED", "UNWIRED", "SOURCE_FAILED", "UNKNOWN", "ABSENT", "CONFLICTED", "ZERO_RESULTS",
})


def to_score_vector_components(evaluation: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``neuruh.lead-recipe-os.score-vector.v1`` components for one recipe evaluation.

    Names are ``<recipe_id>.<dimension>`` so N recipes compose into one vector
    without collapsing into a single score."""
    out = []
    for component in evaluation["score_vector"]:
        state = component["evaluation_state"]
        if state not in _LRO_EVALUATION_STATES:
            raise RecipeCompilerError(f"score state {state!r} is not a lead-recipe-os evaluation state")
        out.append({
            "name": f"{evaluation['recipe_id']}.{component['name']}",
            "organ": component["organ"],
            "evaluation_state": state,
            "value": component["value"],
            "formula": component["formula"],
            "mode": "FIXTURE" if evaluation["mode"] in ("FIXTURE", "SYNTHETIC") else "LIVE",
            "receipt_sha256": evaluation["content_sha256"],
        })
    return out


def to_next_best_evidence_gaps(evaluation: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``neuruh.lead-recipe-os.next-best-evidence.v1`` gap items (additionalProperties false)."""
    return [
        {
            "gap_id": gap["gap_id"],
            "kind": "missing_evidence",
            "organ": "recipe-engine",
            "evaluation_state": gap["evaluation_state"],
            "expected_information": f"{evaluation['recipe_id']}.{gap['requirement_id']}: "
                                    + "; ".join(gap["questions"] or [gap["label"]]),
            "information_gain": gap["information_gain"],
            "blocking_lead": gap["blocks"] != "ECONOMICS",
        }
        for gap in evaluation["next_best_evidence"]
    ]


def compiled_summary_rows(compiled_set: Mapping[str, Any]) -> list[dict[str, str]]:
    """One human row per recipe (the TSV artifact)."""
    rows = []
    for recipe in compiled_set["recipes"]:
        reqs = recipe["requirements"]

        def ids(role: str) -> str:
            return ";".join(
                f"{r['requirement_id']}={'|'.join(_requirement_ingredients(r))}" for r in reqs if r["role"] == role
            ) or "-"

        rows.append({
            "recipe_n": str(recipe["recipe_n"]),
            "recipe_id": recipe["recipe_id"],
            "kind": recipe["kind"],
            "compile_state": recipe["compile_state"],
            "p1_lowering": recipe["p1_lowering"]["state"],
            "membership_predicates": ids("REQUIRED"),
            "defeaters": ids("NEGATIVE"),
            "qualifying": ids("QUALIFYING"),
            "disqualifiers": ids("DISQUALIFIER"),
            "amplifiers": ids("AMPLIFIER"),
            "economics": recipe["economics"]["state"],
            "process_clock": (recipe["process_clock"] or {}).get("clock") or "-",
            "live_registry_id": recipe["aliases"]["live_registry_id"] or "-",
            "materialized_recipe_ids": ";".join(recipe["aliases"]["materialized_recipe_ids"]) or "-",
            "recipe_digest": recipe["recipe_digest"],
        })
    return rows


__all__ = [
    "COMPILED_RECIPE_CONTRACT",
    "COMPILED_RECIPE_SET_CONTRACT",
    "COMPILER_VERSION",
    "RECIPE_EVALUATION_CONTRACT",
    "RECIPE_EVALUATION_INPUT_CONTRACT",
    "RECIPE_EVALUATION_SET_CONTRACT",
    "RecipeCompilerError",
    "compile_canonical_recipes",
    "compile_recipe",
    "compiled_summary_rows",
    "evaluate_canonical_recipes",
    "evaluate_compiled_recipe",
    "lower_context",
    "lower_to_eligibility_policy",
    "to_next_best_evidence_gaps",
    "to_score_vector_components",
    "validate_compiled_set",
    "validate_evaluation_input",
    "validate_recipe_spec",
]

