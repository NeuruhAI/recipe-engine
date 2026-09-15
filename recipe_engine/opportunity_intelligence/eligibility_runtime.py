"""Opportunity eligibility runtime: the compiled canonical recipes over a real universe.

P1B sealed ``RUNTIME_CALL_PATH=CALLER_ABSENT``: the independent producer
(:mod:`.eligibility`) and the recipe compiler (:mod:`.recipe_compiler`) existed,
no runtime called them, and three seams were missing -- a live context
producer, a production policy catalog, and a receipt channel.  The compiler
closed the catalog seam (the canonical 30, content-addressed).  This module is
the source-independent half of the runtime; ``scripts/run_opportunity_eligibility.py``
is the caller that owns the source contract, the files, the commit and the
database.

What it does, for every property of a universe and every compiled recipe,
independently:

* evaluates through ``recipe_compiler.evaluate_canonical_recipes`` -- the one
  evaluator, whose decisions are ``eligibility.derive_decision`` and whose
  lowered recipes are asserted equal to the P1 receipt;
* keeps every decision as an observation keyed by the Lead Recipe OS
  ``membership_key``: MATCH is the only positive membership, NO_MATCH and
  UNKNOWN stay observable and are counted separately, never as zero;
* projects MATCH -- and only MATCH -- onto the DeedSonar membership consumer
  (``candidate_recipe_membership``, class ELIGIBLE, unranked, unscored);
* seals a run receipt: input hash, output hashes, pipeline version, code
  commit, schema versions, ``as_of`` and ``generated_at``, parent receipt.

Contracts, not conventions:

* rank != eligibility.  Nothing here reads, emits or orders by rank, score,
  tier or primary recipe; a projection that would carry one is refused.
* recipe membership != result-set membership.  A membership is one
  (opportunity, recipe) decision from recipe evidence; no query, cohort or
  filter produces one.
* UNKNOWN != zero.  A recipe nobody could decide is UNKNOWN in the census and
  absent from the membership projection; it is never written as NO_MATCH, as
  a 0 score, or as a missing count.

Pure: no I/O, no clock, no database.  ``generated_at`` and ``code`` are handed
in by the caller and recorded, never computed here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import timedelta
import re
from typing import Any

from .eligibility import (
    DECISIONS,
    NEVER_EMITTED_RECEIPT_KEYS,
    PRODUCER_VERSION,
    EligibilityError,
    consumer_canonical_json,
    content_hash,
    membership_key,
    parse_timestamp,
    render_timestamp,
)
from .recipe_compiler import (
    COMPILED_RECIPE_SET_CONTRACT,
    COMPILER_VERSION,
    QUALIFICATION_STATES,
    RECIPE_EVALUATION_CONTRACT,
    RECIPE_EVALUATION_INPUT_CONTRACT,
    RECIPE_EVALUATION_SET_CONTRACT,
    evaluate_canonical_recipes,
    validate_compiled_set,
)

PIPELINE_NAME = "recipe-engine/opportunity-eligibility-runtime"
PIPELINE_VERSION = f"{PIPELINE_NAME}@1.0.0"
RUN_RECEIPT_CONTRACT = "neuruh.recipe-engine.opportunity-eligibility-run.v1"
PROPERTY_DECISIONS_CONTRACT = "neuruh.recipe-engine.property-eligibility-decisions.v1"
MEMBERSHIP_CONTRACT = "neuruh.recipe-engine.eligibility-membership.v1"
OBSERVATION_CONTRACT = "neuruh.recipe-engine.membership-observation.v1"
DEEDSONAR_APPLY_CONTRACT = "deedsonar.candidate-recipe-membership-eligibility-apply.v1"
DEEDSONAR_MEMBERSHIP_CLASS = "ELIGIBLE"

#: Roles whose findings speak to the membership itself.  Amplifiers only feed
#: the score vector: an absent amplifier is not evidence against a match.
_EXPLAINED_ROLES = ("REQUIRED", "NEGATIVE", "QUALIFYING", "DISQUALIFIER")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_VERSION = re.compile(r"^[a-z0-9_]+@compiled\.[0-9a-f]{12}$")

#: Keys no runtime artifact may carry: ranking, routing, contact and CRM
#: authority.  Superset of the P1 receipt ban.
FORBIDDEN_PROJECTION_KEYS = frozenset(NEVER_EMITTED_RECEIPT_KEYS | {
    "recipe_score", "recipe_membership_count", "curative_priority_score",
    "opportunity_score", "membership_class_rank", "ranking_universe_id",
})


class EligibilityRuntimeError(EligibilityError):
    """The universe, run or projection cannot be trusted as stated."""


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

def compiled_recipe_version(compiled_recipe: Mapping[str, Any]) -> str:
    """The version id a compiled recipe carries everywhere downstream.

    Identical to the ``recipe_version_id`` ``lower_to_eligibility_policy``
    stamps on a P1 policy, so a P1 receipt and a DeedSonar membership row name
    the same recipe version."""
    return f"{compiled_recipe['recipe_id']}@compiled.{compiled_recipe['recipe_digest'][:12]}"


def run_id_for(*, as_of: str, input_sha256: str, config_sha256: str) -> str:
    """Deterministic run identity: same as_of + same input + same config -> same id."""
    day = parse_timestamp(as_of, "as_of").strftime("%Y%m%d")
    return f"eligibility_run_{day}_{input_sha256[:12]}_{config_sha256[:8]}"


def _refuse_forbidden(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        leaked = sorted(FORBIDDEN_PROJECTION_KEYS & set(map(str, value.keys())))
        if leaked:
            raise EligibilityRuntimeError(f"{path} carries {leaked}: rank/routing/contact is not eligibility")
        for key, item in value.items():
            _refuse_forbidden(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _refuse_forbidden(item, f"{path}[{index}]")


# --------------------------------------------------------------------------
# one property
# --------------------------------------------------------------------------

def _clause_ingredients(clause: Mapping[str, Any]) -> list[str]:
    if clause.get("alternatives"):
        return [alt["signal_type"] for alt in clause["alternatives"]]
    return [part for part in str(clause["signal_type"]).split("|") if part]


def membership_explanation(
    evaluation: Mapping[str, Any],
    *,
    compiled_recipe: Mapping[str, Any],
    evidence_families: Mapping[str, str],
    run_id: str,
    input_sha256: str,
    valid_until: str,
) -> dict[str, Any]:
    """The receipt a DeedSonar membership row carries in ``explanation``.

    It is the recipe evaluation, reduced to what the membership asserts, and it
    answers the questions DeedSonar's reader already asks of a why-matched
    receipt (``signals_triggered``, ``negative_evidence``, ``unknown_inputs``,
    ``disqualifiers_checked``, ``source_ids``, ``coverage_state``).  Those keys
    keep their meaning:

    * ``disqualifiers_checked`` names only disqualifiers that were EVALUATED
      and clear.  An unresolved disqualifier is in ``disqualifiers_unresolved``
      and ``unknown_inputs`` -- listing it as checked would read UNKNOWN as PASS.
    * ``negative_evidence`` is non-decisive contradiction: weighed, recorded,
      and not the opposite fact.
    """
    if evaluation["decision"] != "MATCH":
        raise EligibilityRuntimeError("only a MATCH is a membership; NO_MATCH and UNKNOWN stay observations")
    explained = [c for c in evaluation["clauses"] if c["role"] in _EXPLAINED_ROLES]
    triggered: list[str] = []
    for clause in evaluation["clauses"]:
        if clause["role"] != "REQUIRED" or clause["outcome"] != "SATISFIED":
            continue
        if clause.get("alternatives"):
            triggered.extend(a["signal_type"] for a in clause["alternatives"] if a["outcome"] == "SATISFIED")
        else:
            triggered.append(clause["signal_type"])
    negative = sorted(
        f"{c['clause_id']}:{c['signal_type']}"
        for c in explained
        if c["outcome"] == "CONTRADICTED" and not c["negative_evidence_decisive"]
    )
    unknown = sorted({i for c in explained if c["outcome"] == "UNKNOWN" for i in _clause_ingredients(c)})
    known_evidence = {e for c in evaluation["clauses"] if c["outcome"] != "UNKNOWN" for e in c["evidence_ids"]}
    families = sorted({evidence_families[e] for e in known_evidence if e in evidence_families})
    qualification = evaluation["qualification"]
    body: dict[str, Any] = {
        "schema_version": MEMBERSHIP_CONTRACT,
        "decision": "MATCH",
        "reason": evaluation["reason"],
        "recipe_id": evaluation["recipe_id"],
        "recipe_n": evaluation["recipe_n"],
        "recipe_version": compiled_recipe_version(compiled_recipe),
        "recipe_digest": evaluation["recipe_digest"],
        "canonical_name": compiled_recipe["canonical_name"],
        "membership_key": evaluation["membership_key"],
        "opportunity_id": evaluation["opportunity_id"],
        "asset_id": evaluation["asset_id"],
        "evaluation_sha256": evaluation["content_sha256"],
        "evaluation_contract": RECIPE_EVALUATION_CONTRACT,
        "context_digest": evaluation["context_digest"],
        "evidence_digests": dict(evaluation["evidence_digests"]),
        "evaluated_at": evaluation["evaluated_at"],
        "valid_until": valid_until,
        "mode": evaluation["mode"],
        "requirements": [
            {
                "clause_id": c["clause_id"],
                "role": c["role"],
                "outcome": c["outcome"],
                "reason_codes": list(c["reason_codes"]),
                "ingredients": _clause_ingredients(c),
            }
            for c in explained
        ],
        "qualification": {
            "state": qualification["state"],
            "blocking_requirements": list(qualification["blocking_requirements"]),
            "action_authority": qualification["action_authority"],
        },
        "signals_triggered": sorted(set(triggered)),
        "negative_evidence": negative,
        "unknown_inputs": unknown,
        "disqualifiers_checked": sorted(
            c["clause_id"] for c in explained if c["role"] == "DISQUALIFIER" and c["outcome"] == "SATISFIED"
        ),
        "disqualifiers_unresolved": sorted(
            c["clause_id"] for c in explained if c["role"] == "DISQUALIFIER" and c["outcome"] == "UNKNOWN"
        ),
        "source_ids": families,
        "coverage_state": "COMPLETE" if not evaluation["missing_evidence"] else "PARTIAL",
        "producer": {
            "owner": "recipe-engine",
            "pipeline_version": PIPELINE_VERSION,
            "compiler_version": COMPILER_VERSION,
            "eligibility_producer_version": PRODUCER_VERSION,
        },
        "run_id": run_id,
        "input_sha256": input_sha256,
        "membership_semantics": "MATCH_ONLY: NO_MATCH and UNKNOWN are observations, never memberships",
    }
    body["content_sha256"] = content_hash(body)
    _refuse_forbidden(body, "explanation")
    return body


def evaluate_property(
    evaluation_input: Mapping[str, Any],
    compiled_set: Mapping[str, Any],
    *,
    property_key: str,
    source_row_id: str,
    run_id: str,
    input_sha256: str,
    validity_seconds: int,
) -> dict[str, Any]:
    """Every compiled recipe against one property; decisions + MATCH projections.

    ``compiled_set`` must already be validated by the caller (once per run)."""
    if validity_seconds <= 0:
        raise EligibilityRuntimeError("validity_seconds must be positive")
    evaluation_set = evaluate_canonical_recipes(
        evaluation_input, compiled_set, lowered_validity_seconds=validity_seconds)
    if evaluation_set["schema_version"] != RECIPE_EVALUATION_SET_CONTRACT:
        raise EligibilityRuntimeError("compiler returned an unexpected evaluation-set contract")
    context = evaluation_input["context"]
    evidence_families = {e["evidence_id"]: e["source_family"] for e in context["evidence"]}
    as_of = parse_timestamp(evaluation_set["evaluated_at"], "evaluated_at")
    valid_until = render_timestamp(as_of + timedelta(seconds=validity_seconds))
    compiled_by_id = {r["recipe_id"]: r for r in compiled_set["recipes"]}

    decisions: dict[str, list[str]] = {}
    memberships: list[dict[str, Any]] = []
    for evaluation in evaluation_set["evaluations"]:
        decision = evaluation["decision"]
        if decision not in DECISIONS:
            raise EligibilityRuntimeError(f"{evaluation['recipe_id']}: decision {decision!r} is not a decision")
        if decision == "NO_MATCH" and not any(
            c["outcome"] == "CONTRADICTED" and c["negative_evidence_decisive"] and c["in_decision"]
            for c in evaluation["clauses"]
        ):
            raise EligibilityRuntimeError(f"{evaluation['recipe_id']}: NO_MATCH without a decisive contradiction")
        qualification = evaluation["qualification"]["state"]
        if qualification not in QUALIFICATION_STATES:
            raise EligibilityRuntimeError(f"{evaluation['recipe_id']}: unknown qualification {qualification!r}")
        decisions[evaluation["recipe_id"]] = [decision, qualification, evaluation["content_sha256"]]
        if decision != "MATCH":
            continue
        compiled = compiled_by_id[evaluation["recipe_id"]]
        explanation = membership_explanation(
            evaluation, compiled_recipe=compiled, evidence_families=evidence_families,
            run_id=run_id, input_sha256=input_sha256, valid_until=valid_until)
        memberships.append({
            "schema_version": MEMBERSHIP_CONTRACT,
            "property_key": property_key,
            "source_row_id": source_row_id,
            "recipe_id": evaluation["recipe_id"],
            "recipe_version": explanation["recipe_version"],
            "membership_class": DEEDSONAR_MEMBERSHIP_CLASS,
            "membership_key": evaluation["membership_key"],
            "evaluation_sha256": evaluation["content_sha256"],
            "evaluated_at": evaluation["evaluated_at"],
            "valid_until": valid_until,
            "explanation": explanation,
        })
    for p1 in evaluation_set["p1_eligibility_receipts"]:
        observed = decisions.get(p1["recipe_id"])
        if observed is None or observed[0] != p1["decision"]:
            raise EligibilityRuntimeError(f"{p1['recipe_id']}: P1 receipt and compiled decision diverge")
    record = {
        "schema_version": PROPERTY_DECISIONS_CONTRACT,
        "property_key": property_key,
        "source_row_id": source_row_id,
        "tenant_id": evaluation_set["tenant_id"],
        "universe_id": evaluation_set["universe_id"],
        "opportunity_id": evaluation_set["opportunity_id"],
        "asset_id": evaluation_set["asset_id"],
        "context_digest": evaluation_set["context_digest"],
        "evaluation_set_sha256": evaluation_set["content_sha256"],
        "compiled_set_digest": evaluation_set["compiled_set_digest"],
        "evaluated_at": evaluation_set["evaluated_at"],
        "valid_until": valid_until,
        "run_id": run_id,
        # recipe_id -> [decision, qualification, recipe-evaluation content_sha256]
        "decisions": {k: decisions[k] for k in sorted(decisions)},
        "p1_receipts": sorted(p1["content_sha256"] for p1 in evaluation_set["p1_eligibility_receipts"]),
    }
    record["content_sha256"] = content_hash(record)
    _refuse_forbidden(record, "decisions")
    for membership in memberships:
        _refuse_forbidden(membership, "membership")
    return {"decisions": record, "memberships": memberships}


# --------------------------------------------------------------------------
# observations (Lead Recipe OS membership-observation shape)
# --------------------------------------------------------------------------

def observations(decisions_record: Mapping[str, Any], compiled_set: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One observation per (property, recipe), field for field the Lead Recipe OS
    ``membership-observation.v1`` (mode LIVE; the LRO intake court is FIXTURE-only).

    Every decision is observable -- including UNKNOWN and NO_MATCH -- so that
    the absence of a membership row can always be explained and is never read as
    a verdict."""
    versions = {r["recipe_id"]: compiled_recipe_version(r) for r in compiled_set["recipes"]}
    out = []
    for recipe_id, (decision, qualification, sha) in decisions_record["decisions"].items():
        out.append({
            "schema_version": OBSERVATION_CONTRACT,
            "mode": "LIVE",
            "tenant_id": decisions_record["tenant_id"],
            "universe_id": decisions_record["universe_id"],
            "opportunity_id": decisions_record["opportunity_id"],
            "asset_id": decisions_record["asset_id"],
            "recipe_id": recipe_id,
            "recipe_version_id": versions[recipe_id],
            "membership_key": membership_key(
                decisions_record["tenant_id"], decisions_record["universe_id"],
                decisions_record["opportunity_id"], recipe_id),
            "decision": decision,
            "qualification": qualification,
            "receipt_sha256": sha,
            "evaluated_at": decisions_record["evaluated_at"],
            "valid_until": decisions_record["valid_until"],
            "production_write": False,
            "contact_authorized": False,
        })
    return out


# --------------------------------------------------------------------------
# census
# --------------------------------------------------------------------------

class EligibilityCensus:
    """Per-recipe MATCH / NO_MATCH / UNKNOWN counts.  UNKNOWN is its own count."""

    def __init__(self, recipe_ids: Sequence[str]) -> None:
        self._recipes = list(recipe_ids)
        self._decisions: dict[str, Counter[str]] = {r: Counter() for r in self._recipes}
        self._qualification: dict[str, Counter[str]] = {r: Counter() for r in self._recipes}
        self.properties = 0
        self.properties_with_match = 0
        self.memberships = 0

    def add(self, decisions_record: Mapping[str, Any]) -> None:
        self.properties += 1
        matched = 0
        for recipe_id, (decision, qualification, _sha) in decisions_record["decisions"].items():
            self._decisions[recipe_id][decision] += 1
            self._qualification[recipe_id][qualification] += 1
            matched += decision == "MATCH"
        self.memberships += matched
        self.properties_with_match += matched > 0

    def to_dict(self) -> dict[str, Any]:
        by_recipe = {}
        for recipe_id in self._recipes:
            counts = self._decisions[recipe_id]
            decided = {d: counts.get(d, 0) for d in DECISIONS}
            if sum(decided.values()) != self.properties:
                raise EligibilityRuntimeError(f"{recipe_id}: census does not cover every property")
            by_recipe[recipe_id] = {
                **decided,
                "qualification": {q: n for q, n in sorted(self._qualification[recipe_id].items())},
            }
        totals = {d: sum(v[d] for v in by_recipe.values()) for d in DECISIONS}
        return {
            "properties": self.properties,
            "recipes": len(self._recipes),
            "decisions_total": totals,
            "memberships_projected": self.memberships,
            "properties_with_match": self.properties_with_match,
            "properties_without_match": self.properties - self.properties_with_match,
            "by_recipe": by_recipe,
            "semantics": {
                "UNKNOWN": "undecided on the evidence bound; counted, never written as NO_MATCH or 0",
                "NO_MATCH": "a decisive contradiction by bound evidence; observable, not a membership",
                "MATCH": "the only positive membership; projected as DeedSonar ELIGIBLE, unranked",
            },
        }


# --------------------------------------------------------------------------
# run receipt
# --------------------------------------------------------------------------

_RUN_REQUIRED = (
    "schema_version", "run_id", "pipeline", "code", "schema_versions", "compiled_set",
    "tenant_id", "universe_id", "as_of", "valid_until", "validity_seconds", "generated_at",
    "input", "parent_receipt", "config_sha256", "outputs", "census", "invariants",
    "lead_recipe_os", "deedsonar", "content_sha256",
)


def build_run_receipt(
    *,
    run_id: str,
    code: Mapping[str, Any],
    compiled_set: Mapping[str, Any],
    tenant_id: str,
    universe_id: str,
    as_of: str,
    validity_seconds: int,
    generated_at: str,
    input_block: Mapping[str, Any],
    parent_receipt: Mapping[str, Any] | None,
    config: Mapping[str, Any],
    outputs: Mapping[str, Any],
    census: Mapping[str, Any],
    source_contract: str,
    supersedes_run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    as_of_ts = parse_timestamp(as_of, "as_of")
    body: dict[str, Any] = {
        "schema_version": RUN_RECEIPT_CONTRACT,
        "run_id": run_id,
        "pipeline": {"name": PIPELINE_NAME, "version": PIPELINE_VERSION},
        "code": dict(code),
        "schema_versions": {
            "run_receipt": RUN_RECEIPT_CONTRACT,
            "evaluation_input": RECIPE_EVALUATION_INPUT_CONTRACT,
            "evaluation": RECIPE_EVALUATION_CONTRACT,
            "evaluation_set": RECIPE_EVALUATION_SET_CONTRACT,
            "compiled_set": COMPILED_RECIPE_SET_CONTRACT,
            "property_decisions": PROPERTY_DECISIONS_CONTRACT,
            "membership": MEMBERSHIP_CONTRACT,
            "observation": OBSERVATION_CONTRACT,
            "source_contract": source_contract,
            "deedsonar_apply": DEEDSONAR_APPLY_CONTRACT,
        },
        "compiled_set": {
            "set_digest": compiled_set["set_digest"],
            "spec_version": compiled_set["spec_version"],
            "compiler_version": compiled_set["compiler_version"],
            "recipe_count": len(compiled_set["recipes"]),
        },
        "tenant_id": tenant_id,
        "universe_id": universe_id,
        "as_of": render_timestamp(as_of_ts),
        "valid_until": render_timestamp(as_of_ts + timedelta(seconds=validity_seconds)),
        "validity_seconds": validity_seconds,
        "generated_at": generated_at,
        "input": dict(input_block),
        "parent_receipt": None if parent_receipt is None else dict(parent_receipt),
        "supersedes_run": None if supersedes_run is None else dict(supersedes_run),
        "config_sha256": content_hash(dict(config), omit=()),
        "outputs": dict(outputs),
        "census": dict(census),
        "invariants": {
            "unknown_is_not_zero": "UNKNOWN counted per recipe; never projected, never NO_MATCH, never 0",
            "rank_is_not_eligibility": "rank/score/tier/primary_recipe refused in input and output",
            "recipe_membership_is_not_result_set_membership": "memberships come from recipe evidence only",
            "match_only_projection": "DeedSonar receives MATCH rows only (class ELIGIBLE, rank and score NULL)",
            "stale_match_retired": "the consumer retires this writer's rows a newer run no longer asserts",
        },
        "lead_recipe_os": {
            "role": "CONTRACT",
            "membership_key": "lead_recipe_os.kernel.membership.membership_key (tenant, universe, opportunity, recipe)",
            "hashing": "lead_recipe_os.hashing.content_hash (sorted keys, compact, ensure_ascii=False)",
            "observation_contract": OBSERVATION_CONTRACT,
            "intake_court": "FIXTURE-only on lead-recipe-os aaee1da (unmerged); a LIVE intake court is not on main",
        },
        "deedsonar": {
            "consumer": "public.candidate_recipe_membership_eligibility_apply_v1",
            "store": "public.candidate_recipe_membership",
            "membership_class": DEEDSONAR_MEMBERSHIP_CLASS,
            "lineage_lock": "every bound estate row's ranking_run_id fingerprint == input.sha256[:12]",
        },
    }
    body["content_sha256"] = content_hash(body)
    validate_run_receipt(body)
    return body


def validate_run_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """What the consumer checks before trusting a run."""
    missing = [k for k in _RUN_REQUIRED if k not in receipt]
    if missing:
        raise EligibilityRuntimeError(f"run receipt is missing {missing}")
    if receipt["schema_version"] != RUN_RECEIPT_CONTRACT:
        raise EligibilityRuntimeError(f"run receipt schema_version must be {RUN_RECEIPT_CONTRACT}")
    if content_hash(receipt) != receipt["content_sha256"]:
        raise EligibilityRuntimeError("run receipt content digest mismatch")
    if not _SHA.match(str(receipt["input"].get("sha256", ""))):
        raise EligibilityRuntimeError("run receipt input.sha256 must be sha256 hex")
    if not _COMMIT.match(str(receipt["code"].get("commit", ""))):
        raise EligibilityRuntimeError("run receipt code.commit must be a 40-hex git commit")
    for name, block in receipt["outputs"].items():
        if not _SHA.match(str(block.get("sha256", ""))):
            raise EligibilityRuntimeError(f"run receipt outputs.{name}.sha256 must be sha256 hex")
    if not str(receipt["run_id"]).startswith("eligibility_run_"):
        raise EligibilityRuntimeError("run receipt run_id is not an eligibility run id")
    _refuse_forbidden(dict(receipt), "run_receipt")
    return dict(receipt)


# --------------------------------------------------------------------------
# DeedSonar apply payload
# --------------------------------------------------------------------------

def deedsonar_run_block(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """The run identity the consumer function binds and stamps (p_run)."""
    validate_run_receipt(receipt)
    return {
        "schema_version": RUN_RECEIPT_CONTRACT,
        "run_id": receipt["run_id"],
        "run_receipt_sha256": receipt["content_sha256"],
        "tenant_id": receipt["tenant_id"],
        "universe_id": receipt["universe_id"],
        "input_sha256": receipt["input"]["sha256"],
        "compiled_set_digest": receipt["compiled_set"]["set_digest"],
        "spec_version": receipt["compiled_set"]["spec_version"],
        "pipeline_version": receipt["pipeline"]["version"],
        "code_commit": receipt["code"]["commit"],
        "as_of": receipt["as_of"],
        "valid_until": receipt["valid_until"],
        "census": receipt["census"],
    }


def deedsonar_membership_rows(memberships: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """p_memberships: exactly what the consumer writes, nothing it could misread."""
    rows = []
    seen: set[tuple[str, str]] = set()
    for m in memberships:
        if m["membership_class"] != DEEDSONAR_MEMBERSHIP_CLASS or m["explanation"]["decision"] != "MATCH":
            raise EligibilityRuntimeError("only MATCH memberships are projected")
        if not _VERSION.match(m["recipe_version"]):
            raise EligibilityRuntimeError(f"{m['recipe_version']!r} is not a compiled recipe version")
        key = (m["property_key"], m["recipe_id"])
        if key in seen:
            raise EligibilityRuntimeError(f"membership {key} projected twice")
        seen.add(key)
        rows.append({
            "property_key": m["property_key"],
            "recipe_id": m["recipe_id"],
            "recipe_version": m["recipe_version"],
            "membership_key": m["membership_key"],
            "evaluation_sha256": m["evaluation_sha256"],
            "evaluated_at": m["evaluated_at"],
            # the producer's canonical bytes: what content_sha256 sealed is what DeedSonar stores
            "explanation": consumer_canonical_json(m["explanation"]),
        })
    _refuse_forbidden([{k: v for k, v in r.items() if k != "explanation"} for r in rows], "p_memberships")
    return rows


def validated_compiled_set(compiled_set: Mapping[str, Any]) -> dict[str, Any]:
    """Validate once per run; every property is evaluated against this object."""
    return validate_compiled_set(compiled_set)


__all__ = [
    "DEEDSONAR_APPLY_CONTRACT",
    "DEEDSONAR_MEMBERSHIP_CLASS",
    "EligibilityCensus",
    "EligibilityRuntimeError",
    "FORBIDDEN_PROJECTION_KEYS",
    "MEMBERSHIP_CONTRACT",
    "OBSERVATION_CONTRACT",
    "PIPELINE_NAME",
    "PIPELINE_VERSION",
    "PROPERTY_DECISIONS_CONTRACT",
    "RUN_RECEIPT_CONTRACT",
    "build_run_receipt",
    "compiled_recipe_version",
    "deedsonar_membership_rows",
    "deedsonar_run_block",
    "evaluate_property",
    "membership_explanation",
    "observations",
    "run_id_for",
    "validate_run_receipt",
    "validated_compiled_set",
]
