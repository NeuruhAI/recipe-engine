"""Independent per-candidate / per-recipe eligibility receipts (Astra P1).

The current Recipe Engine ranks a row through ONE ordered recipe chain
(``scripts/rank_nc_curative_raw.py::recipe``) and emits match/rank receipts for
that primary projection.  That path cannot say whether the same property is
ALSO eligible for a second recipe, and it reads a missing indicator as
``False`` (``truthy(None) is False``) -- exactly the UNKNOWN -> NO_MATCH
collapse this module refuses.

This module is the producer Astra ruled missing (P1):

* one normalized evidence context for one candidate / asset;
* an explicit set of eligibility policies (recipe id + version + clauses);
* every policy evaluated INDEPENDENTLY of every other policy;
* exactly one decision per (candidate, recipe version): MATCH / NO_MATCH /
  UNKNOWN;
* clause-level explanation, evidence lineage, freshness boundary, content
  hash, producer identity and an explicit supersession pointer.

Semantics that are contracts, not conventions:

* UNKNOWN is never NO_MATCH.  A missing signal, an UNKNOWN or CONFLICTED
  observation, a confidence below the clause floor, unbound / withdrawn /
  stale evidence: each leaves the clause UNKNOWN.
* NO_MATCH needs bound negative evidence AND a clause that declares
  ``negative_evidence_decisive``.  "The public index found nothing" is
  negative evidence, not the opposite legal fact; the recipe says which it
  means.
* A decisive contradiction outranks an unknown elsewhere in the same recipe:
  a known exclusion is a finding, not a gap.
* Nothing here ranks, scores, mints a candidate, promotes a lead, authorizes
  contact, or writes anywhere.  ``rank`` / ``score`` / ``primary_recipe`` /
  ``signal_tags`` / ``component_contributions`` are refused as inputs.
* ``evaluated_at`` is the caller's ``as_of``.  Same context + same policy
  (+ same ``supersedes``) -> same ``content_sha256``.  Wall clocks prove
  nothing.

Hashing is byte-compatible with the Lead Recipe OS intake court
(``lead_recipe_os.hashing.content_hash`` @ aaee1da): sorted keys, compact
separators, ``ensure_ascii=False``, ``content_sha256`` omitted.  Timestamps
are rendered the way pydantic renders an aware UTC datetime (``...Z``,
microseconds only when non-zero) so the projected intake receipt re-hashes
identically on the consumer side.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from .contracts import (
    CONFIDENCE_LEVELS,
    OpportunityContractError,
    SIGNAL_STATES,
)
from .recipes import land_acquisition_recipe_v1


ELIGIBILITY_POLICY_CONTRACT = "neuruh.recipe-engine.eligibility-policy.v1"
ELIGIBILITY_CONTEXT_CONTRACT = "neuruh.recipe-engine.eligibility-context.v1"
ELIGIBILITY_RECEIPT_CONTRACT = "neuruh.recipe-engine.eligibility-receipt.v1"
INTAKE_RECEIPT_CONTRACT = "neuruh.lead-recipe-os.independent-eligibility-intake.v1"

PRODUCER_OWNER = "recipe-engine"
PRODUCER_IDENTITY = "recipe_engine.opportunity_intelligence.eligibility"
PRODUCER_VERSION = "recipe-engine/eligibility@1.0.0"
EVALUATION_KIND = "INDEPENDENT_ELIGIBILITY"

DECISIONS = ("MATCH", "NO_MATCH", "UNKNOWN")
CLAUSE_OUTCOMES = ("SATISFIED", "CONTRADICTED", "UNKNOWN")
CLAUSE_EXPECTATIONS = ("PRESENT", "ABSENT")
CONTEXT_MODES = ("FIXTURE", "SHADOW", "LIVE", "SYNTHETIC")
LEDGER_STATES = ("BOUND", "NOT_EVALUATED", "UNWIRED", "SOURCE_FAILED")
CONFIDENCE_FLOORS = ("HIGH", "MEDIUM", "LOW")

#: Clause-level reason codes.  Every UNKNOWN outcome states at least one.
REASON_CODES = (
    "SATISFIED_PRESENT",
    "SATISFIED_ABSENT",
    "CONTRADICTED_PRESENT",
    "CONTRADICTED_ABSENT",
    "CONTRADICTION_NOT_DECISIVE",
    "NO_SIGNAL",
    "SIGNAL_UNKNOWN",
    "SIGNAL_CONFLICTED",
    "CONFIDENCE_BELOW_FLOOR",
    "NO_EVIDENCE_LINEAGE",
    "EVIDENCE_UNBOUND",
    "EVIDENCE_WITHDRAWN",
    "EVIDENCE_STALE",
)

#: Ranking / projection / label fields.  None of them is eligibility
#: authority; a context or signal that carries one is refused outright.
FORBIDDEN_AUTHORITY_KEYS = frozenset({
    "primary_recipe",
    "recipe_id",
    "recipe_rank",
    "global_rank",
    "rank",
    "score",
    "curative_priority_score",
    "priority_tier",
    "research_lane",
    "signal_tags",
    "component_contributions",
    "recipe_membership_count",
    "ui_labels",
    "labels",
    "stage",
    "lead_status",
    "contact_authorized",
    "phone",
})

#: Fields a receipt must never carry: a receipt is an eligibility fact, not a
#: candidate, lead, contact or ranking transition.
NEVER_EMITTED_RECEIPT_KEYS = frozenset({
    "primary_recipe", "rank", "recipe_rank", "global_rank", "score",
    "signal_tags", "component_contributions", "stage", "lead_id",
    "lead_status", "crm_id", "contact_authorized", "phone", "outreach",
    "priority_tier",
})

#: The canonical signal vocabulary is the one the owned scoring recipe
#: already declares; eligibility clauses may only name those types.
CANONICAL_SIGNAL_TYPES: tuple[str, ...] = tuple(sorted(
    str(rule["signal_type"]) for rule in land_acquisition_recipe_v1()["signal_rules"]
))

_CONTEXT_KEYS = frozenset({
    "schema_version", "mode", "tenant_id", "universe_id", "opportunity_id",
    "asset_id", "as_of", "evidence", "signals",
})
_EVIDENCE_KEYS = frozenset({
    "evidence_id", "source_id", "source_family", "content_sha256",
    "observed_at", "ledger_state", "ledger_ref", "withdrawn_at", "media_type",
})
_SIGNAL_KEYS = frozenset({
    "signal_id", "signal_type", "state", "confidence", "evidence_refs",
    "source_families", "observed_at", "note",
})
_POLICY_KEYS = frozenset({
    "schema_version", "tenant_id", "recipe_id", "recipe_version_id", "version",
    "validity_seconds", "clauses", "composition", "policy_digest",
    "composition_digest",
})
_CLAUSE_KEYS = frozenset({
    "clause_id", "signal_type", "expect", "negative_evidence_decisive",
    "min_confidence", "max_age_seconds",
})
_CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: Exact field set of the Lead Recipe OS intake schema (additionalProperties
#: false).  The projection emits these and nothing else.
INTAKE_RECEIPT_FIELDS = (
    "schema_version", "mode", "producer_owner", "evaluation_kind",
    "tenant_id", "universe_id", "opportunity_id", "asset_id",
    "recipe_id", "recipe_version_id", "policy_digest", "composition_digest",
    "evidence_digests", "decision", "reason", "evaluated_at", "valid_until",
    "content_sha256",
)


class EligibilityError(OpportunityContractError):
    """The context, policy, receipt or journal cannot be trusted as stated."""


# --------------------------------------------------------------------------
# hashing and time -- consumer-compatible by construction
# --------------------------------------------------------------------------

def consumer_canonical_json(value: Any) -> str:
    """Lead Recipe OS ``hashing.canonical_json`` @ aaee1da, byte for byte."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def content_hash(payload: Mapping[str, Any], *, omit: tuple[str, ...] = ("content_sha256",)) -> str:
    """Lead Recipe OS ``hashing.content_hash`` @ aaee1da, byte for byte."""
    body = {key: value for key, value in payload.items() if key not in omit}
    return hashlib.sha256(consumer_canonical_json(body).encode("utf-8")).hexdigest()


def parse_timestamp(value: Any, path: str) -> datetime:
    """An aware datetime or a refusal.  Naive time cannot bind a freshness window."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise EligibilityError(f"{path} must be an ISO-8601 timestamp")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise EligibilityError(f"{path} is not ISO-8601: {text!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EligibilityError(f"{path} must be timezone aware")
    return parsed.astimezone(timezone.utc)


def render_timestamp(value: datetime) -> str:
    """UTC, ``Z`` suffix, microseconds only when non-zero (pydantic's rendering)."""
    utc = value.astimezone(timezone.utc)
    text = utc.strftime("%Y-%m-%dT%H:%M:%S")
    if utc.microsecond:
        text += f".{utc.microsecond:06d}"
    return text + "Z"


def membership_key(tenant_id: str, universe_id: str, opportunity_id: str, recipe_id: str) -> str:
    """Candidate + recipe within tenant/universe, independent of version.

    Identical to the key ``lead_recipe_os.kernel.membership`` derives, so the
    consumer can join without trusting this producer's arithmetic."""
    return content_hash({
        "tenant_id": tenant_id,
        "universe_id": universe_id,
        "opportunity_id": opportunity_id,
        "recipe_id": recipe_id,
    })


# --------------------------------------------------------------------------
# validation helpers
# --------------------------------------------------------------------------

def _nonempty(value: Any, path: str) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise EligibilityError(f"{path} must be a non-empty string")
    return text


def _sha(value: Any, path: str) -> str:
    text = str(value or "")
    if not _SHA256.match(text):
        raise EligibilityError(f"{path} must be lowercase sha256 hex")
    return text


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EligibilityError(f"{path} must be an object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        raise EligibilityError(f"{path} must be a list")
    return list(value)


def _refuse_authority(mapping: Mapping[str, Any], path: str) -> None:
    present = sorted(FORBIDDEN_AUTHORITY_KEYS & set(map(str, mapping.keys())))
    if present:
        raise EligibilityError(
            f"{path} carries {present}: ranking, projection and label fields are "
            "not eligibility authority"
        )


def _refuse_unknown(mapping: Mapping[str, Any], allowed: frozenset[str], path: str) -> None:
    unknown = sorted(set(map(str, mapping.keys())) - allowed)
    if unknown:
        raise EligibilityError(f"{path} has unrecognized fields {unknown}")


# --------------------------------------------------------------------------
# policy (recipe version) contract
# --------------------------------------------------------------------------

def _policy_body(policy: Mapping[str, Any]) -> dict[str, Any]:
    """What ``policy_digest`` binds: the evaluation semantics, not the digests
    and not the composition (which has its own digest)."""
    return {
        "schema_version": policy["schema_version"],
        "tenant_id": policy["tenant_id"],
        "recipe_id": policy["recipe_id"],
        "recipe_version_id": policy["recipe_version_id"],
        "version": policy["version"],
        "validity_seconds": policy["validity_seconds"],
        "clauses": [dict(clause) for clause in policy["clauses"]],
    }


def policy_digest(policy: Mapping[str, Any]) -> str:
    return content_hash(_policy_body(policy), omit=())


def composition_digest(composition: Mapping[str, Any]) -> str:
    """``content_hash(recipe.composition)`` exactly as the consumer recomputes it."""
    return content_hash(dict(composition), omit=())


def _validate_clause(raw: Any, path: str) -> dict[str, Any]:
    clause = _mapping(raw, path)
    _refuse_unknown(clause, _CLAUSE_KEYS, path)
    clause_id = _nonempty(clause.get("clause_id"), f"{path}.clause_id")
    signal_type = _nonempty(clause.get("signal_type"), f"{path}.signal_type")
    if signal_type not in CANONICAL_SIGNAL_TYPES:
        raise EligibilityError(
            f"{path}.signal_type {signal_type!r} is not in the canonical signal "
            f"vocabulary {list(CANONICAL_SIGNAL_TYPES)}"
        )
    expect = clause.get("expect")
    if expect not in CLAUSE_EXPECTATIONS:
        raise EligibilityError(f"{path}.expect must be PRESENT or ABSENT")
    decisive = clause.get("negative_evidence_decisive")
    if not isinstance(decisive, bool):
        raise EligibilityError(
            f"{path}.negative_evidence_decisive must be an explicit boolean; "
            "a recipe must say whether negative evidence is the opposite fact"
        )
    floor = clause.get("min_confidence", "LOW")
    if floor not in CONFIDENCE_FLOORS:
        raise EligibilityError(f"{path}.min_confidence must be HIGH, MEDIUM or LOW")
    max_age = clause.get("max_age_seconds")
    if max_age is not None:
        if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0:
            raise EligibilityError(f"{path}.max_age_seconds must be a positive integer or null")
    return {
        "clause_id": clause_id,
        "signal_type": signal_type,
        "expect": expect,
        "negative_evidence_decisive": decisive,
        "min_confidence": floor,
        "max_age_seconds": max_age,
    }


def validate_eligibility_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed when a policy cannot be evaluated deterministically, and
    refuse a policy whose digests do not bind its body."""
    policy = _mapping(policy, "policy")
    _refuse_unknown(policy, _POLICY_KEYS, "policy")
    if policy.get("schema_version") != ELIGIBILITY_POLICY_CONTRACT:
        raise EligibilityError(f"policy.schema_version must be {ELIGIBILITY_POLICY_CONTRACT}")
    tenant_id = _nonempty(policy.get("tenant_id"), "policy.tenant_id")
    recipe_id = _nonempty(policy.get("recipe_id"), "policy.recipe_id")
    recipe_version_id = _nonempty(policy.get("recipe_version_id"), "policy.recipe_version_id")
    version = _nonempty(policy.get("version"), "policy.version")
    validity = policy.get("validity_seconds")
    if isinstance(validity, bool) or not isinstance(validity, int) or validity <= 0:
        raise EligibilityError("policy.validity_seconds must be a positive integer")
    raw_clauses = _list(policy.get("clauses"), "policy.clauses")
    if not raw_clauses:
        raise EligibilityError("policy.clauses must declare at least one clause")
    clauses = [_validate_clause(c, f"policy.clauses[{i}]") for i, c in enumerate(raw_clauses)]
    seen: set[str] = set()
    for clause in clauses:
        if clause["clause_id"] in seen:
            raise EligibilityError(f"duplicate clause_id {clause['clause_id']!r}")
        seen.add(clause["clause_id"])
    clauses.sort(key=lambda c: c["clause_id"])

    composition = _mapping(policy.get("composition"), "policy.composition")
    ingredients = _list(composition.get("ingredients"), "policy.composition.ingredients")
    if not ingredients:
        raise EligibilityError("policy.composition.ingredients must be non-empty")
    for index, item in enumerate(ingredients):
        _nonempty(item, f"policy.composition.ingredients[{index}]")
    if composition.get("policy_contract") != ELIGIBILITY_POLICY_CONTRACT:
        raise EligibilityError(
            f"policy.composition.policy_contract must be {ELIGIBILITY_POLICY_CONTRACT}"
        )

    validated = {
        "schema_version": ELIGIBILITY_POLICY_CONTRACT,
        "tenant_id": tenant_id,
        "recipe_id": recipe_id,
        "recipe_version_id": recipe_version_id,
        "version": version,
        "validity_seconds": validity,
        "clauses": clauses,
        "composition": {
            "ingredients": [str(item) for item in ingredients],
            "policy_contract": ELIGIBILITY_POLICY_CONTRACT,
        },
    }
    validated["policy_digest"] = policy_digest(validated)
    validated["composition_digest"] = composition_digest(validated["composition"])
    if policy.get("policy_digest") != validated["policy_digest"]:
        raise EligibilityError("policy.policy_digest does not bind the policy body")
    if policy.get("composition_digest") != validated["composition_digest"]:
        raise EligibilityError("policy.composition_digest does not bind the composition")
    return validated


def build_eligibility_policy(
    *,
    tenant_id: str,
    recipe_id: str,
    recipe_version_id: str,
    version: str,
    clauses: Sequence[Mapping[str, Any]],
    ingredients: Sequence[str],
    validity_seconds: int = 86400,
) -> dict[str, Any]:
    """Stamp a policy's digests from its body, then validate it like a stranger would."""
    draft: dict[str, Any] = {
        "schema_version": ELIGIBILITY_POLICY_CONTRACT,
        "tenant_id": tenant_id,
        "recipe_id": recipe_id,
        "recipe_version_id": recipe_version_id,
        "version": version,
        "validity_seconds": validity_seconds,
        "clauses": [dict(clause) for clause in clauses],
        "composition": {
            "ingredients": [str(item) for item in ingredients],
            "policy_contract": ELIGIBILITY_POLICY_CONTRACT,
        },
    }
    for index, clause in enumerate(draft["clauses"]):
        draft["clauses"][index] = _validate_clause(clause, f"policy.clauses[{index}]")
    draft["clauses"].sort(key=lambda c: c["clause_id"])
    draft["policy_digest"] = policy_digest(draft)
    draft["composition_digest"] = composition_digest(draft["composition"])
    return validate_eligibility_policy(draft)


# --------------------------------------------------------------------------
# evidence context contract
# --------------------------------------------------------------------------

def validate_eligibility_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one candidate's evidence context and return its canonical form.

    Canonical form sorts evidence by ``evidence_id`` and signals by
    ``signal_id`` (and their ref lists) so the context digest -- and therefore
    every receipt -- is independent of the order a caller happened to use.
    """
    context = _mapping(context, "context")
    _refuse_authority(context, "context")
    _refuse_unknown(context, _CONTEXT_KEYS, "context")
    if context.get("schema_version") != ELIGIBILITY_CONTEXT_CONTRACT:
        raise EligibilityError(f"context.schema_version must be {ELIGIBILITY_CONTEXT_CONTRACT}")
    mode = context.get("mode")
    if mode not in CONTEXT_MODES:
        raise EligibilityError(f"context.mode must be one of {list(CONTEXT_MODES)}")
    identity = {
        name: _nonempty(context.get(name), f"context.{name}")
        for name in ("tenant_id", "universe_id", "opportunity_id", "asset_id")
    }
    as_of = parse_timestamp(context.get("as_of"), "context.as_of")

    evidence_by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(_list(context.get("evidence"), "context.evidence")):
        path = f"context.evidence[{index}]"
        item = _mapping(raw, path)
        _refuse_authority(item, path)
        _refuse_unknown(item, _EVIDENCE_KEYS, path)
        evidence_id = _nonempty(item.get("evidence_id"), f"{path}.evidence_id")
        if evidence_id in evidence_by_id:
            raise EligibilityError(f"duplicate evidence_id {evidence_id!r}")
        ledger_state = item.get("ledger_state")
        if ledger_state not in LEDGER_STATES:
            raise EligibilityError(f"{path}.ledger_state must be one of {list(LEDGER_STATES)}")
        ledger_ref = item.get("ledger_ref")
        if ledger_ref is not None:
            ledger_ref = _nonempty(ledger_ref, f"{path}.ledger_ref")
        if ledger_state == "BOUND" and not ledger_ref:
            raise EligibilityError(f"{path}: BOUND evidence requires a ledger_ref")
        observed_at = parse_timestamp(item.get("observed_at"), f"{path}.observed_at")
        if observed_at > as_of:
            raise EligibilityError(f"{path}.observed_at is after context.as_of (future evidence)")
        withdrawn_at = item.get("withdrawn_at")
        withdrawn_text: str | None = None
        if withdrawn_at is not None:
            withdrawn_text = render_timestamp(parse_timestamp(withdrawn_at, f"{path}.withdrawn_at"))
        media_type = item.get("media_type")
        evidence_by_id[evidence_id] = {
            "evidence_id": evidence_id,
            "source_id": _nonempty(item.get("source_id"), f"{path}.source_id"),
            "source_family": _nonempty(item.get("source_family"), f"{path}.source_family"),
            "content_sha256": _sha(item.get("content_sha256"), f"{path}.content_sha256"),
            "observed_at": render_timestamp(observed_at),
            "ledger_state": ledger_state,
            "ledger_ref": ledger_ref,
            "withdrawn_at": withdrawn_text,
            "media_type": None if media_type is None else _nonempty(media_type, f"{path}.media_type"),
        }

    signals_by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(_list(context.get("signals"), "context.signals")):
        path = f"context.signals[{index}]"
        item = _mapping(raw, path)
        _refuse_authority(item, path)
        _refuse_unknown(item, _SIGNAL_KEYS, path)
        signal_id = _nonempty(item.get("signal_id"), f"{path}.signal_id")
        if signal_id in signals_by_id:
            raise EligibilityError(f"duplicate signal_id {signal_id!r}")
        signal_type = _nonempty(item.get("signal_type"), f"{path}.signal_type")
        if item.get("state") not in SIGNAL_STATES:
            raise EligibilityError(f"{path}.state must be one of {list(SIGNAL_STATES)}")
        if item.get("confidence") not in CONFIDENCE_LEVELS:
            raise EligibilityError(f"{path}.confidence must be one of {list(CONFIDENCE_LEVELS)}")
        refs = sorted({_nonempty(ref, f"{path}.evidence_refs item")
                       for ref in _list(item.get("evidence_refs", []), f"{path}.evidence_refs")})
        for ref in refs:
            if ref not in evidence_by_id:
                raise EligibilityError(f"{path}.evidence_refs names unbound evidence {ref!r}")
        families = sorted({_nonempty(fam, f"{path}.source_families item")
                           for fam in _list(item.get("source_families", []), f"{path}.source_families")})
        observed_at = parse_timestamp(item.get("observed_at"), f"{path}.observed_at")
        if observed_at > as_of:
            raise EligibilityError(f"{path}.observed_at is after context.as_of (future signal)")
        note = item.get("note", "")
        signals_by_id[signal_id] = {
            "signal_id": signal_id,
            "signal_type": signal_type,
            "state": item["state"],
            "confidence": item["confidence"],
            "evidence_refs": refs,
            "source_families": families,
            "observed_at": render_timestamp(observed_at),
            "note": "" if note is None else str(note),
        }

    return {
        "schema_version": ELIGIBILITY_CONTEXT_CONTRACT,
        "mode": mode,
        **identity,
        "as_of": render_timestamp(as_of),
        "evidence": [evidence_by_id[k] for k in sorted(evidence_by_id)],
        "signals": [signals_by_id[k] for k in sorted(signals_by_id)],
    }


def context_digest(context: Mapping[str, Any]) -> str:
    """Replay identity of the (canonical) evidence context."""
    return content_hash(dict(validate_eligibility_context(context)), omit=())


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------

def _evidence_support(
    evidence: Mapping[str, Any], *, as_of: datetime, max_age_seconds: int | None
) -> str | None:
    """None when this evidence can support a known finding, else the exclusion code."""
    if evidence["ledger_state"] != "BOUND":
        return "EVIDENCE_UNBOUND"
    withdrawn = evidence.get("withdrawn_at")
    if withdrawn is not None and parse_timestamp(withdrawn, "withdrawn_at") <= as_of:
        return "EVIDENCE_WITHDRAWN"
    if max_age_seconds is not None:
        observed = parse_timestamp(evidence["observed_at"], "observed_at")
        if (as_of - observed).total_seconds() >= max_age_seconds:
            return "EVIDENCE_STALE"
    return None


def _note_exclusion(exclusions: dict[str, str | None], ref: str, code: str | None) -> None:
    """Support (``None``) always wins; otherwise the first exclusion code is kept."""
    if code is None:
        exclusions[ref] = None
    elif ref not in exclusions:
        exclusions[ref] = code


def _evaluate_clause(
    clause: Mapping[str, Any],
    signals: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    *,
    as_of: datetime,
) -> tuple[dict[str, Any], dict[str, str | None]]:
    """Evaluate one clause against every signal of its type.

    Returns the clause record and, for every evidence item this clause looked
    at, ``None`` when it supported a known finding or the exclusion code.
    """
    considered = [s for s in signals if s["signal_type"] == clause["signal_type"]]
    floor = _CONFIDENCE_RANK[clause["min_confidence"]]
    max_age = clause["max_age_seconds"]

    known: dict[str, list[str]] = {"PRESENT": [], "ABSENT": []}
    unknown_codes: set[str] = set()
    exclusions: dict[str, str | None] = {}

    for signal in considered:
        state = signal["state"]
        code: str | None
        if state == "UNKNOWN":
            code = "SIGNAL_UNKNOWN"
        elif state == "CONFLICTED":
            code = "SIGNAL_CONFLICTED"
        elif _CONFIDENCE_RANK[signal["confidence"]] < floor:
            code = "CONFIDENCE_BELOW_FLOOR"
        elif not signal["evidence_refs"]:
            code = "NO_EVIDENCE_LINEAGE"
        else:
            code = None
        if code is not None:
            unknown_codes.add(code)
            for ref in signal["evidence_refs"]:
                _note_exclusion(exclusions, ref, code)
            continue
        supporting: list[str] = []
        for ref in signal["evidence_refs"]:
            ev_code = _evidence_support(evidence_by_id[ref], as_of=as_of, max_age_seconds=max_age)
            _note_exclusion(exclusions, ref, ev_code)
            if ev_code is None:
                supporting.append(ref)
            else:
                unknown_codes.add(ev_code)
        known[state].extend(supporting)

    record: dict[str, Any] = {
        "clause_id": clause["clause_id"],
        "signal_type": clause["signal_type"],
        "expect": clause["expect"],
        "negative_evidence_decisive": clause["negative_evidence_decisive"],
        "min_confidence": clause["min_confidence"],
        "max_age_seconds": max_age,
        "signal_ids": sorted(s["signal_id"] for s in considered),
    }

    if known["PRESENT"] and known["ABSENT"]:
        for ref in known["PRESENT"] + known["ABSENT"]:
            exclusions[ref] = "SIGNAL_CONFLICTED"
        record.update(outcome="UNKNOWN", observed_state="CONFLICTED",
                      reason_codes=["SIGNAL_CONFLICTED"], evidence_ids=[])
        return record, exclusions
    if not known["PRESENT"] and not known["ABSENT"]:
        codes = sorted(unknown_codes) or ["NO_SIGNAL"]
        record.update(outcome="UNKNOWN", observed_state=None, reason_codes=codes, evidence_ids=[])
        return record, exclusions

    observed = "PRESENT" if known["PRESENT"] else "ABSENT"
    evidence_ids = sorted(set(known[observed]))
    if observed == clause["expect"]:
        record.update(outcome="SATISFIED", observed_state=observed,
                      reason_codes=[f"SATISFIED_{observed}"], evidence_ids=evidence_ids)
    else:
        codes = [f"CONTRADICTED_{observed}"]
        if not clause["negative_evidence_decisive"]:
            codes.append("CONTRADICTION_NOT_DECISIVE")
        record.update(outcome="CONTRADICTED", observed_state=observed,
                      reason_codes=codes, evidence_ids=evidence_ids)
    return record, exclusions


def derive_decision(clauses: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """The one place decision semantics live.  Re-run by the receipt validator."""
    decisive = [c for c in clauses
                if c["outcome"] == "CONTRADICTED" and c["negative_evidence_decisive"]]
    if decisive:
        clause = decisive[0]
        return "NO_MATCH", (
            f"NO_MATCH: clause {clause['clause_id']} contradicted by bound evidence "
            f"({clause['signal_type']} observed {clause['observed_state']}, "
            f"expected {clause['expect']}, negative evidence decisive)"
        )
    unresolved = [c for c in clauses if c["outcome"] != "SATISFIED"]
    if unresolved:
        parts = [f"{c['clause_id']}: {'|'.join(c['reason_codes'])}" for c in unresolved]
        if len(unresolved) == 1:
            return "UNKNOWN", f"UNKNOWN: clause {parts[0]} unresolved"
        return "UNKNOWN", f"UNKNOWN: {len(unresolved)} clauses unresolved ({'; '.join(parts)})"
    ids = ", ".join(c["clause_id"] for c in clauses)
    return "MATCH", f"MATCH: all {len(clauses)} clauses satisfied by bound evidence ({ids})"


@dataclass(frozen=True)
class EligibilityReceipt:
    """One candidate, one recipe version, one decision -- and everything needed
    to re-derive it, join it, supersede it, and refuse to over-read it."""

    schema_version: str
    mode: str
    producer_owner: str
    producer_identity: str
    producer_version: str
    evaluation_kind: str
    tenant_id: str
    universe_id: str
    opportunity_id: str
    asset_id: str
    recipe_id: str
    recipe_version_id: str
    policy_digest: str
    composition_digest: str
    membership_key: str
    context_digest: str
    evidence_digests: Mapping[str, str]
    evidence_lineage: tuple[Mapping[str, Any], ...]
    clauses: tuple[Mapping[str, Any], ...]
    decision: str
    reason: str
    evaluated_at: str
    valid_until: str
    supersedes: str | None
    content_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "producer_owner": self.producer_owner,
            "producer_identity": self.producer_identity,
            "producer_version": self.producer_version,
            "evaluation_kind": self.evaluation_kind,
            "tenant_id": self.tenant_id,
            "universe_id": self.universe_id,
            "opportunity_id": self.opportunity_id,
            "asset_id": self.asset_id,
            "recipe_id": self.recipe_id,
            "recipe_version_id": self.recipe_version_id,
            "policy_digest": self.policy_digest,
            "composition_digest": self.composition_digest,
            "membership_key": self.membership_key,
            "context_digest": self.context_digest,
            "evidence_digests": dict(self.evidence_digests),
            "evidence_lineage": [dict(item) for item in self.evidence_lineage],
            "clauses": [dict(item) for item in self.clauses],
            "decision": self.decision,
            "reason": self.reason,
            "evaluated_at": self.evaluated_at,
            "valid_until": self.valid_until,
            "supersedes": self.supersedes,
            "content_sha256": self.content_sha256,
        }

    @staticmethod
    def from_dict(body: Mapping[str, Any]) -> "EligibilityReceipt":
        validated = validate_eligibility_receipt(body)
        return EligibilityReceipt(**{
            **validated,
            "evidence_lineage": tuple(validated["evidence_lineage"]),
            "clauses": tuple(validated["clauses"]),
        })


def evaluate_eligibility(
    context: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    supersedes: str | None = None,
) -> EligibilityReceipt:
    """Evaluate ONE recipe version against ONE candidate's evidence context.

    Pure.  No I/O, no clock, no ranking, no other recipe in scope.
    """
    ctx = validate_eligibility_context(context)
    pol = validate_eligibility_policy(policy)
    if pol["tenant_id"] != ctx["tenant_id"]:
        raise EligibilityError("cross-tenant recipe binding refused")
    if supersedes is not None:
        supersedes = _sha(supersedes, "supersedes")

    as_of = parse_timestamp(ctx["as_of"], "context.as_of")
    evidence_by_id = {e["evidence_id"]: e for e in ctx["evidence"]}

    clause_records: list[dict[str, Any]] = []
    exclusion_by_evidence: dict[str, str | None] = {}
    valid_until = as_of + timedelta(seconds=pol["validity_seconds"])
    for clause in pol["clauses"]:
        record, exclusions = _evaluate_clause(clause, ctx["signals"], evidence_by_id, as_of=as_of)
        clause_records.append(record)
        for ref, code in exclusions.items():
            _note_exclusion(exclusion_by_evidence, ref, code)
        if clause["max_age_seconds"] is not None:
            for ref in record["evidence_ids"]:
                observed = parse_timestamp(evidence_by_id[ref]["observed_at"], "observed_at")
                boundary = observed + timedelta(seconds=clause["max_age_seconds"])
                valid_until = min(valid_until, boundary)
    clause_records.sort(key=lambda c: c["clause_id"])

    decision, reason = derive_decision(clause_records)
    used = sorted({ref for c in clause_records if c["outcome"] != "UNKNOWN" for ref in c["evidence_ids"]})
    evidence_digests = {ref: evidence_by_id[ref]["content_sha256"] for ref in used}
    if decision != "UNKNOWN" and not evidence_digests:
        raise EligibilityError("a known decision requires bound evidence; refusing to emit")
    if valid_until <= as_of:
        raise EligibilityError("empty validity window; supporting evidence is not fresh")

    lineage = []
    for evidence_id in sorted(evidence_by_id):
        item = evidence_by_id[evidence_id]
        if evidence_id in used:
            exclusion = None
        elif evidence_id in exclusion_by_evidence:
            exclusion = exclusion_by_evidence[evidence_id] or "NOT_USED_BY_KNOWN_FINDING"
        else:
            exclusion = "NOT_REFERENCED_BY_RECIPE"
        lineage.append({
            "evidence_id": evidence_id,
            "source_id": item["source_id"],
            "source_family": item["source_family"],
            "content_sha256": item["content_sha256"],
            "ledger_state": item["ledger_state"],
            "observed_at": item["observed_at"],
            "withdrawn_at": item["withdrawn_at"],
            "used": evidence_id in used,
            "exclusion_reason": exclusion,
        })

    body: dict[str, Any] = {
        "schema_version": ELIGIBILITY_RECEIPT_CONTRACT,
        "mode": ctx["mode"],
        "producer_owner": PRODUCER_OWNER,
        "producer_identity": PRODUCER_IDENTITY,
        "producer_version": PRODUCER_VERSION,
        "evaluation_kind": EVALUATION_KIND,
        "tenant_id": ctx["tenant_id"],
        "universe_id": ctx["universe_id"],
        "opportunity_id": ctx["opportunity_id"],
        "asset_id": ctx["asset_id"],
        "recipe_id": pol["recipe_id"],
        "recipe_version_id": pol["recipe_version_id"],
        "policy_digest": pol["policy_digest"],
        "composition_digest": pol["composition_digest"],
        "membership_key": membership_key(
            ctx["tenant_id"], ctx["universe_id"], ctx["opportunity_id"], pol["recipe_id"]
        ),
        "context_digest": content_hash(ctx, omit=()),
        "evidence_digests": evidence_digests,
        "evidence_lineage": lineage,
        "clauses": clause_records,
        "decision": decision,
        "reason": reason,
        "evaluated_at": render_timestamp(as_of),
        "valid_until": render_timestamp(valid_until),
        "supersedes": supersedes,
    }
    body["content_sha256"] = content_hash(body)
    return EligibilityReceipt.from_dict(body)


def evaluate_recipes(
    context: Mapping[str, Any],
    policies: Iterable[Mapping[str, Any]],
    *,
    supersedes_by_membership: Mapping[str, str] | None = None,
) -> tuple[EligibilityReceipt, ...]:
    """Evaluate every requested policy independently; return them in a
    canonical order that does not depend on the order they were requested."""
    ctx = validate_eligibility_context(context)
    receipts: dict[tuple[str, str], EligibilityReceipt] = {}
    for policy in policies:
        pol = validate_eligibility_policy(policy)
        key = (pol["recipe_id"], pol["recipe_version_id"])
        if key in receipts:
            raise EligibilityError(f"recipe version requested twice: {key[0]}@{key[1]}")
        prior = None
        if supersedes_by_membership:
            prior = supersedes_by_membership.get(membership_key(
                ctx["tenant_id"], ctx["universe_id"], ctx["opportunity_id"], pol["recipe_id"]
            ))
        receipts[key] = evaluate_eligibility(ctx, pol, supersedes=prior)
    return tuple(receipts[key] for key in sorted(receipts))


# --------------------------------------------------------------------------
# receipt validation (what a stranger checks before trusting one)
# --------------------------------------------------------------------------

_RECEIPT_KEYS = frozenset({
    "schema_version", "mode", "producer_owner", "producer_identity", "producer_version",
    "evaluation_kind", "tenant_id", "universe_id", "opportunity_id", "asset_id",
    "recipe_id", "recipe_version_id", "policy_digest", "composition_digest",
    "membership_key", "context_digest", "evidence_digests", "evidence_lineage",
    "clauses", "decision", "reason", "evaluated_at", "valid_until", "supersedes",
    "content_sha256",
})


def validate_eligibility_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed on tampering, over-reach, or a decision its clauses do not support."""
    receipt = _mapping(receipt, "receipt")
    leaked = sorted(NEVER_EMITTED_RECEIPT_KEYS & set(map(str, receipt.keys())))
    if leaked:
        raise EligibilityError(f"receipt carries non-eligibility authority fields {leaked}")
    _refuse_unknown(receipt, _RECEIPT_KEYS, "receipt")
    missing = sorted(_RECEIPT_KEYS - set(map(str, receipt.keys())))
    if missing:
        raise EligibilityError(f"receipt is missing fields {missing}")
    if receipt["schema_version"] != ELIGIBILITY_RECEIPT_CONTRACT:
        raise EligibilityError(f"receipt.schema_version must be {ELIGIBILITY_RECEIPT_CONTRACT}")
    if content_hash(receipt) != receipt["content_sha256"]:
        raise EligibilityError("receipt content digest mismatch")
    if receipt["mode"] not in CONTEXT_MODES:
        raise EligibilityError("receipt.mode is not recognized")
    if receipt["producer_owner"] != PRODUCER_OWNER:
        raise EligibilityError("receipt.producer_owner must be recipe-engine")
    if receipt["evaluation_kind"] != EVALUATION_KIND:
        raise EligibilityError(f"receipt.evaluation_kind must be {EVALUATION_KIND}")
    for name in ("producer_identity", "producer_version", "tenant_id", "universe_id",
                 "opportunity_id", "asset_id", "recipe_id", "recipe_version_id", "reason"):
        _nonempty(receipt[name], f"receipt.{name}")
    for name in ("policy_digest", "composition_digest", "membership_key", "context_digest"):
        _sha(receipt[name], f"receipt.{name}")
    if receipt["supersedes"] is not None:
        _sha(receipt["supersedes"], "receipt.supersedes")
    if receipt["decision"] not in DECISIONS:
        raise EligibilityError("receipt.decision must be MATCH, NO_MATCH or UNKNOWN")
    evaluated_at = parse_timestamp(receipt["evaluated_at"], "receipt.evaluated_at")
    valid_until = parse_timestamp(receipt["valid_until"], "receipt.valid_until")
    if valid_until <= evaluated_at:
        raise EligibilityError("receipt has an empty validity window")
    digests = _mapping(receipt["evidence_digests"], "receipt.evidence_digests")
    for evidence_id, sha in digests.items():
        _nonempty(evidence_id, "receipt.evidence_digests key")
        _sha(sha, f"receipt.evidence_digests[{evidence_id}]")
    if receipt["decision"] != "UNKNOWN" and not digests:
        raise EligibilityError("a known decision requires evidence, including negative evidence")
    clauses = [dict(_mapping(c, f"receipt.clauses[{i}]"))
               for i, c in enumerate(_list(receipt["clauses"], "receipt.clauses"))]
    if not clauses:
        raise EligibilityError("receipt.clauses must not be empty")
    for index, clause in enumerate(clauses):
        if clause.get("outcome") not in CLAUSE_OUTCOMES:
            raise EligibilityError(f"receipt.clauses[{index}].outcome is not recognized")
        if not isinstance(clause.get("negative_evidence_decisive"), bool):
            raise EligibilityError(f"receipt.clauses[{index}].negative_evidence_decisive must be boolean")
        codes = _list(clause.get("reason_codes"), f"receipt.clauses[{index}].reason_codes")
        if not codes or any(code not in REASON_CODES for code in codes):
            raise EligibilityError(f"receipt.clauses[{index}].reason_codes must be stated and recognized")
    expected_decision, expected_reason = derive_decision(sorted(clauses, key=lambda c: c["clause_id"]))
    if receipt["decision"] != expected_decision or receipt["reason"] != expected_reason:
        raise EligibilityError("receipt.decision/reason are not what its clauses derive")
    if receipt["membership_key"] != membership_key(
        receipt["tenant_id"], receipt["universe_id"], receipt["opportunity_id"], receipt["recipe_id"]
    ):
        raise EligibilityError("receipt.membership_key does not bind tenant/universe/candidate/recipe")
    lineage = [dict(_mapping(item, f"receipt.evidence_lineage[{i}]"))
               for i, item in enumerate(_list(receipt["evidence_lineage"], "receipt.evidence_lineage"))]
    return {**{k: receipt[k] for k in _RECEIPT_KEYS},
            "evidence_digests": dict(digests), "evidence_lineage": lineage, "clauses": clauses}


# --------------------------------------------------------------------------
# projection onto the Lead Recipe OS intake contract
# --------------------------------------------------------------------------

def to_intake_receipt(receipt: EligibilityReceipt | Mapping[str, Any]) -> dict[str, Any]:
    """Project onto ``neuruh.lead-recipe-os.independent-eligibility-intake.v1``.

    Exactly the consumer's field set (its schema forbids extras), re-hashed
    with the consumer's algorithm.  The intake court accepts FIXTURE only;
    projecting any other mode is refused rather than relabelled.
    """
    body = receipt.to_dict() if isinstance(receipt, EligibilityReceipt) else validate_eligibility_receipt(receipt)
    if body["mode"] != "FIXTURE":
        raise EligibilityError("lead-recipe-os intake accepts FIXTURE mode only; refusing to relabel")
    projected = {
        "schema_version": INTAKE_RECEIPT_CONTRACT,
        "mode": "FIXTURE",
        "producer_owner": PRODUCER_OWNER,
        "evaluation_kind": EVALUATION_KIND,
        "tenant_id": body["tenant_id"],
        "universe_id": body["universe_id"],
        "opportunity_id": body["opportunity_id"],
        "asset_id": body["asset_id"],
        "recipe_id": body["recipe_id"],
        "recipe_version_id": body["recipe_version_id"],
        "policy_digest": body["policy_digest"],
        "composition_digest": body["composition_digest"],
        "evidence_digests": dict(body["evidence_digests"]),
        "decision": body["decision"],
        "reason": body["reason"],
        "evaluated_at": body["evaluated_at"],
        "valid_until": body["valid_until"],
    }
    projected["content_sha256"] = content_hash(projected)
    assert tuple(projected) == INTAKE_RECEIPT_FIELDS
    return projected


# --------------------------------------------------------------------------
# append-only, content-addressed receipt journal (process-local, no I/O)
# --------------------------------------------------------------------------

class EligibilityReceiptJournal:
    """History that cannot be rewritten, and one live truth per membership.

    * ``append`` is idempotent: the same receipt again changes nothing.
    * a second live receipt for a membership must name what it supersedes;
      the superseded receipt stays, byte-identical, in ``history``.
    * nothing is written anywhere.  Storage binding is a separate seam.
    """

    def __init__(self) -> None:
        self._by_sha: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._by_key: dict[str, list[str]] = {}
        self._superseded_by: dict[str, str] = {}

    def __len__(self) -> int:
        return len(self._order)

    def append(self, receipt: EligibilityReceipt | Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        body = receipt.to_dict() if isinstance(receipt, EligibilityReceipt) else dict(receipt)
        body = validate_eligibility_receipt(body)
        sha = body["content_sha256"]
        if sha in self._by_sha:
            return dict(self._by_sha[sha]), False
        key = body["membership_key"]
        prior = body["supersedes"]
        if prior is not None:
            target = self._by_sha.get(prior)
            if target is None:
                raise EligibilityError(f"supersedes unknown receipt {prior}")
            if target["membership_key"] != key:
                raise EligibilityError("a receipt may only supersede its own membership")
            if parse_timestamp(body["evaluated_at"], "evaluated_at") < parse_timestamp(target["evaluated_at"], "evaluated_at"):
                raise EligibilityError("a receipt cannot supersede a later evaluation")
            already = self._superseded_by.get(prior)
            if already is not None and already != sha:
                raise EligibilityError(f"receipt {prior} is already superseded by {already}")
        else:
            live = [s for s in self._by_key.get(key, []) if s not in self._superseded_by]
            if live:
                raise EligibilityError(
                    f"membership already has live receipt {live[0]}; a new evaluation must supersede it"
                )
        self._by_sha[sha] = body
        self._order.append(sha)
        self._by_key.setdefault(key, []).append(sha)
        if prior is not None:
            self._superseded_by[prior] = sha
        return dict(body), True

    def get(self, sha: str) -> dict[str, Any] | None:
        body = self._by_sha.get(sha)
        return None if body is None else dict(body)

    def superseded_by(self, sha: str) -> str | None:
        return self._superseded_by.get(sha)

    def history(self, key: str) -> tuple[dict[str, Any], ...]:
        return tuple(dict(self._by_sha[s]) for s in self._by_key.get(key, []))

    def current(self, key: str) -> dict[str, Any] | None:
        live = [s for s in self._by_key.get(key, []) if s not in self._superseded_by]
        if len(live) > 1:  # unreachable by construction; stated so it is checkable
            raise EligibilityError("journal integrity: more than one live receipt")
        return dict(self._by_sha[live[0]]) if live else None

    def current_by_candidate(self, tenant_id: str, universe_id: str, opportunity_id: str) -> tuple[dict[str, Any], ...]:
        out = []
        for key in sorted(self._by_key):
            body = self.current(key)
            if body and (body["tenant_id"], body["universe_id"], body["opportunity_id"]) == (
                tenant_id, universe_id, opportunity_id
            ):
                out.append(body)
        return tuple(out)

    def verify(self) -> bool:
        for sha in self._order:
            body = self._by_sha[sha]
            if validate_eligibility_receipt(body)["content_sha256"] != sha:
                raise EligibilityError(f"journal integrity: {sha} does not hash to itself")
            if body["supersedes"] is not None and self._superseded_by.get(body["supersedes"]) != sha:
                raise EligibilityError("journal integrity: supersession link missing")
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipts": [dict(self._by_sha[s]) for s in self._order],
            "supersessions": [
                {"superseded": old, "superseding": new}
                for old, new in sorted(self._superseded_by.items())
            ],
        }


__all__ = [
    "CANONICAL_SIGNAL_TYPES",
    "CLAUSE_OUTCOMES",
    "CONTEXT_MODES",
    "DECISIONS",
    "ELIGIBILITY_CONTEXT_CONTRACT",
    "ELIGIBILITY_POLICY_CONTRACT",
    "ELIGIBILITY_RECEIPT_CONTRACT",
    "EVALUATION_KIND",
    "EligibilityError",
    "EligibilityReceipt",
    "EligibilityReceiptJournal",
    "FORBIDDEN_AUTHORITY_KEYS",
    "INTAKE_RECEIPT_CONTRACT",
    "INTAKE_RECEIPT_FIELDS",
    "LEDGER_STATES",
    "NEVER_EMITTED_RECEIPT_KEYS",
    "PRODUCER_IDENTITY",
    "PRODUCER_OWNER",
    "PRODUCER_VERSION",
    "REASON_CODES",
    "build_eligibility_policy",
    "composition_digest",
    "consumer_canonical_json",
    "content_hash",
    "context_digest",
    "derive_decision",
    "evaluate_eligibility",
    "evaluate_recipes",
    "membership_key",
    "parse_timestamp",
    "policy_digest",
    "render_timestamp",
    "to_intake_receipt",
    "validate_eligibility_context",
    "validate_eligibility_policy",
    "validate_eligibility_receipt",
]
