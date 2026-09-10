"""Recipe compiler court: the canonical 30 compile, and every compiled recipe is
evaluated independently with UNKNOWN kept distinct from NO_MATCH.

Everything here is synthetic.  Nothing here is a live finding, a candidate
mint, a lead, or contact authority.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
from copy import deepcopy
from pathlib import Path

import pytest

from recipe_engine.opportunity_intelligence import canonical_recipes as spec_mod
from recipe_engine.opportunity_intelligence.eligibility import (
    ELIGIBILITY_CONTEXT_CONTRACT,
    to_intake_receipt,
    validate_eligibility_policy,
    validate_eligibility_receipt,
)
from recipe_engine.opportunity_intelligence.recipe_compiler import (
    COMPILED_RECIPE_SET_CONTRACT,
    RECIPE_EVALUATION_INPUT_CONTRACT,
    SCORE_DIMENSIONS,
    RecipeCompilerError,
    compile_canonical_recipes,
    compiled_summary_rows,
    evaluate_canonical_recipes,
    lower_to_eligibility_policy,
    to_next_best_evidence_gaps,
    to_score_vector_components,
    validate_compiled_set,
    validate_recipe_spec,
)

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = ROOT / "artifacts" / "recipe_compiler"

#: recipe_id column of lead-recipe-os artifacts/CANONICAL_30_RECIPE_MATRIX.tsv @40d48d4
#: (sha256 a52965b8...), in recipe_n order.
CANONICAL_30_IDS = (
    "tax_threshold_watch", "tax_pressure_equity", "tax_out_of_state_equity", "open_probate_property",
    "pre_probate_verified_death_event_property", "estate_sale_property", "estate_tax_pressure",
    "stalled_tax_foreclosure_heirship", "heir_curative", "title_curative", "code_tax_equity", "code_estate",
    "failed_listing_high_equity_long_tenure", "long_hold_free_clear", "long_hold_high_equity",
    "absentee_code_equity", "defunct_entity_on_title", "vacant_estate_tax", "old_mortgage_release_gap",
    "lien_stack_equity", "tax_sale_rescue_pre_sale", "redemption_window", "storm_absentee", "storm_estate",
    "agricultural_succession", "assemblage", "infill_land_improvement_imbalance",
    "vacant_demolition_order_land_value", "ownership_transition_watch", "data_gap_nbe_recipe",
)

FORBIDDEN_OUTPUT_KEYS = {"primary_recipe", "rank", "recipe_rank", "global_rank", "score", "lead_score",
                         "contact_authorized", "priority_tier"}

AS_OF = "2026-09-10T00:00:00Z"
OBSERVED = "2026-09-01T00:00:00Z"


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def evidence(evidence_id: str, family: str, **extra):
    item = {
        "evidence_id": evidence_id, "source_id": f"src.{evidence_id}", "source_family": family,
        "content_sha256": _sha(evidence_id), "observed_at": OBSERVED, "ledger_state": "BOUND",
        "ledger_ref": f"ledger:{evidence_id}",
    }
    item.update(extra)
    return item


def signal(signal_id: str, ingredient: str, state: str, refs, confidence: str = "HIGH"):
    return {"signal_id": signal_id, "signal_type": ingredient, "state": state, "confidence": confidence,
            "evidence_refs": list(refs), "source_families": [], "observed_at": OBSERVED}


def measurement(mid: str, ingredient: str, value, refs, basis: str = "ESTIMATED", confidence: str = "MEDIUM"):
    return {"measurement_id": mid, "ingredient": ingredient, "value": value, "basis": basis,
            "confidence": confidence, "evidence_refs": list(refs), "observed_at": OBSERVED}


def make_input(signals=(), measurements=(), extra_evidence=(), opportunity_id="opp-1"):
    ev = [
        evidence("tax_list", "tax_delinquency"),
        evidence("roll", "owner_roll"),
        evidence("avm", "assessor"),
        evidence("code_portal", "code"),
        evidence("docket", "tax_foreclosure"),
        evidence("probate_index", "probate"),
        evidence("deed", "deed"),
        *extra_evidence,
    ]
    return {
        "schema_version": RECIPE_EVALUATION_INPUT_CONTRACT,
        "context": {
            "schema_version": ELIGIBILITY_CONTEXT_CONTRACT, "mode": "SYNTHETIC", "tenant_id": "tenant-x",
            "universe_id": "universe-x", "opportunity_id": opportunity_id, "asset_id": "asset-1", "as_of": AS_OF,
            "evidence": ev, "signals": list(signals),
        },
        "measurements": list(measurements),
    }


def base_property(**overrides):
    """Tax-delinquent, out-of-state, not government, open code case, equity ~72% (estimated)."""
    signals = {
        "tax": signal("s-tax", "tax.delinquent", "PRESENT", ["tax_list"]),
        "oos": signal("s-oos", "absentee.out_of_state", "PRESENT", ["roll"]),
        "gov": signal("s-gov", "ownership.government", "ABSENT", ["roll"]),
        "code": signal("s-code", "code.violation", "PRESENT", ["code_portal"]),
    }
    measurements = {"equity": measurement("m-eq", "equity.estimated_pct", 72, ["avm"])}
    for key, value in overrides.items():
        target = measurements if key.startswith("m_") else signals
        name = key[2:] if key.startswith("m_") else key
        if value is None:
            target.pop(name, None)
        else:
            target[name] = value
    return make_input(signals.values(), measurements.values())


@pytest.fixture(scope="module")
def compiled():
    return compile_canonical_recipes()


def run(payload, compiled_set):
    return evaluate_canonical_recipes(payload, compiled_set)


def by_id(result):
    return {e["recipe_id"]: e for e in result["evaluations"]}


def decisions(result):
    return {m["recipe_id"]: m["decision"] for m in result["memberships"]}


def walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_keys(item)


# --------------------------------------------------------------------------
# compile
# --------------------------------------------------------------------------

def test_the_canonical_30_compile_in_canonical_order(compiled):
    assert compiled["schema_version"] == COMPILED_RECIPE_SET_CONTRACT
    assert tuple(r["recipe_id"] for r in compiled["recipes"]) == CANONICAL_30_IDS
    assert [r["recipe_n"] for r in compiled["recipes"]] == list(range(1, 31))
    validate_compiled_set(compiled)


def test_every_compiled_recipe_carries_all_eight_stages(compiled):
    for recipe in compiled["recipes"]:
        for stage in ("input_signals", "eligibility", "score_vector", "required_evidence", "nbe_plan",
                      "economics", "qualification_policy"):
            assert stage in recipe, (recipe["recipe_id"], stage)
        assert recipe["score_vector"]["total"] is None, "no generic lead score"
        assert [d["name"] for d in recipe["score_vector"]["dimensions"]] == [n for n, _ in SCORE_DIMENSIONS]
        if recipe["kind"] != "META":
            assert recipe["eligibility"]["membership_predicates"], recipe["recipe_id"]
            assert recipe["input_signals"], recipe["recipe_id"]


def test_compile_is_deterministic_and_content_addressed(compiled):
    assert compile_canonical_recipes() == compiled
    tampered = deepcopy(compiled)
    tampered["recipes"][0]["requirements"][0]["negative_evidence_decisive"] = True
    with pytest.raises(RecipeCompilerError, match="digest mismatch"):
        validate_compiled_set(tampered)


def test_compile_states_are_computed_from_source_states_not_asserted(compiled):
    states = {r["recipe_id"]: r["compile_state"] for r in compiled["recipes"]}
    assert states["data_gap_nbe_recipe"] == "META"
    assert states["infill_land_improvement_imbalance"] == "PREDICATE_UNDEFINED"
    # listing history is WALLED; tenure has no NC deed tape; storm has no adapter
    for rid in ("failed_listing_high_equity_long_tenure", "long_hold_free_clear", "storm_absentee",
                "pre_probate_verified_death_event_property"):
        assert states[rid] == "SOURCE_BLOCKED", rid
    for rid in ("tax_threshold_watch", "stalled_tax_foreclosure_heirship", "code_tax_equity"):
        assert states[rid] == "PARTIAL", rid
    assert not [r for r in compiled["recipes"] if r["compile_state"] == "EXECUTABLE"], (
        "no membership predicate is fully AVAILABLE today; claiming EXECUTABLE would be aspiration")


def test_only_exactly_expressible_recipes_lower_to_eligibility_policy(compiled):
    lowered = [r["recipe_id"] for r in compiled["recipes"] if r["p1_lowering"]["state"] == "LOWERED"]
    assert lowered == ["tax_threshold_watch"]
    ingredients = compiled["ingredients"]
    for recipe in compiled["recipes"]:
        policy = lower_to_eligibility_policy(recipe, ingredients, tenant_id="tenant-x")
        if recipe["recipe_id"] in lowered:
            validate_eligibility_policy(policy)
            assert [c["signal_type"] for c in policy["clauses"]] == ["tax_delinquent"]
        else:
            assert policy is None, recipe["recipe_id"]
            assert recipe["p1_lowering"]["reasons"], recipe["recipe_id"]


def test_spec_refuses_what_cannot_compile():
    spec = spec_mod.canonical_recipe_spec()

    def mutate(fn):
        broken = deepcopy(spec)
        fn(broken)
        return broken

    def first_req(s):
        return s["recipes"][0]["requirements"][0]

    cases = [
        (lambda s: first_req(s).update(ingredient="tax.nope"), "not in the ingredient vocabulary"),
        (lambda s: first_req(s).update(op="gte", value=3), "not valid for a BOOLEAN"),
        (lambda s: first_req(s).update(negative_evidence_decisive=None), "explicit boolean"),
        (lambda s: s["recipes"][1]["requirements"][1].update(min_basis="VERIFIED"), "could never be satisfied"),
        (lambda s: s["recipes"][1].update(recipe_n=1), "unique integer"),
        (lambda s: s["recipes"][0].update(requirements=[r for r in s["recipes"][0]["requirements"]
                                                        if r["role"] != "REQUIRED"]), "REQUIRED membership"),
        (lambda s: s["ingredients"]["estate.open"].update(engine_signal_type="tax_delinquent"), "one-to-one"),
        (lambda s: s["ingredients"]["equity.estimated_pct"].update(engine_signal_type="value_spread"),
         "only BOOLEAN"),
        (lambda s: s["recipes"][0]["requirements"][1].update(value=["NOT_A_STATE"]), "outside"),
        (lambda s: s["recipes"][0].update(process_clock="made_up_clock"), "not a known process clock"),
    ]
    validate_recipe_spec(spec)
    for fn, message in cases:
        with pytest.raises(RecipeCompilerError, match=message):
            validate_recipe_spec(mutate(fn))


def test_every_ingredient_names_a_genome_id_and_every_reference_resolves(compiled):
    ingredients = compiled["ingredients"]
    for name, info in ingredients.items():
        assert info["genome_ingredient_id"].startswith("I-"), name
    for recipe in compiled["recipes"]:
        for item in recipe["input_signals"]:
            assert item["ingredient"] in ingredients


def test_committed_artifacts_are_exactly_what_the_compiler_emits(compiled):
    committed = json.loads((ARTIFACT_DIR / "COMPILED_CANONICAL_30.v1.json").read_text())
    assert committed == compiled, "regenerate with scripts/compile_canonical_recipes.py"
    with (ARTIFACT_DIR / "COMPILED_CANONICAL_30.tsv").open() as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert rows == compiled_summary_rows(compiled)


# --------------------------------------------------------------------------
# evaluate: membership semantics
# --------------------------------------------------------------------------

def test_one_property_is_a_member_of_several_recipes_at_once(compiled):
    result = run(base_property(), compiled)
    got = decisions(result)
    matches = sorted(r for r, d in got.items() if d == "MATCH")
    assert matches == ["absentee_code_equity", "code_tax_equity", "tax_out_of_state_equity",
                       "tax_pressure_equity", "tax_threshold_watch"]
    assert len(result["memberships"]) == 29, "every non-META recipe answers; none is dropped"
    assert len(result["data_gap_nbe"]) == 1


def test_no_primary_rank_or_scalar_score_anywhere(compiled):
    result = run(base_property(), compiled)
    leaked = FORBIDDEN_OUTPUT_KEYS & set(walk_keys(result))
    assert not leaked
    leaked_compiled = FORBIDDEN_OUTPUT_KEYS & set(walk_keys(compiled))
    assert not leaked_compiled


def test_a_missing_signal_is_unknown_never_no_match(compiled):
    result = run(base_property(code=None), compiled)
    ev = by_id(result)["code_tax_equity"]
    assert ev["decision"] == "UNKNOWN"
    [clause] = [c for c in ev["clauses"] if c["clause_id"] == "code_violation"]
    assert clause["reason_codes"] == ["NO_SIGNAL"]
    assert ev["missing_evidence"][0]["requirement_id"] == "code_violation"


def test_resolved_taxes_are_negative_evidence_not_the_opposite_fact(compiled):
    resolved = signal("s-tax", "tax.delinquent", "ABSENT", ["tax_list"])
    ev = by_id(run(base_property(tax=resolved), compiled))
    assert ev["tax_threshold_watch"]["decision"] == "UNKNOWN"
    [clause] = [c for c in ev["tax_threshold_watch"]["clauses"] if c["clause_id"] == "tax_delinquent"]
    assert clause["reason_codes"] == ["CONTRADICTED_ABSENT", "CONTRADICTION_NOT_DECISIVE"]


def test_an_estimate_below_a_threshold_is_never_decisive(compiled):
    low = measurement("m-eq", "equity.estimated_pct", 40, ["avm"], basis="ESTIMATED")
    ev = by_id(run(base_property(m_equity=low), compiled))
    for rid in ("tax_pressure_equity", "code_tax_equity", "absentee_code_equity"):
        assert ev[rid]["decision"] == "UNKNOWN", rid
        [c] = [c for c in ev[rid]["clauses"] if c["clause_id"] == "equity_65"]
        assert c["outcome"] == "CONTRADICTED" and c["negative_evidence_decisive"] is False


def test_a_verified_measurement_below_a_threshold_is_decisive(compiled):
    tenure = measurement("m-ten", "transfer.tenure_years", 4, ["deed"], basis="VERIFIED", confidence="HIGH")
    ev = by_id(run(base_property(m_tenure=tenure), compiled))
    assert ev["long_hold_free_clear"]["decision"] == "NO_MATCH"
    assert ev["long_hold_free_clear"]["qualification"]["state"] == "NOT_ELIGIBLE"
    assert ev["long_hold_free_clear"]["next_best_evidence"] == [], "a known exclusion is a finding, not a gap"
    estimated = measurement("m-ten", "transfer.tenure_years", 4, ["deed"], basis="ESTIMATED")
    ev2 = by_id(run(base_property(m_tenure=estimated), compiled))
    # tenure_15 requires VERIFIED: an estimate is below the basis floor, so UNKNOWN
    [c] = [c for c in ev2["long_hold_free_clear"]["clauses"] if c["clause_id"] == "tenure_15"]
    assert c["reason_codes"] == ["BASIS_BELOW_FLOOR"]


def test_a_found_defeater_decides_and_an_unobserved_one_does_not(compiled):
    probate = signal("s-pro", "probate.opened", "PRESENT", ["probate_index"])
    unobserved = by_id(run(base_property(pro=probate), compiled))["open_probate_property"]
    assert unobserved["decision"] == "MATCH"
    closed = signal("s-closed", "probate.closed", "PRESENT", ["probate_index"])
    found = by_id(run(base_property(pro=probate, closed=closed), compiled))["open_probate_property"]
    assert found["decision"] == "NO_MATCH"
    assert "estate_closed" in found["reason"]
    assert any(c["outcome"] == "UNKNOWN" for c in found["clauses"] if c["role"] == "QUALIFYING")
    assert found["next_best_evidence"] == [], "open qualifying items do not matter once membership is excluded"


def test_probate_routes_a_pre_probate_death_to_r4(compiled):
    death = signal("s-death", "death.verified", "PRESENT", ["deed"])
    probate = signal("s-pro", "probate.opened", "PRESENT", ["probate_index"])
    before = decisions(run(base_property(death=death), compiled))
    after = decisions(run(base_property(death=death, pro=probate), compiled))
    assert before["pre_probate_verified_death_event_property"] == "MATCH"
    assert after["pre_probate_verified_death_event_property"] == "NO_MATCH"
    assert after["open_probate_property"] == "MATCH"


def test_any_of_satisfies_on_one_alternative_and_contradicts_only_on_all(compiled):
    fc = signal("s-fc", "tax.foreclosed", "PRESENT", ["docket"])
    heir = signal("s-heir", "heirship.unresolved", "PRESENT", ["deed"])
    ev = by_id(run(base_property(fc=fc, heir=heir), compiled))["stalled_tax_foreclosure_heirship"]
    assert ev["decision"] == "MATCH"
    [anyof] = [c for c in ev["clauses"] if c["clause_id"] == "deceased_or_heirship"]
    assert anyof["reason_codes"] == ["SATISFIED_ANY_OF"]
    no_heir = signal("s-heir", "heirship.unresolved", "ABSENT", ["deed"])
    ev2 = by_id(run(base_property(fc=fc, heir=no_heir), compiled))["stalled_tax_foreclosure_heirship"]
    assert ev2["decision"] == "UNKNOWN", "estate.open and death.verified are still unknown"


def test_a_disqualified_property_is_still_a_member(compiled):
    gov = signal("s-gov", "ownership.government", "PRESENT", ["roll"])
    ev = by_id(run(base_property(gov=gov), compiled))["code_tax_equity"]
    assert ev["decision"] == "MATCH"
    assert ev["qualification"]["state"] == "DISQUALIFIED"
    unknown_gov = by_id(run(base_property(gov=None), compiled))["code_tax_equity"]
    assert unknown_gov["decision"] == "MATCH"
    assert unknown_gov["qualification"]["state"] == "NEEDS_EVIDENCE"
    assert "not_government_owned" in unknown_gov["qualification"]["blocking_requirements"]


def test_stall_is_qualifying_not_membership_for_r8(compiled):
    fc = signal("s-fc", "tax.foreclosed", "PRESENT", ["docket"])
    heir = signal("s-heir", "heirship.unresolved", "PRESENT", ["deed"])
    ev = by_id(run(base_property(fc=fc, heir=heir), compiled))["stalled_tax_foreclosure_heirship"]
    assert ev["decision"] == "MATCH"
    assert ev["qualification"]["state"] == "NEEDS_EVIDENCE"
    [gap] = [g for g in ev["next_best_evidence"] if g["requirement_id"] == "stalled"]
    assert gap["blocks"] == "QUALIFICATION"
    progressing = measurement("m-clk", "clock.foreclosure_transition_clock", "PROGRESSING", ["docket"],
                              basis="VERIFIED", confidence="HIGH")
    ev2 = by_id(run(base_property(fc=fc, heir=heir, m_clock=progressing), compiled))[
        "stalled_tax_foreclosure_heirship"]
    assert ev2["decision"] == "MATCH"
    assert ev2["qualification"]["state"] == "NOT_QUALIFIED"


def test_stale_withdrawn_and_unbound_evidence_cannot_support_a_finding(compiled):
    for extra, code in (
        ({"ledger_state": "NOT_EVALUATED", "ledger_ref": None}, "EVIDENCE_UNBOUND"),
        ({"withdrawn_at": "2026-09-05T00:00:00Z"}, "EVIDENCE_WITHDRAWN"),
    ):
        payload = base_property()
        payload["context"]["evidence"] = [
            {**e, **extra} if e["evidence_id"] == "code_portal" else e for e in payload["context"]["evidence"]
        ]
        ev = by_id(run(payload, compiled))["code_tax_equity"]
        assert ev["decision"] == "UNKNOWN"
        [c] = [c for c in ev["clauses"] if c["clause_id"] == "code_violation"]
        assert c["reason_codes"] == [code]


def test_unknown_undefined_predicate_is_its_own_qualification_state(compiled):
    acres = measurement("m-ac", "acreage.acres", 3, ["avm"], basis="VERIFIED", confidence="HIGH")
    ev = by_id(run(base_property(m_acres=acres), compiled))["infill_land_improvement_imbalance"]
    assert ev["decision"] == "UNKNOWN"
    assert ev["qualification"]["state"] == "PREDICATE_UNDEFINED"
    [gap] = [g for g in ev["next_best_evidence"] if g["blocks"] == "MEMBERSHIP"]
    assert gap["information_gain"] == 0.0, "no source can resolve an undefined threshold"


def test_source_blocked_when_a_membership_predicate_has_no_producer(compiled):
    ev = by_id(run(base_property(), compiled))
    assert ev["storm_estate"]["qualification"]["state"] == "SOURCE_BLOCKED"
    assert ev["failed_listing_high_equity_long_tenure"]["qualification"]["state"] == "SOURCE_BLOCKED"


# --------------------------------------------------------------------------
# evaluate: score vector, NBE, economics, projections
# --------------------------------------------------------------------------

def test_score_vector_keeps_unknown_null_and_owners_honest(compiled):
    ev = by_id(run(base_property(), compiled))
    for evaluation in ev.values():
        vector = {c["name"]: c for c in evaluation["score_vector"]}
        assert list(vector) == [n for n, _ in SCORE_DIMENSIONS]
        for component in vector.values():
            if component["evaluation_state"] != "EVALUATED":
                assert component["value"] is None, (evaluation["recipe_id"], component["name"])
        assert vector["contactability"]["evaluation_state"] == "NOT_EVALUATED"
        assert vector["liquidity"]["evaluation_state"] == "UNWIRED"
        assert vector["evidence_confidence"]["organ"] == "landos-engine"
    code_tax = {c["name"]: c for c in ev["code_tax_equity"]["score_vector"]}
    assert code_tax["strategic_fit"]["value"] == 1.0
    assert code_tax["economic_value"]["value"] == 1.0
    assert code_tax["source_independence_diversity"]["value"] == 4


def test_urgency_reads_the_recipe_deadline(compiled):
    sale = measurement("m-sale", "tax_sale.sale_date", "2026-10-01", ["docket"], basis="VERIFIED",
                       confidence="HIGH")
    ev = by_id(run(base_property(m_sale=sale), compiled))["tax_sale_rescue_pre_sale"]
    assert ev["decision"] == "MATCH"
    urgency = {c["name"]: c for c in ev["score_vector"]}["urgency"]
    assert (urgency["evaluation_state"], urgency["value"]) == ("EVALUATED", 21)
    past = measurement("m-sale", "tax_sale.sale_date", "2026-09-01", ["docket"], basis="VERIFIED",
                       confidence="HIGH")
    ev2 = by_id(run(base_property(m_sale=past), compiled))["tax_sale_rescue_pre_sale"]
    assert ev2["decision"] == "UNKNOWN", "a past date may be a continued sale, not a held one"


def test_nbe_ranks_the_single_deciding_fact_first(compiled):
    ev = by_id(run(base_property(code=None), compiled))["code_tax_equity"]
    first = ev["next_best_evidence"][0]
    assert first["requirement_id"] == "code_violation"
    assert first["decides_membership"] is True
    assert first["blocks"] == "MEMBERSHIP"
    assert first["information_gain"] == 0.75
    assert [g["nbe_order"] for g in ev["next_best_evidence"]] == list(range(1, len(ev["next_best_evidence"]) + 1))


def test_data_gap_meta_recipe_lists_memberships_one_fact_away(compiled):
    result = run(base_property(code=None), compiled)
    [meta] = result["data_gap_nbe"]
    assert meta["recipe_id"] == "data_gap_nbe_recipe"
    blocked = {item["recipe_id"]: item["requirement_id"] for item in meta["blocked_by_one_fact"]}
    assert blocked["code_tax_equity"] == "code_violation"
    assert blocked["absentee_code_equity"] == "code_violation"
    assert "tax_threshold_watch" not in blocked, "a MATCH is not a data gap"


def test_economics_undefined_never_blocks_and_defined_reports_basis(compiled):
    ev = by_id(run(base_property(), compiled))
    assert ev["tax_threshold_watch"]["economics"]["outcome"] == "UNDEFINED"
    assert ev["code_tax_equity"]["economics"]["outcome"] == "PASS"
    assert ev["code_tax_equity"]["economics"]["basis"] == "ESTIMATED"
    assert ev["code_tax_equity"]["qualification"]["economics_basis"] == "ESTIMATED"


def test_lead_recipe_os_projections_fit_the_kernel_shapes(compiled):
    ev = by_id(run(base_property(code=None), compiled))["code_tax_equity"]
    components = to_score_vector_components(ev)
    allowed = {"NOT_EVALUATED", "EVALUATED", "UNWIRED", "SOURCE_FAILED", "UNKNOWN", "ABSENT", "CONFLICTED",
               "ZERO_RESULTS"}
    for c in components:
        assert set(c) == {"name", "organ", "evaluation_state", "value", "formula", "mode", "receipt_sha256"}
        assert c["name"].startswith("code_tax_equity.")
        assert c["evaluation_state"] in allowed
        assert c["mode"] == "FIXTURE"
    gaps = to_next_best_evidence_gaps(ev)
    for gap in gaps:
        assert set(gap) == {"gap_id", "kind", "organ", "evaluation_state", "expected_information",
                            "information_gain", "blocking_lead"}
        assert 0 <= gap["information_gain"] <= 1


# --------------------------------------------------------------------------
# P1 parity, determinism, refusals
# --------------------------------------------------------------------------

def test_lowered_recipes_emit_p1_receipts_with_the_same_decision(compiled):
    for tax_state, expected in (("PRESENT", "MATCH"), ("ABSENT", "UNKNOWN")):
        tax = signal("s-tax", "tax.delinquent", tax_state, ["tax_list"])
        payload = base_property(tax=tax)
        payload["context"]["mode"] = "FIXTURE"  # the lead-recipe-os intake court accepts FIXTURE only
        result = run(payload, compiled)
        [receipt] = result["p1_eligibility_receipts"]
        validate_eligibility_receipt(receipt)
        assert receipt["recipe_id"] == "tax_threshold_watch"
        assert receipt["decision"] == expected == decisions(result)["tax_threshold_watch"]
        assert receipt["membership_key"] == by_id(result)["tax_threshold_watch"]["membership_key"]
        if expected == "MATCH":
            to_intake_receipt(receipt)


def test_evaluation_is_independent_of_input_order(compiled):
    payload = base_property(fc=signal("s-fc", "tax.foreclosed", "PRESENT", ["docket"]))
    shuffled = deepcopy(payload)
    rng = random.Random(7)
    for key in ("evidence", "signals"):
        rng.shuffle(shuffled["context"][key])
    rng.shuffle(shuffled["measurements"])
    reversed_set = deepcopy(compiled)
    assert run(payload, compiled)["content_sha256"] == run(shuffled, reversed_set)["content_sha256"]


def test_a_recipe_evaluation_does_not_depend_on_the_other_recipes(compiled):
    spec = spec_mod.canonical_recipe_spec()
    spec["recipes"] = [r for r in spec["recipes"] if r["recipe_id"] in ("code_tax_equity", "open_probate_property")]
    subset = compile_canonical_recipes(spec)
    payload = base_property(pro=signal("s-pro", "probate.opened", "PRESENT", ["probate_index"]))
    alone = by_id(run(payload, subset))
    together = by_id(run(payload, compiled))
    for rid in alone:
        assert alone[rid]["content_sha256"] == together[rid]["content_sha256"], rid


def test_a_new_fact_moves_exactly_the_recipes_that_read_it(compiled):
    base = by_id(run(base_property(), compiled))
    probate = signal("s-pro", "probate.opened", "PRESENT", ["probate_index"])
    with_probate = by_id(run(base_property(pro=probate), compiled))
    changed = {rid for rid in base if base[rid]["decision"] != with_probate[rid]["decision"]}
    readers = {r["recipe_id"] for r in compiled["recipes"]
               if "probate.opened" in {i["ingredient"] for i in r["input_signals"]}}
    assert changed <= readers
    assert changed == {"open_probate_property", "estate_tax_pressure", "code_estate",
                       "pre_probate_verified_death_event_property"}


def test_refuses_ranking_inputs_and_wrong_channels(compiled):
    payload = base_property()
    payload["primary_recipe"] = "published_tax_distress"
    with pytest.raises(RecipeCompilerError, match="not eligibility authority"):
        run(payload, compiled)
    payload = base_property()
    payload["measurements"][0]["score"] = 90
    with pytest.raises(RecipeCompilerError, match="not eligibility authority"):
        run(payload, compiled)
    payload = base_property()
    payload["context"]["signals"].append(signal("s-eq", "equity.estimated_pct", "PRESENT", ["avm"]))
    with pytest.raises(RecipeCompilerError, match="send it as a measurement"):
        run(payload, compiled)
    payload = base_property()
    payload["measurements"].append(measurement("m-tax", "tax.delinquent", 1, ["tax_list"]))
    with pytest.raises(RecipeCompilerError, match="send it as a context signal"):
        run(payload, compiled)
    payload = base_property()
    payload["measurements"][0]["basis"] = "VERIFIED"
    with pytest.raises(RecipeCompilerError, match="at best ESTIMATED"):
        run(payload, compiled)


def test_conflicting_measurements_are_unknown(compiled):
    payload = base_property()
    payload["context"]["evidence"].append(evidence("avm2", "assessor"))
    payload["measurements"].append(measurement("m-eq2", "equity.estimated_pct", 30, ["avm2"]))
    ev = by_id(run(payload, compiled))["code_tax_equity"]
    [c] = [c for c in ev["clauses"] if c["clause_id"] == "equity_65"]
    assert (c["outcome"], c["observed_state"]) == ("UNKNOWN", "CONFLICTED")
    assert ev["decision"] == "UNKNOWN"


def test_withdrawn_or_stale_evidence_behind_a_measurement_supports_nothing(compiled):
    for extra, code in (
        ({"withdrawn_at": "2026-09-05T00:00:00Z"}, "EVIDENCE_WITHDRAWN"),
        ({"ledger_state": "SOURCE_FAILED", "ledger_ref": None}, "EVIDENCE_UNBOUND"),
    ):
        payload = base_property()
        payload["context"]["evidence"] = [
            {**e, **extra} if e["evidence_id"] == "avm" else e for e in payload["context"]["evidence"]
        ]
        ev = by_id(run(payload, compiled))["code_tax_equity"]
        [c] = [c for c in ev["clauses"] if c["clause_id"] == "equity_65"]
        assert (c["outcome"], c["reason_codes"]) == ("UNKNOWN", [code])
        assert ev["decision"] == "UNKNOWN"


def test_no_runtime_loop_calls_the_compiler_so_runtime_call_path_stays_absent():
    from tests.opportunity_intelligence.test_p1b_runtime_caller_absent import RUNTIME_CANDIDATES

    hits = []
    for rel in RUNTIME_CANDIDATES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for token in ("recipe_compiler", "evaluate_canonical_recipes", "compile_canonical_recipes"):
            if token in text:
                hits.append(f"{rel}:{token}")
    assert hits == []


def test_a_lowering_that_changes_the_decision_is_refused(compiled, monkeypatch):
    from recipe_engine.opportunity_intelligence import recipe_compiler

    real = recipe_compiler.evaluate_eligibility

    class Flipped:
        def __init__(self, receipt):
            self._body = receipt.to_dict()
            self._body["decision"] = "NO_MATCH" if self._body["decision"] != "NO_MATCH" else "MATCH"

        def to_dict(self):
            return self._body

    monkeypatch.setattr(recipe_compiler, "evaluate_eligibility", lambda ctx, pol: Flipped(real(ctx, pol)))
    with pytest.raises(RecipeCompilerError, match="the lowering changed the predicate"):
        run(base_property(), compiled)
