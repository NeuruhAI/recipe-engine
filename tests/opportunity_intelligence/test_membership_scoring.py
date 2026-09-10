"""Court: per-membership score vector and rank(opportunity, recipe, strategy).

The ranker's existing receipts are the proof.  The canonical strategy must
reproduce ``scripts/rank_nc_curative_raw.py`` factor for factor on every input
combination, and ranking the primary memberships alone must reproduce the
ranker's ``rank`` / ``recipe_rank`` / ``recipe_membership_count`` /
``ranking_universe_id`` whatever order the memberships arrive in.
"""

from __future__ import annotations

import copy
import csv
import itertools
import json
import random
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from recipe_engine.opportunity_intelligence import membership_scoring as ms
from recipe_engine.opportunity_intelligence.oppack_receipts import DEFAULT_RANKING_VERSION, _parse_contributions
from recipe_engine.opportunity_intelligence.membership_scoring import (
    CANONICAL_STRATEGY_ID,
    ReceiptDriftError,
    SCORE_DIMENSIONS,
    ScoringError,
    build_strategy,
    load_strategy,
    membership_projection_row,
    rank,
    score_membership,
    to_score_vector,
    validate_membership_score_receipt,
)
from scripts import rank_nc_curative_raw as base
from scripts import rank_nc_curative_raw_v3 as v3


REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def canonical():
    return load_strategy(CANONICAL_STRATEGY_ID)


def _row(**overrides):
    row = {
        "county": "Union",
        "county_fips": "37179",
        "parcel_apn": "02.239.077",
        "owner_name": "UNKNOWN HEIRS OF FREDDIE SOWELL",
        "source_types": "taxdelinquent",
        "tax_foreclosure_indicator": "",
        "heir_indicator": "True",
        "fragmented_ownership_indicator": "False",
        "out_of_state": "",
        "years_owned": "",
        "assessed_value": "156600",
        "tax_balance": "1200",
        "assessed_value_less_tax": "155400",
        "tax_years": "2023|2024",
        "equity_status": "UNKNOWN_NEEDS_DEBT_SEARCH",
        "source_url": "https://example.test/tax",
    }
    row.update(overrides)
    return row


def _component(receipt, name):
    return next(c for c in receipt["score_vector"]["components"] if c["name"] == name)


# ---------------------------------------------------------------- strategy


def test_canonical_strategy_is_pinned_and_mirrors_the_ranker(canonical):
    assert canonical.strategy_id == CANONICAL_STRATEGY_ID == DEFAULT_RANKING_VERSION
    assert canonical.cap == 100
    assert not canonical.recipe_conditioned
    for score, expected in [(100, "A"), (85, "A"), (84, "B"), (70, "B"), (55, "C"), (35, "D"), (34, "E"), (0, "E")]:
        assert canonical.tier(score) == base.tier(score) == expected


def test_a_drifted_strategy_document_is_refused(tmp_path, monkeypatch):
    document = json.loads((ms.STRATEGY_DIR / f"{CANONICAL_STRATEGY_ID}.json").read_text())
    document["factor_deltas"]["heir_or_estate_signal"] = 21
    (tmp_path / f"{CANONICAL_STRATEGY_ID}.json").write_text(json.dumps(document))
    monkeypatch.setattr(ms, "STRATEGY_DIR", tmp_path)
    with pytest.raises(ScoringError):
        load_strategy(CANONICAL_STRATEGY_ID)
    with pytest.raises(ScoringError, match="registered to a different document"):
        build_strategy(document)
    with pytest.raises(ScoringError, match="not registered"):
        load_strategy("nc_curative_rank.v99")


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["factor_deltas"].pop("multi_trigger_3_plus"), "exactly the canonical factors"),
        (lambda d: d["factor_deltas"].update({"invented_signal": 5}), "exactly the canonical factors"),
        (lambda d: d["factor_deltas"].update({"heir_or_estate_signal": 2.5}), "integer"),
        (lambda d: d["recipe_factor_deltas"].update({"heir_property_acquisition_research": {"made_up": 1}}), "not a canonical factor"),
        (lambda d: d["tiers"].pop(), "floorless"),
        (lambda d: d["tie_break"].insert(0, "parcel_apn_asc"), "lead with composite_desc"),
        (lambda d: d["tie_break"].append("owner_name_asc"), "distinct members"),
        (lambda d: d.update({"weights_learned_from": "vibes"}), "unknown"),
    ],
)
def test_a_strategy_cannot_invent_a_fact_or_a_float(mutate, message):
    document = json.loads((ms.STRATEGY_DIR / f"{CANONICAL_STRATEGY_ID}.json").read_text())
    document["strategy_id"] = "review.fixture"
    mutate(document)
    with pytest.raises(ScoringError, match=message):
        build_strategy(document)


# ------------------------------------------------ factor parity with the ranker

_FLAG_VALUES = ("True", "False", "")
_EXHAUSTIVE = list(itertools.product(
    ("taxdelinquent", "tax_foreclosure_case|taxdelinquent", "tax_foreclosure_case", ""),
    _FLAG_VALUES,  # tax_foreclosure_indicator
    _FLAG_VALUES,  # heir_indicator
    _FLAG_VALUES,  # fragmented_ownership_indicator
    _FLAG_VALUES,  # out_of_state
    ("", "5", "12", "25"),  # years_owned
    ("", "50000", "90000", "160000", "300000"),  # assessed_value
    ("", "500", "2000", "6000", "12000"),  # tax_balance
))


def _combo_row(combo, **extra):
    source_types, foreclosure, heir, fragmented, out_state, years, assessed, tax = combo
    return _row(
        source_types=source_types, tax_foreclosure_indicator=foreclosure, heir_indicator=heir,
        fragmented_ownership_indicator=fragmented, out_of_state=out_state, years_owned=years,
        assessed_value=assessed, tax_balance=tax, **extra,
    )


def test_canonical_factors_equal_base_score_on_every_input_combination(canonical):
    """32,400 combinations: fired factors, deltas, cap and trigger count equal the ranker's."""
    for combo in _EXHAUSTIVE:
        row = _combo_row(combo)
        score, reasons = base.base_score(row)
        records = ms.evaluate_factors(ms.read_inputs(row))
        fired = [r["factor_id"] for r in records if r["state"] == ms.FIRED]
        contributions = [{"factor_id": f, "delta": canonical.delta(f, "any")} for f in fired]
        assert contributions == _parse_contributions("|".join(reasons)), combo
        assert min(sum(c["delta"] for c in contributions), canonical.cap) == score, combo
        multi = next(r for r in records if r["factor_id"] == "multi_trigger_3_plus")
        assert multi["inputs"]["triggers_fired"] == sorted(base.triggers(row)), combo


def test_full_receipts_equal_base_score_on_a_sample(canonical):
    for combo in random.Random(11).sample(_EXHAUSTIVE, 600):
        row = _combo_row(combo)
        score, reasons = base.base_score(row)
        scored = score_membership(row, base.recipe(row)[0], strategy=canonical)
        assert scored.composite == score, combo
        assert [f"{c['factor_id']}:+{c['delta']}" for c in scored.receipt["composite"]["component_contributions"]] == reasons
        assert scored.trigger_count == len(base.triggers(row))
        assert scored.receipt["composite"]["tier"] == base.tier(score)
        validate_membership_score_receipt(rank([scored]).receipts[0])


def test_trigger_families_match_the_v3_annotation():
    assert dict(ms.TRIGGER_FAMILY) == dict(v3.TRIGGER_FAMILY)


# ------------------------------------------------------ UNKNOWN is not FALSE


def test_blank_foreclosure_flag_is_unknown_not_false(canonical):
    unknown = score_membership(_row(tax_foreclosure_indicator=""), "heir_property_acquisition_research", strategy=canonical)
    false = score_membership(_row(tax_foreclosure_indicator="False"), "heir_property_acquisition_research", strategy=canonical)
    # Same points: the ranker never scored an unknown.  Different truth.
    assert unknown.composite == false.composite
    assert "active_tax_foreclosure" in unknown.receipt["composite"]["undetermined_factors"]
    assert "active_tax_foreclosure" not in false.receipt["composite"]["undetermined_factors"]
    assert "tax_foreclosure_indicator" in unknown.receipt["counter_evidence"]["unknown_inputs"]
    negatives = {n["field"] for n in false.receipt["counter_evidence"]["negative_evidence"]}
    assert "tax_foreclosure_indicator" in negatives
    assert {n["field"] for n in unknown.receipt["counter_evidence"]["negative_evidence"]} == {"fragmented_ownership_indicator"}
    assert all(n["meaning"] == "NOT_OBSERVED_IN_SOURCE_NOT_A_LEGAL_FACT"
               for n in false.receipt["counter_evidence"]["negative_evidence"])


def test_a_dimension_with_no_known_input_has_no_value(canonical):
    scored = score_membership(_row(assessed_value="", tax_balance=""), "heir_property_acquisition_research", strategy=canonical)
    economic = _component(scored.receipt, "economic_value")
    assert economic["evaluation_state"] == "UNKNOWN"
    assert economic["value"] is None
    known_zero = score_membership(_row(assessed_value="10", tax_balance="10"), "heir_property_acquisition_research", strategy=canonical)
    assert _component(known_zero.receipt, "economic_value")["evaluation_state"] == "EVALUATED"
    assert _component(known_zero.receipt, "economic_value")["value"] == 0


def test_multi_trigger_is_undetermined_when_unknowns_could_reach_three(canonical):
    # tax_distress + heir fired, out_of_state / tenure unknown -> could reach 3
    scored = score_membership(_row(assessed_value="1000"), "heir_property_acquisition_research", strategy=canonical)
    assert "multi_trigger_3_plus" in scored.receipt["composite"]["undetermined_factors"]
    decided = score_membership(
        _row(assessed_value="1000", out_of_state="False", years_owned="3", tax_foreclosure_indicator="False"),
        "heir_property_acquisition_research", strategy=canonical,
    )
    assert "multi_trigger_3_plus" not in decided.receipt["composite"]["undetermined_factors"]


def test_a_cross_dimension_factor_keeps_its_dimension_evaluated(canonical):
    # every signal_strength input unknown, but multi_trigger fires on tax + court + value
    row = _row(heir_indicator="", fragmented_ownership_indicator="", out_of_state="", years_owned="",
               source_types="tax_foreclosure_case|taxdelinquent", assessed_value="200000")
    scored = score_membership(row, "tax_foreclosure_curative", strategy=canonical)
    signal = _component(scored.receipt, "signal_strength")
    assert signal["evaluation_state"] == "EVALUATED"
    assert signal["value"] == 10
    validate_membership_score_receipt(rank([scored]).receipts[0])


def test_the_score_vector_is_the_census_vector_and_never_invents_contactability(canonical):
    receipt = rank([score_membership(_row(), "heir_property_acquisition_research", strategy=canonical)]).receipts[0]
    with (REPO / "RECIPE_SCORE_VECTOR_COVERAGE.csv").open(newline="", encoding="utf-8") as fh:
        census = [(r["dimension"], r["owner"]) for r in csv.DictReader(fh)]
    assert [(c["name"], c["coverage_owner"]) for c in receipt["score_vector"]["components"]] == census == list(SCORE_DIMENSIONS)
    for name in ("contactability", "liquidity", "evidence_confidence"):
        assert _component(receipt, name)["evaluation_state"] == "UNWIRED"
        assert _component(receipt, name)["value"] is None
    text = json.dumps(receipt)
    for forbidden in ('"phone"', '"contact_authorized"', '"outreach"'):
        assert forbidden not in text


def test_economics_are_proxies_and_say_so(canonical):
    receipt = score_membership(_row(assessed_value="60000", potential_equity_proxy="UNKNOWN"),
                               "heir_property_acquisition_research", strategy=canonical).receipt
    economics = receipt["economics"]
    assert economics["basis"] == "PROXY_NOT_VERIFIED_EQUITY"
    assert economics["verified_equity"] == {"state": "UNKNOWN", "value": None, "equity_status": "UNKNOWN_NEEDS_DEBT_SEARCH"}
    assert economics["assessed_value"] == {"state": "KNOWN", "value": 60000}
    assert economics["tax_years"] == {"state": "KNOWN", "value": ["2023", "2024"]}
    assert "potential_equity_proxy_note" in economics


def test_evidence_carries_inputs_and_source_family(canonical):
    receipt = score_membership(_row(), "heir_property_acquisition_research", strategy=canonical).receipt
    by_factor = {e["factor_id"]: e for e in receipt["evidence"]}
    assert by_factor["heir_or_estate_signal"]["inputs"] == {"heir_indicator": {"state": "KNOWN", "value": True}}
    assert by_factor["heir_or_estate_signal"]["source_families"] == ["assessor_parcel_roll"]
    assert by_factor["published_tax_distress"]["source_families"] == ["county_tax_collector_publication"]
    assert sum(e["delta"] for e in receipt["evidence"]) == receipt["composite"]["uncapped"]


# ------------------------------------------------ received receipts are proof


def test_received_receipt_drift_is_refused(canonical):
    row = _row()
    score, reasons = base.base_score(row)
    good = dict(row, curative_priority_score=str(score), score_reasons="|".join(reasons),
                trigger_count=str(len(base.triggers(row))), priority_tier=base.tier(score))
    assert score_membership(good, "heir_property_acquisition_research", strategy=canonical).receipt["received"]["parity"] == "EQUAL"
    for field, value in [("curative_priority_score", str(score + 1)), ("score_reasons", "heir_or_estate_signal:+20"),
                         ("trigger_count", "9"), ("priority_tier", "A")]:
        with pytest.raises(ReceiptDriftError):
            score_membership(dict(good, **{field: value}), "heir_property_acquisition_research", strategy=canonical)


def test_a_why_matched_receipt_for_another_recipe_is_refused(canonical):
    row = _row()
    other = json.dumps({"recipe_id": "tax_foreclosure_curative", "input_hash": "x"})
    with pytest.raises(ScoringError, match="not 'heir_property_acquisition_research'"):
        score_membership(row, "heir_property_acquisition_research", strategy=canonical, why_matched_receipt=other)


# -------------------------------------------------------------- rank


def _ranker_rows(tmp: Path):
    rows = []
    rng = random.Random(20260910)
    owners = ("SMITH JOHN", "JONES MARY & JONES BOB", "ESTATE OF DOE JANE", "ACME HOLDINGS LLC", "BROWN ET AL")
    for i, combo in enumerate(rng.sample(_EXHAUSTIVE, 400)):
        assessed, tax = combo[6], combo[7]
        rows.append(_combo_row(
            combo, parcel_apn=f"APN-{i:04d}", county_fips=("37179", "37119")[i % 2], owner_name=owners[i % len(owners)],
            assessed_value_less_tax=("" if not assessed else str(float(assessed) - float(tax or 0))),
        ))
    path = tmp / "input.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    v3.run(path, tmp / "out")
    with (tmp / "out" / "NC_CURATIVE_ALL.csv").open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def test_primary_only_rank_reproduces_the_ranker_receipts_in_any_order(canonical):
    with TemporaryDirectory() as td:
        ranked = _ranker_rows(Path(td))
    scored = [
        score_membership(r, r["recipe_id"], strategy=canonical, is_primary=True, match_rank=1,
                         why_matched_receipt=r["why_matched_receipt"])
        for r in ranked
    ]
    assert {s.receipt["received"]["parity"] for s in scored} == {"EQUAL"}
    for seed in (1, 2, 3):
        shuffled = scored[:]
        random.Random(seed).shuffle(shuffled)
        universe = rank(shuffled, ranking_run_id="ranking_run_fixture")
        assert universe.ranking_universe_id == ranked[0]["ranking_universe_id"]
        for r in ranked:
            got = universe.get(f"{r['county_fips']}|{r['parcel_apn']}", r["recipe_id"])["rank"]
            assert (got["global_rank"], got["recipe_rank"], got["recipe_membership_count"]) == (
                int(r["rank"]), int(r["recipe_rank"]), int(r["recipe_membership_count"])
            )


def test_secondary_memberships_are_scored_and_ranked_within_their_recipe(canonical):
    heir_and_frag = _row(parcel_apn="A", heir_indicator="True", fragmented_ownership_indicator="True")
    frag_only = _row(parcel_apn="B", heir_indicator="False", fragmented_ownership_indicator="True", assessed_value="400000")
    memberships = [
        score_membership(heir_and_frag, "heir_property_acquisition_research", strategy=canonical, is_primary=True, match_rank=1),
        score_membership(heir_and_frag, "fractional_family_ownership", strategy=canonical, is_primary=False, match_rank=2),
        score_membership(frag_only, "fractional_family_ownership", strategy=canonical, is_primary=True, match_rank=1),
    ]
    universe = rank(memberships)
    primary = universe.get("37179|A", "heir_property_acquisition_research")
    secondary = universe.get("37179|A", "fractional_family_ownership")
    other = universe.get("37179|B", "fractional_family_ownership")
    assert primary["composite"]["value"] == secondary["composite"]["value"]  # recipe-unconditioned
    assert primary["rank"]["global_rank"] == secondary["rank"]["global_rank"]
    assert secondary["recipe"]["membership"] == "SECONDARY"
    assert secondary["rank"]["recipe_membership_count"] == other["rank"]["recipe_membership_count"] == 2
    assert {secondary["rank"]["recipe_rank"], other["rank"]["recipe_rank"]} == {1, 2}
    for receipt in universe.receipts:
        validate_membership_score_receipt(receipt)
    assert universe.get("37179|B", "heir_property_acquisition_research") is None


def test_a_recipe_conditioned_strategy_changes_rank_within_a_recipe_only_by_policy():
    document = json.loads((ms.STRATEGY_DIR / f"{CANONICAL_STRATEGY_ID}.json").read_text())
    document["strategy_id"] = "review.fractional_emphasis.fixture"
    document["recipe_factor_deltas"] = {"fractional_family_ownership": {"ownership_fragmentation_proxy": 30}}
    review = build_strategy(document)
    row = _row(fragmented_ownership_indicator="True")
    as_heir = score_membership(row, "heir_property_acquisition_research", strategy=review)
    as_frag = score_membership(row, "fractional_family_ownership", strategy=review)
    assert as_frag.composite - as_heir.composite == 22
    assert as_frag.receipt["received"]["parity"] == "NOT_APPLICABLE"
    assert as_frag.receipt["strategy"]["recipe_conditioned"] is True


def test_rank_refuses_mixed_strategies_duplicates_and_empty(canonical):
    document = json.loads((ms.STRATEGY_DIR / f"{CANONICAL_STRATEGY_ID}.json").read_text())
    document["strategy_id"] = "review.fixture"
    document["cap"] = 90
    review = build_strategy(document)
    a = score_membership(_row(), "heir_property_acquisition_research", strategy=canonical)
    with pytest.raises(ScoringError, match="exactly one strategy"):
        rank([a, score_membership(_row(parcel_apn="Z"), "heir_property_acquisition_research", strategy=review)])
    with pytest.raises(ScoringError, match="duplicate"):
        rank([a, copy.deepcopy(a)])
    with pytest.raises(ScoringError, match="empty"):
        rank([])


def test_receipts_are_deterministic_and_tamper_evident(canonical):
    first = rank([score_membership(_row(), "heir_property_acquisition_research", strategy=canonical)]).receipts[0]
    second = rank([score_membership(_row(), "heir_property_acquisition_research", strategy=canonical)]).receipts[0]
    assert first == second
    tampered = copy.deepcopy(first)
    tampered["composite"]["value"] += 1
    with pytest.raises(ScoringError, match="content_sha256"):
        validate_membership_score_receipt(tampered)
    inflated = copy.deepcopy(first)
    _component(inflated, "liquidity")["value"] = 0
    from recipe_engine.opportunity_intelligence.eligibility import content_hash
    inflated["content_sha256"] = content_hash(inflated)
    with pytest.raises(ScoringError, match="not a low score"):
        validate_membership_score_receipt(inflated)


# -------------------------------------------------------- consumer shapes


def test_score_vector_projection_satisfies_the_consumer_contract(canonical):
    receipt = rank([score_membership(_row(), "heir_property_acquisition_research", strategy=canonical)]).receipts[0]
    vector = to_score_vector(receipt, tenant_id="tenant-fixture", universe_id="universe-fixture")
    schema = json.loads(
        (REPO / "tests" / "opportunity_intelligence" / "fixtures" / "lead_recipe_os_score_vector.v1.schema.json").read_text()
    )
    assert set(vector) == set(schema["required"])
    allowed = set(schema["properties"]["components"]["items"]["properties"]["evaluation_state"]["enum"])
    for component in vector["components"]:
        assert component["evaluation_state"] in allowed
        assert set(schema["properties"]["components"]["items"]["required"]) <= set(component)
        if component["evaluation_state"] == "EVALUATED":
            assert isinstance(component["value"], float) and len(component["receipt_sha256"]) == 64
        else:
            assert component["value"] is None


def test_projection_row_is_the_membership_table_shape(canonical):
    receipt = rank([score_membership(_row(property_key="NC:37179:02.239.077"), "heir_property_acquisition_research",
                                     strategy=canonical, is_primary=True, match_rank=1)]).receipts[0]
    row = membership_projection_row(receipt, why_matched_receipt="{}")
    assert list(row) == list(ms.MEMBERSHIP_PROJECTION_COLUMNS)
    assert row["scoring_version"] == CANONICAL_STRATEGY_ID
    assert row["recipe_score"] == receipt["composite"]["value"]
    assert row["recipe_rank"] == 1
    assert row["is_primary"] == "true"
    assert "tax_foreclosure_indicator" in row["unknown_inputs"].split("|")
