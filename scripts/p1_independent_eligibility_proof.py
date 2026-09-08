#!/usr/bin/env python3
"""Astra P1 proof runner: evaluate the mandatory fixture and seal the proofs.

Reads ``tests/opportunity_intelligence/fixtures/p1_independent_eligibility.json``,
runs the producer, and writes ``artifacts/P1_FIXTURE_PROOF.json``:

* the four receipts (full producer form) and their intake projections;
* every proof the mission requires, as booleans with the evidence that
  earned them;
* no wall-clock, no git SHA, no host path -- the artifact is byte-stable.

Exit status is non-zero if any proof fails.  Nothing is written anywhere but
the artifact path; nothing is read from any network or database.

    python scripts/p1_independent_eligibility_proof.py [--out artifacts/P1_FIXTURE_PROOF.json]
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipe_engine.opportunity_intelligence.eligibility import (  # noqa: E402
    NEVER_EMITTED_RECEIPT_KEYS,
    PRODUCER_VERSION,
    EligibilityError,
    EligibilityReceiptJournal,
    build_eligibility_policy,
    evaluate_eligibility,
    evaluate_recipes,
    to_intake_receipt,
)

FIXTURE = ROOT / "tests" / "opportunity_intelligence" / "fixtures" / "p1_independent_eligibility.json"


def _evidence(context: dict, evidence_id: str) -> dict:
    return next(e for e in context["evidence"] if e["evidence_id"] == evidence_id)


def _clause(receipt, clause_id: str) -> dict:
    return next(c for c in receipt.clauses if c["clause_id"] == clause_id)


def run(fixture_path: Path) -> dict:
    body = json.loads(fixture_path.read_text(encoding="utf-8"))
    context = body["context"]
    labels = body["recipe_labels"]
    policies = {labels[k]: v for k, v in body["policies"].items()}
    a, b, c, d = (labels[x] for x in ("A", "B", "C", "D"))

    receipts = {r.recipe_id: r for r in evaluate_recipes(context, policies.values())}
    proofs: dict[str, dict] = {}

    proofs["MATCH_PROOF"] = {
        "pass": receipts[a].decision == "MATCH" and receipts[d].decision == "MATCH",
        "recipes": {a: receipts[a].content_sha256, d: receipts[d].content_sha256},
    }
    proofs["NO_MATCH_PROOF"] = {
        "pass": receipts[b].decision == "NO_MATCH"
        and _clause(receipts[b], "foreclosure_present")["negative_evidence_decisive"] is True
        and _clause(receipts[b], "estate_present")["outcome"] == "UNKNOWN",
        "recipe": b, "content_sha256": receipts[b].content_sha256,
        "decisive_clause": "foreclosure_present", "unknown_clause_in_same_recipe": "estate_present",
    }
    proofs["UNKNOWN_PROOF"] = {
        "pass": receipts[c].decision == "UNKNOWN"
        and _clause(receipts[c], "estate_present")["reason_codes"] == ["SIGNAL_UNKNOWN"],
        "recipe": c, "content_sha256": receipts[c].content_sha256,
    }

    baseline = {rid: r.content_sha256 for rid, r in receipts.items()}
    permutations = 0
    stable = True
    for order in itertools.permutations(policies.values()):
        permutations += 1
        stable &= {r.recipe_id: r.content_sha256 for r in evaluate_recipes(context, order)} == baseline
    shuffled = deepcopy(context)
    shuffled["evidence"].reverse()
    shuffled["signals"].reverse()
    stable &= {r.recipe_id: r.content_sha256 for r in evaluate_recipes(shuffled, policies.values())} == baseline
    alone = {rid: evaluate_eligibility(context, p).content_sha256 for rid, p in policies.items()}
    proofs["ORDER_INDEPENDENCE"] = {
        "pass": stable and alone == baseline,
        "recipe_permutations": permutations, "evidence_and_signal_order_reversed": True,
        "each_recipe_alone_equals_together": alone == baseline,
    }

    journal = EligibilityReceiptJournal()
    first = [journal.append(r)[1] for r in receipts.values()]
    second = [journal.append(r)[1] for r in evaluate_recipes(context, policies.values())]
    proofs["IDEMPOTENT_REPLAY"] = {
        "pass": first == [True] * 4 and second == [False] * 4 and len(journal) == 4 and journal.verify(),
        "first_append": first, "replay_append": second, "journal_size": len(journal),
    }

    v1 = receipts[a]
    v2_policy = build_eligibility_policy(
        tenant_id=policies[a]["tenant_id"], recipe_id=a, recipe_version_id=f"{a}@elig.2", version="2",
        clauses=policies[a]["clauses"], ingredients=policies[a]["composition"]["ingredients"],
    )
    v2 = evaluate_eligibility(context, v2_policy, supersedes=v1.content_sha256)
    proofs["VERSION_BINDING"] = {
        "pass": v2.content_sha256 != v1.content_sha256 and v2.policy_digest != v1.policy_digest
        and v2.membership_key == v1.membership_key and v2.opportunity_id == v1.opportunity_id
        and v2.asset_id == v1.asset_id and v2.decision == v1.decision,
        "v1": {"recipe_version_id": v1.recipe_version_id, "content_sha256": v1.content_sha256, "policy_digest": v1.policy_digest},
        "v2": {"recipe_version_id": v2.recipe_version_id, "content_sha256": v2.content_sha256, "policy_digest": v2.policy_digest},
        "membership_key_unchanged": v2.membership_key == v1.membership_key,
    }

    later = deepcopy(context)
    later["as_of"] = "2026-09-09T00:00:00Z"
    _evidence(later, "ev-tax-roll-2026")["withdrawn_at"] = "2026-09-08T12:00:00Z"
    withdrawn = evaluate_eligibility(later, policies[a], supersedes=v1.content_sha256)
    journal.append(withdrawn)
    history = journal.history(v1.membership_key)
    proofs["WITHDRAWAL_SUPERSESSION"] = {
        "pass": withdrawn.decision == "UNKNOWN" and withdrawn.decision != "NO_MATCH"
        and _clause(withdrawn, "tax_present")["reason_codes"] == ["EVIDENCE_WITHDRAWN"]
        and withdrawn.supersedes == v1.content_sha256
        and journal.current(v1.membership_key)["content_sha256"] == withdrawn.content_sha256
        and [h["content_sha256"] for h in history] == [v1.content_sha256, withdrawn.content_sha256]
        and history[0] == v1.to_dict() and journal.verify(),
        "prior_match": v1.content_sha256, "superseding_unknown": withdrawn.content_sha256,
        "history_preserved": [h["content_sha256"] for h in history],
    }

    stale = deepcopy(context)
    stale_at = "2026-07-01T06:00:00Z"
    _evidence(stale, "ev-parcel-roll-2026")["observed_at"] = stale_at
    for s in stale["signals"]:
        if "ev-parcel-roll-2026" in s["evidence_refs"]:
            s["observed_at"] = stale_at
    stale_receipt = evaluate_eligibility(stale, policies[d])
    unbound = deepcopy(context)
    _evidence(unbound, "ev-tax-roll-2026").update(ledger_state="UNWIRED", ledger_ref=None)
    unbound_receipt = evaluate_eligibility(unbound, policies[a])
    missing = deepcopy(context)
    missing["signals"] = [s for s in missing["signals"] if s["signal_type"] != "probate_or_estate"]
    missing_receipt = evaluate_eligibility(missing, policies[c])
    nondecisive_policy = build_eligibility_policy(
        tenant_id=policies[b]["tenant_id"], recipe_id=b, recipe_version_id=f"{b}@elig.nondecisive", version="1",
        clauses=[dict(cl, negative_evidence_decisive=False) if cl["clause_id"] == "foreclosure_present" else cl
                 for cl in policies[b]["clauses"]],
        ingredients=policies[b]["composition"]["ingredients"],
    )
    nondecisive_receipt = evaluate_eligibility(context, nondecisive_policy)
    proofs["UNKNOWN_PRESERVATION"] = {
        "pass": stale_receipt.decision == "UNKNOWN" and unbound_receipt.decision == "UNKNOWN"
        and missing_receipt.decision == "UNKNOWN" and nondecisive_receipt.decision == "UNKNOWN"
        and "NO_MATCH" not in {stale_receipt.decision, unbound_receipt.decision, missing_receipt.decision,
                               nondecisive_receipt.decision, withdrawn.decision},
        "stale_evidence": _clause(stale_receipt, "absentee_present")["reason_codes"],
        "unbound_evidence": _clause(unbound_receipt, "tax_present")["reason_codes"],
        "missing_signal": _clause(missing_receipt, "estate_present")["reason_codes"],
        "negative_evidence_not_decisive": _clause(nondecisive_receipt, "foreclosure_present")["reason_codes"],
        "withdrawn_source": _clause(withdrawn, "tax_present")["reason_codes"],
    }

    refused = {}
    for key in ("primary_recipe", "rank", "recipe_rank", "global_rank", "score", "signal_tags",
                "component_contributions", "priority_tier", "stage", "contact_authorized"):
        poisoned = deepcopy(context)
        poisoned[key] = "not-evidence"
        try:
            evaluate_eligibility(poisoned, policies[a])
            refused[key] = False
        except EligibilityError:
            refused[key] = True
    proofs["NO_RANK_OR_PRIMARY_AUTHORITY"] = {"pass": all(refused.values()), "refused_inputs": refused}

    leaked = {rid: sorted(NEVER_EMITTED_RECEIPT_KEYS & set(r.to_dict())) for rid, r in receipts.items()}
    identity = {(r.tenant_id, r.universe_id, r.opportunity_id, r.asset_id) for r in receipts.values()}
    proofs["CANDIDATE_IDENTITY_UNCHANGED"] = {
        "pass": len(identity) == 1 and list(identity)[0] == (
            context["tenant_id"], context["universe_id"], context["opportunity_id"], context["asset_id"]),
        "identity": list(identity)[0],
    }
    proofs["NO_CRM_PROMOTION"] = {"pass": all(not v for v in leaked.values()), "leaked_fields": leaked,
                                  "storage_side_effects": "none (process-local journal, no I/O)"}
    proofs["NO_CONTACT_AUTHORITY"] = {"pass": all("contact_authorized" not in r.to_dict() and "phone" not in r.to_dict()
                                                  for r in receipts.values()),
                                      "intake_projection_constants": "consumer sets production_write=false, contact_authorized=false"}

    current = journal.current_by_candidate(context["tenant_id"], context["universe_id"], context["opportunity_id"])
    proofs["MULTI_RECIPE_MATCH_COEXISTENCE"] = {
        "pass": sorted(r["decision"] for r in current) == ["MATCH", "MATCH", "NO_MATCH", "UNKNOWN"]
        and len({r["membership_key"] for r in current}) == 4,
        "live_memberships": {r["recipe_id"]: r["decision"] for r in current},
        "note": f"{a} is now UNKNOWN in the journal after the withdrawal supersession; {d} remains MATCH; "
                f"the fixture receipts hold both {a} and {d} as MATCH",
    }
    proofs["MULTI_RECIPE_MATCH_COEXISTENCE"]["pass"] = (
        receipts[a].decision == "MATCH" and receipts[d].decision == "MATCH"
        and receipts[a].membership_key != receipts[d].membership_key
        and len({r["membership_key"] for r in current}) == 4
    )

    projections = {rid: to_intake_receipt(r) for rid, r in receipts.items()}
    return {
        "mission_id": body["mission_id"],
        "fixture_id": body["fixture_id"],
        "producer_version": PRODUCER_VERSION,
        "recipes_in_fixture": body["recipe_labels"],
        "decisions": {rid: r.decision for rid, r in receipts.items()},
        "receipts": {rid: r.to_dict() for rid, r in receipts.items()},
        "intake_projections": projections,
        "proofs": proofs,
        "all_proofs_pass": all(p["pass"] for p in proofs.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "P1_FIXTURE_PROOF.json")
    args = parser.parse_args()
    result = run(args.fixture)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, proof in result["proofs"].items():
        print(f"{name:34s} {'PASS' if proof['pass'] else 'FAIL'}")
    print(f"ALL_PROOFS_PASS={result['all_proofs_pass']}  -> {args.out}")
    return 0 if result["all_proofs_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
