"""Astra P1 court: independent per-candidate / per-recipe eligibility receipts.

ONE property, FOUR recipe versions, evaluated independently:

    A published_tax_distress                 -> MATCH
    B stalled_tax_foreclosure_heirship       -> NO_MATCH  (decisive negative docket evidence)
    C open_probate_property                  -> UNKNOWN   (inconclusive probate index)
    D long_term_absentee_potential_equity    -> MATCH     (second MATCH on the same asset)

Everything here is synthetic.  Nothing here is a live finding, a candidate
mint, a lead, or contact authority.
"""

from __future__ import annotations

import builtins
import hashlib
import inspect
import itertools
import json
import socket
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from recipe_engine.closed_loop.identity import digest as identity_digest
from recipe_engine.opportunity_intelligence import eligibility as elig
from recipe_engine.opportunity_intelligence.eligibility import (
    CANONICAL_SIGNAL_TYPES,
    ELIGIBILITY_RECEIPT_CONTRACT,
    INTAKE_RECEIPT_CONTRACT,
    INTAKE_RECEIPT_FIELDS,
    NEVER_EMITTED_RECEIPT_KEYS,
    EligibilityError,
    EligibilityReceipt,
    EligibilityReceiptJournal,
    build_eligibility_policy,
    content_hash,
    evaluate_eligibility,
    evaluate_recipes,
    membership_key,
    to_intake_receipt,
    validate_eligibility_receipt,
)

FIXTURE = Path(__file__).parent / "fixtures" / "p1_independent_eligibility.json"
A, B, C, D = (
    "published_tax_distress",
    "stalled_tax_foreclosure_heirship",
    "open_probate_property",
    "long_term_absentee_potential_equity",
)

#: The receipt Astra sealed in lead-recipe-os @aaee1da
#: (tests/fixtures/convergence/independent_eligibility.json).  Reproducing its
#: content_sha256 from this repository proves the hash algorithms agree.
ASTRA_FIXTURE_RECEIPT = {
    "schema_version": "neuruh.lead-recipe-os.independent-eligibility-intake.v1",
    "mode": "FIXTURE",
    "producer_owner": "recipe-engine",
    "evaluation_kind": "INDEPENDENT_ELIGIBILITY",
    "tenant_id": "fixture-tenant",
    "universe_id": "fixture-universe",
    "opportunity_id": "candidate-1",
    "asset_id": "property-1",
    "recipe_id": "fixture-independent-policy",
    "recipe_version_id": "version-1",
    "policy_digest": "1" * 64,
    "composition_digest": "b4da23face00a896f728c6239428b937c541e9e77c152420c58ecf4f9fea4a4a",
    "evidence_digests": {"evidence-1": "2" * 64},
    "decision": "MATCH",
    "reason": "Synthetic receipt only; no production eligibility claimed",
    "evaluated_at": "2026-09-08T01:00:00Z",
    "valid_until": "2026-09-09T01:00:00Z",
    "content_sha256": "35f723d15bc6a392e317895b2653eed152e0cbc68187f338fc82d6fbe0365bf2",
}
ASTRA_FIXTURE_COMPOSITION = {
    "ingredients": ["fixture-tax-evidence"],
    "policy_contract": "neuruh.opportunity-intelligence.recipe.v1",
}


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _context() -> dict:
    return deepcopy(_fixture()["context"])


def _policies() -> dict[str, dict]:
    body = _fixture()
    return {body["recipe_labels"][label]: deepcopy(policy) for label, policy in body["policies"].items()}


def _by_recipe(receipts) -> dict[str, EligibilityReceipt]:
    return {r.recipe_id: r for r in receipts}


def _evidence(context: dict, evidence_id: str) -> dict:
    return next(e for e in context["evidence"] if e["evidence_id"] == evidence_id)


def _signal(context: dict, signal_id: str) -> dict:
    return next(s for s in context["signals"] if s["signal_id"] == signal_id)


def _clause(receipt: EligibilityReceipt, clause_id: str) -> dict:
    return next(c for c in receipt.clauses if c["clause_id"] == clause_id)


def _lro_content_hash(payload: dict, omit=("content_sha256",)) -> str:
    """Independent replica of lead_recipe_os.hashing.content_hash @aaee1da."""
    body = {k: v for k, v in payload.items() if k not in omit}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# the mandatory fixture: A MATCH, B NO_MATCH, C UNKNOWN, D MATCH on ONE asset
# --------------------------------------------------------------------------

def test_fixture_decisions_and_sealed_hashes():
    body = _fixture()
    receipts = _by_recipe(evaluate_recipes(body["context"], body["policies"].values()))
    assert {r.decision for r in receipts.values()} == {"MATCH", "NO_MATCH", "UNKNOWN"}
    assert receipts[A].decision == "MATCH"
    assert receipts[B].decision == "NO_MATCH"
    assert receipts[C].decision == "UNKNOWN"
    assert receipts[D].decision == "MATCH"
    for recipe_id, expected in body["expected"].items():
        receipt = receipts[recipe_id]
        assert receipt.decision == expected["decision"]
        assert receipt.content_sha256 == expected["content_sha256"]
        assert to_intake_receipt(receipt)["content_sha256"] == expected["intake_content_sha256"]
        assert receipt.membership_key == expected["membership_key"]
        assert receipt.policy_digest == expected["policy_digest"]
        assert receipt.composition_digest == expected["composition_digest"]
        assert receipt.context_digest == expected["context_digest"]
        assert receipt.valid_until == expected["valid_until"]
        assert sorted(receipt.evidence_digests) == expected["evidence_ids_used"]
    # one property, one candidate: identity is identical across all four receipts
    assert len({(r.tenant_id, r.universe_id, r.opportunity_id, r.asset_id) for r in receipts.values()}) == 1
    assert len({r.context_digest for r in receipts.values()}) == 1


def test_match_receipt_states_clause_evidence():
    receipt = _by_recipe(evaluate_recipes(_context(), _policies().values()))[A]
    assert receipt.decision == "MATCH"
    assert receipt.schema_version == ELIGIBILITY_RECEIPT_CONTRACT
    assert all(c["outcome"] == "SATISFIED" for c in receipt.clauses)
    assert _clause(receipt, "tax_present")["evidence_ids"] == ["ev-tax-roll-2026"]
    assert _clause(receipt, "not_government_owned")["observed_state"] == "ABSENT"
    assert set(receipt.evidence_digests) == {"ev-tax-roll-2026", "ev-parcel-roll-2026"}
    used = {l["evidence_id"]: l for l in receipt.evidence_lineage}
    assert used["ev-tax-roll-2026"]["used"] is True
    assert used["ev-docket-search-2026"]["exclusion_reason"] == "NOT_REFERENCED_BY_RECIPE"


def test_no_match_requires_decisive_negative_evidence_and_outranks_unknown():
    receipt = _by_recipe(evaluate_recipes(_context(), _policies().values()))[B]
    assert receipt.decision == "NO_MATCH"
    foreclosure = _clause(receipt, "foreclosure_present")
    assert foreclosure["outcome"] == "CONTRADICTED"
    assert foreclosure["negative_evidence_decisive"] is True
    assert foreclosure["reason_codes"] == ["CONTRADICTED_ABSENT"]
    assert foreclosure["evidence_ids"] == ["ev-docket-search-2026"]
    # the estate clause is UNKNOWN in the same recipe; a known exclusion is a finding, not a gap
    assert _clause(receipt, "estate_present")["outcome"] == "UNKNOWN"
    assert "ev-docket-search-2026" in receipt.evidence_digests
    assert "negative evidence decisive" in receipt.reason


def test_unknown_receipt_names_the_unresolved_clause():
    receipt = _by_recipe(evaluate_recipes(_context(), _policies().values()))[C]
    assert receipt.decision == "UNKNOWN"
    estate = _clause(receipt, "estate_present")
    assert estate["outcome"] == "UNKNOWN"
    assert estate["reason_codes"] == ["SIGNAL_UNKNOWN"]
    assert estate["evidence_ids"] == []
    probate = next(l for l in receipt.evidence_lineage if l["evidence_id"] == "ev-probate-index-2026")
    assert probate["used"] is False and probate["exclusion_reason"] == "SIGNAL_UNKNOWN"


def test_two_independent_match_receipts_coexist_for_one_property():
    receipts = _by_recipe(evaluate_recipes(_context(), _policies().values()))
    assert receipts[A].decision == receipts[D].decision == "MATCH"
    assert receipts[A].membership_key != receipts[D].membership_key
    assert (receipts[A].opportunity_id, receipts[A].asset_id) == (receipts[D].opportunity_id, receipts[D].asset_id)
    journal = EligibilityReceiptJournal()
    for receipt in receipts.values():
        assert journal.append(receipt)[1] is True
    current = journal.current_by_candidate(
        receipts[A].tenant_id, receipts[A].universe_id, receipts[A].opportunity_id
    )
    assert sorted(r["decision"] for r in current) == ["MATCH", "MATCH", "NO_MATCH", "UNKNOWN"]
    assert journal.verify() is True


# --------------------------------------------------------------------------
# order independence
# --------------------------------------------------------------------------

def test_recipe_evaluation_order_does_not_change_any_receipt():
    context, policies = _context(), list(_policies().values())
    baseline = {r.recipe_id: r.content_sha256 for r in evaluate_recipes(context, policies)}
    for order in itertools.permutations(policies):
        receipts = evaluate_recipes(context, order)
        assert {r.recipe_id: r.content_sha256 for r in receipts} == baseline
        assert [r.recipe_id for r in receipts] == sorted(baseline)


def test_evidence_and_signal_order_do_not_change_any_receipt():
    context, policies = _context(), _policies()
    baseline = {r.recipe_id: r.content_sha256 for r in evaluate_recipes(context, policies.values())}
    shuffled = deepcopy(context)
    shuffled["evidence"].reverse()
    shuffled["signals"].reverse()
    for signal in shuffled["signals"]:
        signal["evidence_refs"].reverse()
    assert {r.recipe_id: r.content_sha256 for r in evaluate_recipes(shuffled, policies.values())} == baseline


def test_each_recipe_is_evaluated_alone():
    context, policies = _context(), _policies()
    together = {r.recipe_id: r.content_sha256 for r in evaluate_recipes(context, policies.values())}
    alone = {rid: evaluate_eligibility(context, policy).content_sha256 for rid, policy in policies.items()}
    assert together == alone


# --------------------------------------------------------------------------
# idempotent replay
# --------------------------------------------------------------------------

def test_same_inputs_reproduce_the_same_receipt_hashes():
    first = evaluate_recipes(_context(), _policies().values())
    second = evaluate_recipes(_context(), _policies().values())
    assert [r.to_dict() for r in first] == [r.to_dict() for r in second]
    assert [r.content_sha256 for r in first] == [r.content_sha256 for r in second]


def test_journal_replay_appends_nothing():
    journal = EligibilityReceiptJournal()
    receipts = evaluate_recipes(_context(), _policies().values())
    assert [journal.append(r)[1] for r in receipts] == [True] * 4
    assert [journal.append(r)[1] for r in receipts] == [False] * 4
    assert [journal.append(r.to_dict())[1] for r in receipts] == [False] * 4
    assert len(journal) == 4
    assert journal.verify() is True


def test_timestamp_spelling_does_not_change_the_hash():
    context = _context()
    baseline = evaluate_eligibility(context, _policies()[A]).content_sha256
    context["as_of"] = "2026-09-08T01:00:00+00:00"
    assert evaluate_eligibility(context, _policies()[A]).content_sha256 == baseline
    context["as_of"] = "2026-09-08T03:00:00+02:00"
    assert evaluate_eligibility(context, _policies()[A]).content_sha256 == baseline
    context["as_of"] = "2026-09-08T01:00:00"
    with pytest.raises(EligibilityError, match="timezone aware"):
        evaluate_eligibility(context, _policies()[A])


# --------------------------------------------------------------------------
# version binding
# --------------------------------------------------------------------------

def test_new_recipe_version_changes_receipt_identity_but_not_candidate_identity():
    context, policies = _context(), _policies()
    v1 = evaluate_eligibility(context, policies[A])
    v2_policy = build_eligibility_policy(
        tenant_id=policies[A]["tenant_id"], recipe_id=A,
        recipe_version_id="published_tax_distress@elig.2", version="2",
        clauses=policies[A]["clauses"], ingredients=policies[A]["composition"]["ingredients"],
    )
    v2 = evaluate_eligibility(context, v2_policy, supersedes=v1.content_sha256)
    assert v2.decision == v1.decision == "MATCH"
    assert v2.recipe_version_id != v1.recipe_version_id
    assert v2.policy_digest != v1.policy_digest
    assert v2.content_sha256 != v1.content_sha256
    assert v2.membership_key == v1.membership_key
    assert (v2.opportunity_id, v2.asset_id, v2.context_digest) == (v1.opportunity_id, v1.asset_id, v1.context_digest)
    assert v2.supersedes == v1.content_sha256


def test_policy_digest_binds_the_policy_body():
    policy = _policies()[A]
    tampered = deepcopy(policy)
    tampered["clauses"][0]["negative_evidence_decisive"] = not tampered["clauses"][0]["negative_evidence_decisive"]
    with pytest.raises(EligibilityError, match="policy_digest does not bind"):
        evaluate_eligibility(_context(), tampered)
    tampered = deepcopy(policy)
    tampered["composition"]["ingredients"].append("something-else")
    with pytest.raises(EligibilityError, match="composition_digest does not bind"):
        evaluate_eligibility(_context(), tampered)


def test_cross_tenant_policy_is_refused():
    policy = deepcopy(_policies()[A])
    policy["tenant_id"] = "other-tenant"
    policy["policy_digest"] = elig.policy_digest(policy)
    with pytest.raises(EligibilityError, match="cross-tenant"):
        evaluate_eligibility(_context(), policy)


# --------------------------------------------------------------------------
# UNKNOWN preservation: withdrawal, staleness, custody, gaps, conflict
# --------------------------------------------------------------------------

def test_source_withdrawal_turns_match_into_unknown_never_no_match():
    context = _context()
    _evidence(context, "ev-tax-roll-2026")["withdrawn_at"] = "2026-09-07T20:00:00Z"
    receipt = evaluate_eligibility(context, _policies()[A])
    assert receipt.decision == "UNKNOWN"
    assert receipt.decision != "NO_MATCH"
    assert _clause(receipt, "tax_present")["reason_codes"] == ["EVIDENCE_WITHDRAWN"]
    lineage = next(l for l in receipt.evidence_lineage if l["evidence_id"] == "ev-tax-roll-2026")
    assert lineage["used"] is False and lineage["exclusion_reason"] == "EVIDENCE_WITHDRAWN"
    assert lineage["withdrawn_at"] == "2026-09-07T20:00:00Z"
    assert "ev-tax-roll-2026" not in receipt.evidence_digests


def test_withdrawal_after_as_of_is_not_yet_a_withdrawal():
    context = _context()
    _evidence(context, "ev-tax-roll-2026")["withdrawn_at"] = "2026-09-08T09:00:00Z"
    assert evaluate_eligibility(context, _policies()[A]).decision == "MATCH"


def test_stale_evidence_is_unknown_and_bounds_valid_until():
    context = _context()
    stale = "2026-07-01T06:00:00Z"  # 69 days before as_of; absentee clause allows 30
    _evidence(context, "ev-parcel-roll-2026")["observed_at"] = stale
    for signal_id in ("sig-absentee-owner", "sig-long-hold", "sig-government-owner"):
        _signal(context, signal_id)["observed_at"] = stale
    receipt = evaluate_eligibility(context, _policies()[D])
    assert receipt.decision == "UNKNOWN"
    assert _clause(receipt, "absentee_present")["reason_codes"] == ["EVIDENCE_STALE"]
    assert _clause(receipt, "long_hold_present")["outcome"] == "SATISFIED"  # no freshness clause
    fresh = evaluate_eligibility(_context(), _policies()[D])
    assert fresh.decision == "MATCH"
    assert fresh.valid_until == "2026-09-09T01:00:00Z"  # policy validity, tighter than 30d boundary


def test_freshness_boundary_can_tighten_valid_until():
    context, policy = _context(), deepcopy(_policies()[D])
    for clause in policy["clauses"]:
        if clause["clause_id"] == "absentee_present":
            clause["max_age_seconds"] = 8 * 86400  # observed 2026-09-01T06:00Z -> boundary 09-09T06:00Z
    policy = build_eligibility_policy(
        tenant_id=policy["tenant_id"], recipe_id=policy["recipe_id"],
        recipe_version_id=policy["recipe_version_id"], version=policy["version"],
        clauses=policy["clauses"], ingredients=policy["composition"]["ingredients"],
        validity_seconds=7 * 86400,
    )
    receipt = evaluate_eligibility(context, policy)
    assert receipt.decision == "MATCH"
    assert receipt.valid_until == "2026-09-09T06:00:00Z"


@pytest.mark.parametrize("ledger_state", ["UNWIRED", "SOURCE_FAILED", "NOT_EVALUATED"])
def test_unbound_evidence_is_unknown(ledger_state):
    context = _context()
    item = _evidence(context, "ev-tax-roll-2026")
    item["ledger_state"] = ledger_state
    item["ledger_ref"] = None
    receipt = evaluate_eligibility(context, _policies()[A])
    assert receipt.decision == "UNKNOWN"
    assert _clause(receipt, "tax_present")["reason_codes"] == ["EVIDENCE_UNBOUND"]


def test_bound_evidence_requires_a_ledger_ref():
    context = _context()
    _evidence(context, "ev-tax-roll-2026")["ledger_ref"] = None
    with pytest.raises(EligibilityError, match="ledger_ref"):
        evaluate_eligibility(context, _policies()[A])


def test_missing_required_signal_is_unknown_not_no_match():
    context = _context()
    context["signals"] = [s for s in context["signals"] if s["signal_type"] != "probate_or_estate"]
    receipt = evaluate_eligibility(context, _policies()[C])
    assert receipt.decision == "UNKNOWN"
    assert _clause(receipt, "estate_present")["reason_codes"] == ["NO_SIGNAL"]
    assert _clause(receipt, "estate_present")["signal_ids"] == []


def test_confidence_below_clause_floor_is_unknown():
    context = _context()
    _signal(context, "sig-tax-delinquent")["confidence"] = "LOW"  # clause floor is MEDIUM
    receipt = evaluate_eligibility(context, _policies()[A])
    assert receipt.decision == "UNKNOWN"
    assert _clause(receipt, "tax_present")["reason_codes"] == ["CONFIDENCE_BELOW_FLOOR"]


def test_signal_without_evidence_lineage_is_unknown():
    context = _context()
    _signal(context, "sig-tax-delinquent")["evidence_refs"] = []
    receipt = evaluate_eligibility(context, _policies()[A])
    assert receipt.decision == "UNKNOWN"
    assert _clause(receipt, "tax_present")["reason_codes"] == ["NO_EVIDENCE_LINEAGE"]


def test_conflicting_known_observations_are_unknown():
    context = _context()
    context["evidence"].append({
        "evidence_id": "ev-tax-cure-2026", "source_id": "wake-county-tax-collector-payoff",
        "source_family": "COUNTY_TAX_COLLECTOR", "content_sha256": "c" * 64,
        "observed_at": "2026-09-07T13:00:00Z", "ledger_state": "BOUND",
        "ledger_ref": "fixture-ledger:ev-tax-cure-2026", "withdrawn_at": None, "media_type": "application/json",
    })
    context["signals"].append({
        "signal_id": "sig-tax-cured", "signal_type": "tax_delinquent", "state": "ABSENT",
        "confidence": "HIGH", "evidence_refs": ["ev-tax-cure-2026"], "source_families": ["COUNTY_TAX_COLLECTOR"],
        "observed_at": "2026-09-07T13:00:00Z", "note": "Synthetic: payoff recorded.",
    })
    receipt = evaluate_eligibility(context, _policies()[A])
    assert receipt.decision == "UNKNOWN"
    clause = _clause(receipt, "tax_present")
    assert clause["observed_state"] == "CONFLICTED"
    assert clause["reason_codes"] == ["SIGNAL_CONFLICTED"]


def test_conflicted_signal_state_is_unknown():
    context = _context()
    _signal(context, "sig-tax-delinquent")["state"] = "CONFLICTED"
    receipt = evaluate_eligibility(context, _policies()[A])
    assert receipt.decision == "UNKNOWN"
    assert _clause(receipt, "tax_present")["reason_codes"] == ["SIGNAL_CONFLICTED"]


def test_negative_evidence_is_no_match_only_when_the_recipe_says_so():
    policy = deepcopy(_policies()[B])
    for clause in policy["clauses"]:
        if clause["clause_id"] == "foreclosure_present":
            clause["negative_evidence_decisive"] = False
    policy = build_eligibility_policy(
        tenant_id=policy["tenant_id"], recipe_id=policy["recipe_id"],
        recipe_version_id="stalled_tax_foreclosure_heirship@elig.nondecisive", version="1",
        clauses=policy["clauses"], ingredients=policy["composition"]["ingredients"],
    )
    receipt = evaluate_eligibility(_context(), policy)
    assert receipt.decision == "UNKNOWN"
    clause = _clause(receipt, "foreclosure_present")
    assert clause["outcome"] == "CONTRADICTED"
    assert clause["reason_codes"] == ["CONTRADICTED_ABSENT", "CONTRADICTION_NOT_DECISIVE"]


def test_decisive_exclusion_present_is_no_match():
    context = _context()
    _signal(context, "sig-government-owner")["state"] = "PRESENT"
    receipt = evaluate_eligibility(context, _policies()[A])
    assert receipt.decision == "NO_MATCH"
    assert _clause(receipt, "not_government_owned")["reason_codes"] == ["CONTRADICTED_PRESENT"]


def test_a_clause_must_declare_whether_negative_evidence_is_decisive():
    with pytest.raises(EligibilityError, match="negative_evidence_decisive"):
        build_eligibility_policy(
            tenant_id="fixture-tenant", recipe_id="x", recipe_version_id="x@1", version="1",
            clauses=[{"clause_id": "c", "signal_type": "tax_delinquent", "expect": "PRESENT"}],
            ingredients=["COUNTY_TAX_ROLL"],
        )


def test_clauses_are_bound_to_the_canonical_signal_vocabulary():
    assert "tax_delinquent" in CANONICAL_SIGNAL_TYPES
    with pytest.raises(EligibilityError, match="canonical signal vocabulary"):
        build_eligibility_policy(
            tenant_id="fixture-tenant", recipe_id="x", recipe_version_id="x@1", version="1",
            clauses=[{"clause_id": "c", "signal_type": "tax_sale_stage", "expect": "PRESENT",
                      "negative_evidence_decisive": True}],
            ingredients=["TAX_SALE"],
        )


def test_future_evidence_is_refused_not_scored():
    context = _context()
    _evidence(context, "ev-tax-roll-2026")["observed_at"] = "2026-09-08T02:00:00Z"
    with pytest.raises(EligibilityError, match="future evidence"):
        evaluate_eligibility(context, _policies()[A])


# --------------------------------------------------------------------------
# no rank / primary / label authority; no candidate, lead or contact transition
# --------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "primary_recipe", "rank", "recipe_rank", "global_rank", "score", "curative_priority_score",
    "priority_tier", "signal_tags", "component_contributions", "recipe_membership_count",
    "research_lane", "stage", "lead_status", "contact_authorized", "phone", "recipe_id",
])
def test_ranking_projection_and_label_fields_are_not_authority(key):
    context = _context()
    context[key] = "not-evidence"
    with pytest.raises(EligibilityError, match="not eligibility authority"):
        evaluate_eligibility(context, _policies()[A])
    context = _context()
    context["signals"][0][key] = "not-evidence"
    with pytest.raises(EligibilityError, match="not eligibility authority"):
        evaluate_eligibility(context, _policies()[A])


def test_unrecognized_context_fields_fail_closed():
    context = _context()
    context["ui_hint"] = "gold badge"
    with pytest.raises(EligibilityError, match="unrecognized fields"):
        evaluate_eligibility(context, _policies()[A])


def test_receipts_never_carry_candidate_lead_contact_or_rank_fields():
    for receipt in evaluate_recipes(_context(), _policies().values()):
        body = receipt.to_dict()
        assert not (NEVER_EMITTED_RECEIPT_KEYS & set(body))
        assert receipt.opportunity_id == "candidate-willow-120"
        assert receipt.asset_id == "parcel-37183-0123456"
        assert receipt.producer_owner == "recipe-engine"
        assert receipt.evaluation_kind == "INDEPENDENT_ELIGIBILITY"
        leaked = dict(body, contact_authorized=True)
        leaked["content_sha256"] = content_hash(leaked)
        with pytest.raises(EligibilityError, match="non-eligibility authority"):
            validate_eligibility_receipt(leaked)


def test_producer_performs_no_io(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the eligibility producer must not touch files, sockets or databases")

    context, policies = _context(), list(_policies().values())
    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(sqlite3, "connect", refuse)
    journal = EligibilityReceiptJournal()
    for receipt in evaluate_recipes(context, policies):
        journal.append(receipt)
        to_intake_receipt(receipt)
    assert len(journal) == 4


def test_module_imports_no_transport_or_storage():
    source = inspect.getsource(elig)
    for name in ("import requests", "import psycopg", "import sqlite3", "import socket", "supabase", "deedsonar"):
        assert name not in source


# --------------------------------------------------------------------------
# supersession without deleting history
# --------------------------------------------------------------------------

def test_withdrawn_source_supersedes_prior_match_and_keeps_history():
    journal = EligibilityReceiptJournal()
    context, policy = _context(), _policies()[A]
    prior = evaluate_eligibility(context, policy)
    assert prior.decision == "MATCH"
    journal.append(prior)

    later = deepcopy(context)
    later["as_of"] = "2026-09-09T00:00:00Z"
    _evidence(later, "ev-tax-roll-2026")["withdrawn_at"] = "2026-09-08T12:00:00Z"
    superseding = evaluate_eligibility(later, policy, supersedes=prior.content_sha256)
    assert superseding.decision == "UNKNOWN"
    assert superseding.supersedes == prior.content_sha256
    assert superseding.membership_key == prior.membership_key
    body, appended = journal.append(superseding)
    assert appended is True

    assert journal.current(prior.membership_key)["content_sha256"] == superseding.content_sha256
    history = journal.history(prior.membership_key)
    assert [h["content_sha256"] for h in history] == [prior.content_sha256, superseding.content_sha256]
    assert history[0] == prior.to_dict()  # byte-identical, still there
    assert journal.superseded_by(prior.content_sha256) == superseding.content_sha256
    assert journal.verify() is True
    assert journal.to_dict()["supersessions"] == [
        {"superseded": prior.content_sha256, "superseding": superseding.content_sha256}
    ]


def test_second_live_truth_for_one_membership_is_refused():
    journal = EligibilityReceiptJournal()
    context, policies = _context(), _policies()
    v1 = evaluate_eligibility(context, policies[A])
    journal.append(v1)
    v2_policy = build_eligibility_policy(
        tenant_id=policies[A]["tenant_id"], recipe_id=A, recipe_version_id="published_tax_distress@elig.2",
        version="2", clauses=policies[A]["clauses"], ingredients=policies[A]["composition"]["ingredients"],
    )
    with pytest.raises(EligibilityError, match="must supersede"):
        journal.append(evaluate_eligibility(context, v2_policy))
    with pytest.raises(EligibilityError, match="supersedes unknown receipt"):
        journal.append(evaluate_eligibility(context, v2_policy, supersedes="9" * 64))
    other = evaluate_eligibility(context, policies[D])
    journal.append(other)
    with pytest.raises(EligibilityError, match="own membership"):
        journal.append(evaluate_eligibility(context, v2_policy, supersedes=other.content_sha256))
    v2 = evaluate_eligibility(context, v2_policy, supersedes=v1.content_sha256)
    assert journal.append(v2)[1] is True
    v3 = evaluate_eligibility(context, build_eligibility_policy(
        tenant_id=policies[A]["tenant_id"], recipe_id=A, recipe_version_id="published_tax_distress@elig.3",
        version="3", clauses=policies[A]["clauses"], ingredients=policies[A]["composition"]["ingredients"],
    ), supersedes=v1.content_sha256)
    with pytest.raises(EligibilityError, match="already superseded"):
        journal.append(v3)
    assert len(journal.history(v1.membership_key)) == 2


def test_supersession_cannot_point_backwards_in_time():
    journal = EligibilityReceiptJournal()
    context, policy = _context(), _policies()[A]
    later = deepcopy(context)
    later["as_of"] = "2026-09-09T00:00:00Z"
    newer = evaluate_eligibility(later, policy)
    journal.append(newer)
    older = evaluate_eligibility(context, policy, supersedes=newer.content_sha256)
    with pytest.raises(EligibilityError, match="later evaluation"):
        journal.append(older)


def test_evaluate_recipes_threads_supersession_by_membership():
    context, policies = _context(), _policies()
    first = evaluate_recipes(context, policies.values())
    later = deepcopy(context)
    later["as_of"] = "2026-09-09T00:00:00Z"
    second = evaluate_recipes(later, policies.values(),
                              supersedes_by_membership={r.membership_key: r.content_sha256 for r in first})
    assert [r.supersedes for r in second] == [r.content_sha256 for r in first]
    with pytest.raises(EligibilityError, match="requested twice"):
        evaluate_recipes(context, [policies[A], policies[A]])


# --------------------------------------------------------------------------
# receipt integrity
# --------------------------------------------------------------------------

def test_tampered_receipt_is_rejected():
    receipt = evaluate_eligibility(_context(), _policies()[A]).to_dict()
    flipped = dict(receipt, decision="NO_MATCH")
    with pytest.raises(EligibilityError, match="digest mismatch"):
        validate_eligibility_receipt(flipped)
    flipped["content_sha256"] = content_hash(flipped)
    with pytest.raises(EligibilityError, match="not what its clauses derive"):
        validate_eligibility_receipt(flipped)
    assert EligibilityReceipt.from_dict(receipt).content_sha256 == receipt["content_sha256"]


def test_known_decision_without_evidence_is_refused():
    receipt = evaluate_eligibility(_context(), _policies()[A]).to_dict()
    receipt["evidence_digests"] = {}
    receipt["content_sha256"] = content_hash(receipt)
    with pytest.raises(EligibilityError, match="requires evidence"):
        validate_eligibility_receipt(receipt)


# --------------------------------------------------------------------------
# consumer projection and cross-repo hash compatibility
# --------------------------------------------------------------------------

def test_intake_projection_is_exactly_the_consumer_contract():
    for receipt in evaluate_recipes(_context(), _policies().values()):
        projected = to_intake_receipt(receipt)
        assert tuple(projected) == INTAKE_RECEIPT_FIELDS
        assert projected["schema_version"] == INTAKE_RECEIPT_CONTRACT
        assert projected["mode"] == "FIXTURE"
        assert projected["decision"] == receipt.decision
        assert projected["content_sha256"] == _lro_content_hash(projected)
        assert projected["content_sha256"] != receipt.content_sha256
        assert membership_key(projected["tenant_id"], projected["universe_id"],
                              projected["opportunity_id"], projected["recipe_id"]) == receipt.membership_key


def test_intake_projection_refuses_to_relabel_non_fixture_modes():
    context = _context()
    context["mode"] = "SHADOW"
    receipt = evaluate_eligibility(context, _policies()[A])
    assert receipt.mode == "SHADOW"
    with pytest.raises(EligibilityError, match="FIXTURE mode only"):
        to_intake_receipt(receipt)


def test_astra_fixture_receipt_hash_reproduces_from_this_repository():
    assert content_hash(ASTRA_FIXTURE_RECEIPT) == ASTRA_FIXTURE_RECEIPT["content_sha256"]
    assert elig.composition_digest(ASTRA_FIXTURE_COMPOSITION) == ASTRA_FIXTURE_RECEIPT["composition_digest"]
    assert elig.render_timestamp(elig.parse_timestamp("2026-09-08T01:00:00+00:00", "t")) == "2026-09-08T01:00:00Z"
    assert elig.render_timestamp(elig.parse_timestamp("2026-09-08T01:00:00.5Z", "t")) == "2026-09-08T01:00:00.500000Z"


def test_non_ascii_content_hashes_the_consumer_way():
    policy = build_eligibility_policy(
        tenant_id="fixture-tenant", recipe_id="impôt_en_retard", recipe_version_id="impôt@1", version="1",
        clauses=[{"clause_id": "taxe_présente", "signal_type": "tax_delinquent", "expect": "PRESENT",
                  "negative_evidence_decisive": True}],
        ingredients=["COUNTY_TAX_ROLL"],
    )
    receipt = evaluate_eligibility(_context(), policy).to_dict()
    assert "présente" in receipt["reason"]
    assert receipt["content_sha256"] == _lro_content_hash(receipt)
    body = {k: v for k, v in receipt.items() if k != "content_sha256"}
    assert identity_digest(body) != receipt["content_sha256"]  # ensure_ascii divergence, documented


def test_contract_schemas_validate_fixture_policies_context_and_receipts():
    jsonschema = pytest.importorskip("jsonschema")
    root = Path(__file__).resolve().parents[2] / "contracts"
    schemas = {
        name: json.loads((root / f"eligibility.{name}.v1.schema.json").read_text(encoding="utf-8"))
        for name in ("policy", "context", "receipt")
    }
    for schema in schemas.values():
        jsonschema.Draft202012Validator.check_schema(schema)
    body = _fixture()
    jsonschema.Draft202012Validator(schemas["context"]).validate(body["context"])
    for policy in body["policies"].values():
        jsonschema.Draft202012Validator(schemas["policy"]).validate(policy)
    for receipt in evaluate_recipes(body["context"], body["policies"].values()):
        jsonschema.Draft202012Validator(schemas["receipt"]).validate(receipt.to_dict())
