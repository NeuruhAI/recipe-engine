"""Court: the opportunity eligibility runtime (T11) -- producer -> runtime -> DeedSonar consumer.

Locks the three laws the runtime exists to keep:

* UNKNOWN != zero        -- a blank fact is no observation; the recipe stays UNKNOWN, is counted
                            as UNKNOWN, never becomes NO_MATCH, a 0 or a membership row.
* rank != eligibility    -- ranked outputs and rank/score/primary-recipe fields are refused;
                            decisions do not move when non-evidence columns move.
* recipe membership != result-set membership -- a membership is exactly one MATCH decision,
                            keyed by the Lead Recipe OS membership_key, with its own receipt.

The end-to-end DB court at the bottom applies DeedSonar's own migration to a scratch LOCAL
database and drives the CLI through forecast -> apply -> supersession -> lineage refusal. It runs
when RECIPE_ENGINE_LOCAL_PG_URL (a loopback Postgres) and DEEDSONAR_ELIGIBILITY_MIGRATION (path to
deedsonar supabase/migrations/20260915140000_candidate_recipe_membership_eligibility_v1.sql) are set.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from recipe_engine.opportunity_intelligence import eligibility_runtime as rt
from recipe_engine.opportunity_intelligence.eligibility import (
    consumer_canonical_json,
    content_hash,
    membership_key,
)
from recipe_engine.opportunity_intelligence.recipe_compiler import compile_canonical_recipes

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import run_opportunity_eligibility as runtime  # noqa: E402

TENANT = "1840ac90-54bd-4b3d-8baf-227c8d84ab5a"
AS_OF = "2026-09-14T23:50:09Z"
COLUMNS = list(runtime.REQUIRED_COLUMNS) + [
    "owner_name_2", "court_case_numbers", "parcel_match_status", "stage", "contact_authorized",
    "candidate_id", "land_value", "improvement_value", "years_owned", "last_sale_date",
]

TRACER = "NC:37179:09222012"
BLANKS = "NC:37119:0001"
INSTITUTIONAL = "NC:37119:0002"
FORECLOSURE_ONLY = "NC:37063:0003"
AMPERSAND = "NC:37183:0004"


def _row(key: str, **values: str) -> dict[str, str]:
    _, fips, apn = key.split(":")
    row = {c: "" for c in COLUMNS}
    row.update(county_fips=fips, parcel_apn=apn, property_key=key, source_row_id=key,
               source_url=f"https://county.example/{fips}/delinquent.pdf")
    row.update(values)
    return row


def fixture_rows() -> list[dict[str, str]]:
    return [
        # the production tracer (7421053a): heirs + fractional + tax foreclosure + delinquent
        _row(TRACER, owner_name="CHAMBERS EDNA MAE HEIRS", source_types="tax_foreclosure_case|taxdelinquent",
             heir_indicator="True", fragmented_ownership_indicator="True", tax_foreclosure_indicator="True",
             out_of_state="False", owner_mailing_state="NC", assessed_value="262100.0", tax_balance="4351.71",
             tax_years="2022|2023|2024|2025", facts_as_of="2026-09-02", court_case_numbers="25CV001262-890",
             land_value="0.0", improvement_value="0.0", last_sale_date="1/1/1900 12:00:00 AM"),
        # an 08-27 row: the fragmentation, foreclosure and mailing facts were never collected
        _row(BLANKS, owner_name="SMITH JOHN", source_types="taxdelinquent", heir_indicator="False",
             tax_balance="812.5"),
        # upstream flagged heirs on an institutional owner; strict_v2 corrects it
        _row(INSTITUTIONAL, owner_name="PIEDMONT REAL ESTATE LLC", source_types="taxdelinquent",
             heir_indicator="True", fragmented_ownership_indicator="False", tax_foreclosure_indicator="False",
             out_of_state="False", owner_mailing_state="NC", assessed_value="98000", tax_balance="1200"),
        # on the docket, not on a delinquency list; assessed value is a 0.0 placeholder
        _row(FORECLOSURE_ONLY, owner_name="JONES MARY", source_types="tax_foreclosure_case", heir_indicator="False",
             fragmented_ownership_indicator="False", tax_foreclosure_indicator="True", out_of_state="True",
             owner_mailing_state="SC", assessed_value="0.0", facts_as_of="2026-09-10"),
        # fragmentation stated nowhere, but the owner text names two owners
        _row(AMPERSAND, owner_name="DOE JANE & DOE JOHN", source_types="taxdelinquent", heir_indicator="False",
             tax_balance="300"),
    ]


def write_csv(path: Path, rows: list[dict[str, str]], columns: list[str] = COLUMNS) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate_cli(tmp: Path, input_csv: Path, out: str = "run", **extra: str) -> tuple[int, Path]:
    argv = ["evaluate", "--input", str(input_csv), "--tenant-id", TENANT, "--as-of", AS_OF,
            "--output-dir", str(tmp / out)]
    for key, value in extra.items():
        argv += [f"--{key.replace('_', '-')}", value]
    return runtime.main(argv), tmp / out


def decisions(run_dir: Path) -> dict[str, dict[str, list[str]]]:
    lines = (run_dir / runtime.DECISIONS_FILE).read_text(encoding="utf-8").splitlines()
    return {json.loads(line)["property_key"]: json.loads(line)["decisions"] for line in lines}


def memberships(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (run_dir / runtime.MEMBERSHIPS_FILE).read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def compiled():
    return rt.validated_compiled_set(compile_canonical_recipes())


@pytest.fixture()
def fixture_run(tmp_path):
    input_csv = write_csv(tmp_path / "input.csv", fixture_rows())
    code, run_dir = evaluate_cli(tmp_path, input_csv)
    assert code == 0
    return tmp_path, input_csv, run_dir


def _input_for(key: str, rows=None) -> dict:
    row = next(r for r in (rows or fixture_rows()) if r["property_key"] == key)
    return runtime.estate_row_evaluation_input(row, tenant_id=TENANT, universe_id=runtime.DEFAULT_UNIVERSE_ID,
                                               as_of=AS_OF, input_sha256="a" * 64)


# --------------------------------------------------------------------------
# the crosswalk is the spec's, not ours
# --------------------------------------------------------------------------

def test_crosswalk_maps_only_ingredients_the_spec_names_a_producer_for_on_this_input(compiled):
    ingredients = compiled["ingredients"]
    for entry in runtime.NC_ESTATE_CROSSWALK:
        info = ingredients[entry["ingredient"]]
        assert entry["producer_token"] in info["producer"], entry["ingredient"]
        assert info["source_state"] not in ("SOURCE_REQUIRED", "WALLED", "PROPOSED", "MISSING"), entry["ingredient"]
        expected = "signal" if info["value_kind"] == "BOOLEAN" else "measurement"
        assert entry["channel"] == expected, entry["ingredient"]
    mapped = {e["ingredient"] for e in runtime.NC_ESTATE_CROSSWALK}
    produced = set()
    for key in (TRACER, BLANKS, INSTITUTIONAL, FORECLOSURE_ONLY, AMPERSAND):
        body = _input_for(key)
        produced |= {s["signal_type"] for s in body["context"]["signals"]}
        produced |= {m["ingredient"] for m in body["measurements"]}
    assert produced <= mapped, produced - mapped
    # the placeholder-prone and no-producer facts are never read
    assert not mapped & {"transfer.tenure_years", "valuation.land_value", "valuation.improvement_value",
                         "equity.estimated_pct", "estate.open", "probate.opened", "ownership.government"}


# --------------------------------------------------------------------------
# UNKNOWN != zero
# --------------------------------------------------------------------------

def test_a_blank_fact_is_no_observation_and_the_recipe_stays_unknown(fixture_run):
    _, _, run_dir = fixture_run
    blanks = decisions(run_dir)[BLANKS]
    assert blanks["tax_threshold_watch"][0] == "MATCH"
    assert blanks["title_curative"][0] == "UNKNOWN"          # fragmentation never collected
    assert blanks["stalled_tax_foreclosure_heirship"][0] == "UNKNOWN"
    body = _input_for(BLANKS)
    assert "title.fractional" not in {s["signal_type"] for s in body["context"]["signals"]}
    assert "absentee.out_of_state" not in {s["signal_type"] for s in body["context"]["signals"]}
    assert not [m for m in body["measurements"] if m["ingredient"] == "valuation.assessed_value"]


def test_no_decision_is_no_match_without_decisive_evidence_and_the_census_counts_unknown(fixture_run):
    _, _, run_dir = fixture_run
    receipt = json.loads((run_dir / runtime.RUN_RECEIPT_FILE).read_text())
    census = receipt["census"]
    assert census["decisions_total"]["NO_MATCH"] == 0
    assert census["decisions_total"]["UNKNOWN"] > 0
    for recipe_id, counts in census["by_recipe"].items():
        assert counts["MATCH"] + counts["NO_MATCH"] + counts["UNKNOWN"] == census["properties"], recipe_id
    assert census["memberships_projected"] == census["decisions_total"]["MATCH"]


def test_an_explicit_false_is_negative_evidence_not_the_opposite_fact(compiled):
    result = rt.evaluate_property(_input_for(BLANKS), compiled, property_key=BLANKS, source_row_id=BLANKS,
                                  run_id="eligibility_run_20260914_aaaaaaaaaaaa_00000000",
                                  input_sha256="a" * 64, validity_seconds=86400)
    assert result["decisions"]["decisions"]["heir_curative"][0] == "UNKNOWN"
    evaluation = rt.evaluate_canonical_recipes(_input_for(BLANKS), compiled)
    heir = next(e for e in evaluation["evaluations"] if e["recipe_id"] == "heir_curative")
    clause = next(c for c in heir["clauses"] if c["clause_id"] == "heirship")
    assert clause["outcome"] == "CONTRADICTED"
    assert "CONTRADICTION_NOT_DECISIVE" in clause["reason_codes"]


def test_a_placeholder_valuation_is_unknown_not_zero():
    body = _input_for(FORECLOSURE_ONLY)
    assert not [m for m in body["measurements"] if m["ingredient"] == "valuation.assessed_value"]
    tracer = _input_for(TRACER)
    [assessed] = [m for m in tracer["measurements"] if m["ingredient"] == "valuation.assessed_value"]
    assert assessed["value"] == 262100.0 and assessed["basis"] == "ESTIMATED"


def test_not_on_a_delinquency_list_is_not_not_delinquent(fixture_run):
    _, _, run_dir = fixture_run
    assert decisions(run_dir)[FORECLOSURE_ONLY]["tax_threshold_watch"][0] == "UNKNOWN"


# --------------------------------------------------------------------------
# rank != eligibility
# --------------------------------------------------------------------------

def test_a_ranked_output_is_refused_as_input(tmp_path):
    ranked_cols = COLUMNS + ["rank", "recipe_id", "curative_priority_score"]
    rows = [{**r, "rank": "1", "recipe_id": "stalled_tax_foreclosure_heirship", "curative_priority_score": "85"}
            for r in fixture_rows()]
    ranked = write_csv(tmp_path / "NC_CURATIVE_ALL.csv", rows, ranked_cols)
    code, _ = evaluate_cli(tmp_path, ranked)
    assert code == 2
    with pytest.raises(runtime.RuntimeRefusal, match="rank is not eligibility"):
        runtime.estate_row_evaluation_input({**fixture_rows()[0], "primary_recipe": "heir_curative"},
                                            tenant_id=TENANT, universe_id="u", as_of=AS_OF, input_sha256="a" * 64)


def test_non_evidence_columns_never_move_a_decision(compiled):
    rows = fixture_rows()
    moved = [{**r, "stage": "LEAD", "contact_authorized": "True", "candidate_id": str(uuid.uuid4()),
              "land_value": "999999", "years_owned": "126", "last_sale_date": "1/1/1900"} for r in rows]
    for key in (TRACER, BLANKS, AMPERSAND):
        a, b = _input_for(key, rows), _input_for(key, moved)
        assert content_hash(a, omit=()) == content_hash(b, omit=()), key


def test_recipe_order_and_row_order_do_not_move_a_decision(tmp_path):
    rows = fixture_rows()
    shuffled = rows[:]
    random.Random(7).shuffle(shuffled)
    _, a = evaluate_cli(tmp_path, write_csv(tmp_path / "a.csv", rows), out="a")
    _, b = evaluate_cli(tmp_path, write_csv(tmp_path / "b.csv", shuffled), out="b")
    assert decisions(a).keys() == decisions(b).keys()
    for key in decisions(a):
        assert {r: v[:2] for r, v in decisions(a)[key].items()} == {r: v[:2] for r, v in decisions(b)[key].items()}


# --------------------------------------------------------------------------
# recipe membership != result-set membership
# --------------------------------------------------------------------------

def test_a_membership_is_exactly_one_match_with_its_own_receipt(fixture_run):
    _, _, run_dir = fixture_run
    by_property = decisions(run_dir)
    rows = memberships(run_dir)
    assert {(m["property_key"], m["recipe_id"]) for m in rows} == {
        (k, r) for k, d in by_property.items() for r, v in d.items() if v[0] == "MATCH"}
    for m in rows:
        e = m["explanation"]
        assert m["membership_class"] == "ELIGIBLE"
        assert e["decision"] == "MATCH" and e["recipe_id"] == m["recipe_id"]
        assert e["opportunity_id"] == m["property_key"]
        assert m["membership_key"] == membership_key(TENANT, runtime.DEFAULT_UNIVERSE_ID, m["property_key"],
                                                     m["recipe_id"])
        assert e["evaluation_sha256"] == by_property[m["property_key"]][m["recipe_id"]][2]
        assert content_hash(e) == e["content_sha256"]
        assert not rt.FORBIDDEN_PROJECTION_KEYS & set(e)
    assert {m["recipe_id"] for m in rows if m["property_key"] == TRACER} == {
        "tax_threshold_watch", "stalled_tax_foreclosure_heirship", "heir_curative", "title_curative"}


def test_owner_text_is_the_ranker_classifier_without_its_collapse(fixture_run):
    _, _, run_dir = fixture_run
    by_property = decisions(run_dir)
    assert by_property[INSTITUTIONAL]["heir_curative"][0] == "UNKNOWN"   # institutional heirs flag corrected
    assert by_property[AMPERSAND]["title_curative"][0] == "MATCH"         # co-owners named in the owner text


def test_an_unresolved_disqualifier_is_never_listed_as_checked(fixture_run):
    _, _, run_dir = fixture_run
    for m in memberships(run_dir):
        e = m["explanation"]
        assert "not_government_owned" not in e["disqualifiers_checked"]
        assert "not_government_owned" in e["disqualifiers_unresolved"]
        assert "ownership.government" in e["unknown_inputs"]
        assert e["source_ids"], "a MATCH cites the source families behind it"


def test_observations_keep_every_decision_observable(fixture_run, compiled):
    _, _, run_dir = fixture_run
    line = json.loads((run_dir / runtime.DECISIONS_FILE).read_text().splitlines()[0])
    observed = rt.observations(line, compiled)
    assert len(observed) == 29
    assert {o["decision"] for o in observed} <= {"MATCH", "NO_MATCH", "UNKNOWN"}
    assert all(o["production_write"] is False and o["contact_authorized"] is False for o in observed)


def test_the_consumer_payload_carries_canonical_explanation_bytes(fixture_run):
    _, _, run_dir = fixture_run
    rows = rt.deedsonar_membership_rows(memberships(run_dir))
    for row, m in zip(rows, memberships(run_dir)):
        assert row["explanation"] == consumer_canonical_json(m["explanation"])
    with pytest.raises(rt.EligibilityRuntimeError, match="only MATCH"):
        bad = dict(memberships(run_dir)[0])
        bad["explanation"] = {**bad["explanation"], "decision": "UNKNOWN"}
        rt.deedsonar_membership_rows([bad])


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------

def test_the_run_receipt_binds_input_outputs_code_schema_time_and_parent(tmp_path):
    input_csv = write_csv(tmp_path / "input.csv", fixture_rows())
    input_sha = sha(input_csv)
    parent = tmp_path / "INPUT_RECEIPT.json"
    parent.write_text(json.dumps({"contract": "t6.full-universe-ranking-input.v1", "output": {"sha256": input_sha}}))
    run_id = f"ranking_run_20260914_{input_sha[:12]}"
    code, run_dir = evaluate_cli(tmp_path, input_csv, parent_receipt=str(parent), ranking_run_id=run_id)
    assert code == 0
    receipt = rt.validate_run_receipt(json.loads((run_dir / runtime.RUN_RECEIPT_FILE).read_text()))
    assert receipt["input"]["sha256"] == input_sha
    assert receipt["input"]["ranking_run_id"] == run_id
    assert receipt["parent_receipt"]["sha256"] == sha(parent)
    for block in receipt["outputs"].values():
        assert block["sha256"] == sha(run_dir / block["path"])
    assert len(receipt["code"]["commit"]) == 40
    assert receipt["pipeline"]["version"] == rt.PIPELINE_VERSION
    assert receipt["schema_versions"]["membership"] == rt.MEMBERSHIP_CONTRACT
    assert receipt["as_of"] == AS_OF and receipt["generated_at"].endswith("Z")
    assert receipt["valid_until"] == "2026-09-21T23:50:09Z"
    assert receipt["run_id"].startswith(f"eligibility_run_20260914_{input_sha[:12]}_")
    # replay: same input, same code -> byte-identical outputs
    code, again = evaluate_cli(tmp_path, input_csv, out="again", parent_receipt=str(parent), ranking_run_id=run_id)
    for name in (runtime.DECISIONS_FILE, runtime.MEMBERSHIPS_FILE):
        assert sha(run_dir / name) == sha(again / name)


def test_a_ranking_run_that_did_not_consume_the_input_is_refused(tmp_path):
    input_csv = write_csv(tmp_path / "input.csv", fixture_rows())
    code, _ = evaluate_cli(tmp_path, input_csv, ranking_run_id="ranking_run_20260914_98c6e952f36d")
    assert code == 2
    parent = tmp_path / "p.json"
    parent.write_text(json.dumps({"output": {"sha256": "0" * 64}}))
    code, _ = evaluate_cli(tmp_path, input_csv, parent_receipt=str(parent))
    assert code == 2


def test_a_tampered_run_receipt_is_refused(fixture_run):
    _, _, run_dir = fixture_run
    receipt = json.loads((run_dir / runtime.RUN_RECEIPT_FILE).read_text())
    receipt["census"]["decisions_total"]["UNKNOWN"] = 0
    with pytest.raises(rt.EligibilityRuntimeError, match="digest mismatch"):
        rt.validate_run_receipt(receipt)


# --------------------------------------------------------------------------
# end to end: producer -> runtime -> DeedSonar consumer (local database only)
# --------------------------------------------------------------------------

PG_URL = os.environ.get("RECIPE_ENGINE_LOCAL_PG_URL", "")
MIGRATION = os.environ.get("DEEDSONAR_ELIGIBILITY_MIGRATION", "")
needs_db = pytest.mark.skipif(not (PG_URL and MIGRATION), reason="set RECIPE_ENGINE_LOCAL_PG_URL and "
                              "DEEDSONAR_ELIGIBILITY_MIGRATION for the cross-repo consumer court")

SCHEMA = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role; end if;
end $$;
create table public.lead_import_batches (batch_id uuid primary key, tenant_id uuid not null, import_kind text not null,
  is_test boolean not null default false, is_synthetic boolean not null default false,
  source_context jsonb not null default '{}'::jsonb, created_at timestamptz not null default now());
create table public.lead_import_rows (row_id uuid primary key, batch_id uuid not null references public.lead_import_batches,
  tenant_id uuid not null, source_row_id text, primary_recipe text, global_rank integer, recipe_engine jsonb,
  source_as_of timestamptz);
create table public.candidate_recipe_definitions (recipe_id text primary key, display_name text not null,
  owner_condition text not null default 'ANY', scoring_version text not null default '',
  description text not null default '', is_active boolean not null default true, created_at timestamptz not null default now());
create table public.candidate_recipe_membership (tenant_id uuid not null,
  import_row_id uuid not null references public.lead_import_rows on delete cascade,
  recipe_id text not null references public.candidate_recipe_definitions (recipe_id), recipe_version text not null default '',
  scoring_version text not null default '', membership_class text not null, recipe_rank integer, recipe_score numeric,
  source_receipt_id text not null default '', explanation text not null default '', observed_at timestamptz,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  primary key (tenant_id, import_row_id, recipe_id, recipe_version, scoring_version),
  check (membership_class in ('PRIMARY_PROJECTION','ELIGIBLE','PROXY','HELD')));
"""


def _db(tmp_path):
    import psycopg
    admin = psycopg.connect(PG_URL, autocommit=True)
    name = f"t11_eligibility_{uuid.uuid4().hex[:10]}"
    admin.execute(f"create database {name}")
    url = PG_URL.rsplit("/", 1)[0] + f"/{name}"
    return admin, name, url


def _cli(args: list[str], url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DEEDSONAR_DATABASE_URL": url, "PYTHONPATH": str(ROOT)}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "run_opportunity_eligibility.py"), *args,
                           "--env-file", "/nonexistent", "--allow-unmerged-code"],
                          env=env, capture_output=True, text=True, cwd=ROOT)


@needs_db
def test_end_to_end_runtime_to_deedsonar_membership(tmp_path):
    import psycopg
    admin, name, url = _db(tmp_path)
    try:
        rows = fixture_rows()
        input_csv = write_csv(tmp_path / "input.csv", rows)
        fingerprint = sha(input_csv)[:12]
        batch = uuid.uuid4()
        row_ids = {r["property_key"]: uuid.uuid4() for r in rows}
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(SCHEMA)
            conn.execute("insert into public.lead_import_batches (batch_id, tenant_id, import_kind, source_context) "
                         "values (%s, %s, 'data_estate', %s)",
                         (batch, TENANT, json.dumps({"dataset": "CURRENT_CANONICAL", "current": True,
                                                     "authoritative": True})))
            conn.execute("insert into public.candidate_recipe_definitions (recipe_id, display_name) "
                         "values ('published_tax_distress', 'Published Tax Distress')")
            for rank, r in enumerate(rows, 1):
                bag = {"property_key": r["property_key"], "ranking_run_id": f"ranking_run_20260914_{fingerprint}"}
                conn.execute("insert into public.lead_import_rows values (%s, %s, %s, %s, 'published_tax_distress', "
                             "%s, %s, '2026-09-02')", (row_ids[r["property_key"]], batch, TENANT, r["property_key"],
                                                      rank, json.dumps(bag)))
            conn.execute("insert into public.candidate_recipe_membership (tenant_id, import_row_id, recipe_id, "
                         "recipe_version, membership_class, recipe_rank, source_receipt_id) values "
                         "(%s, %s, 'published_tax_distress', 'nc_curative_recipes.v1', 'PRIMARY_PROJECTION', 1, "
                         "'ranking_run_x')", (TENANT, row_ids[TRACER]))
            conn.execute(Path(MIGRATION).read_text(encoding="utf-8"))

        code, run_dir = evaluate_cli(tmp_path, input_csv, ranking_run_id=f"ranking_run_20260914_{fingerprint}")
        assert code == 0
        run_id = json.loads((run_dir / runtime.RUN_RECEIPT_FILE).read_text())["run_id"]
        expected = len(memberships(run_dir))

        forecast = _cli(["apply", "--run-dir", str(run_dir), "--mode", "forecast"], url)
        assert forecast.returncode == 0, forecast.stderr
        with psycopg.connect(url) as conn:
            assert conn.execute("select count(*) from public.candidate_recipe_membership "
                                "where membership_class='ELIGIBLE'").fetchone()[0] == 0

        refused = _cli(["apply", "--run-dir", str(run_dir), "--mode", "apply"], url)
        assert refused.returncode == 2 and "confirm-run-id" in refused.stderr

        one = _cli(["apply", "--run-dir", str(run_dir), "--mode", "apply", "--confirm-run-id", run_id,
                    "--property-key", TRACER], url)
        assert one.returncode == 0, one.stderr
        with psycopg.connect(url) as conn:
            got = conn.execute("select recipe_id, membership_class, recipe_rank, recipe_score, source_receipt_id, "
                               "explanation from public.candidate_recipe_membership where import_row_id=%s "
                               "and recipe_version like '%%@compiled.%%' order by recipe_id",
                               (row_ids[TRACER],)).fetchall()
            assert [g[0] for g in got] == ["heir_curative", "stalled_tax_foreclosure_heirship", "tax_threshold_watch",
                                           "title_curative"]
            assert all(g[1] == "ELIGIBLE" and g[2] is None and g[3] is None for g in got)
            by_recipe = {m["recipe_id"]: m for m in memberships(run_dir) if m["property_key"] == TRACER}
            for g in got:
                assert g[4] == by_recipe[g[0]]["evaluation_sha256"]
                assert g[5] == consumer_canonical_json(by_recipe[g[0]]["explanation"])
                parsed = json.loads(g[5])   # what DeedSonar's reader (parseWhyMatched) reads
                assert parsed["source_ids"] and parsed["signals_triggered"]
            primary = conn.execute("select membership_class, recipe_rank from public.candidate_recipe_membership "
                                   "where recipe_version='nc_curative_recipes.v1'").fetchone()
            assert primary == ("PRIMARY_PROJECTION", 1)

        full = _cli(["apply", "--run-dir", str(run_dir), "--mode", "apply", "--confirm-run-id", run_id], url)
        assert full.returncode == 0, full.stderr
        again = _cli(["apply", "--run-dir", str(run_dir), "--mode", "apply", "--confirm-run-id", run_id], url)
        assert again.returncode == 0
        with psycopg.connect(url) as conn:
            count = conn.execute("select count(*) from public.candidate_recipe_membership "
                                 "where recipe_version like '%%@compiled.%%'").fetchone()[0]
            assert count == expected
            stamp = conn.execute("select source_context->'recipe_eligibility' from public.lead_import_batches").fetchone()[0]
            assert stamp["run_id"] == run_id and stamp["census"]["decisions_total"]["NO_MATCH"] == 0

        # the estate re-ranks on a newer input where the tracer's fragmentation is no longer stated
        newer = [dict(r) for r in rows]
        tracer = next(r for r in newer if r["property_key"] == TRACER)
        tracer.update(fragmented_ownership_indicator="", heir_indicator="False", owner_name="CHAMBERS EDNA MAE")
        newer_csv = write_csv(tmp_path / "newer.csv", newer)
        new_fp = sha(newer_csv)[:12]
        code, newer_dir = evaluate_cli(tmp_path, newer_csv, out="newer",
                                       ranking_run_id=f"ranking_run_20260915_{new_fp}")
        assert code == 0
        new_run_id = json.loads((newer_dir / runtime.RUN_RECEIPT_FILE).read_text())["run_id"]
        stale = _cli(["apply", "--run-dir", str(newer_dir), "--mode", "forecast"], url)
        assert stale.returncode != 0 and "ranked on another input" in stale.stderr   # estate not yet re-ranked
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("update public.lead_import_rows set recipe_engine = jsonb_set(recipe_engine, "
                         "'{ranking_run_id}', to_jsonb(%s::text))", (f"ranking_run_20260915_{new_fp}",))
        old = _cli(["apply", "--run-dir", str(run_dir), "--mode", "forecast"], url)
        assert old.returncode != 0 and "ranked on another input" in old.stderr     # lineage lock
        moved = _cli(["apply", "--run-dir", str(newer_dir), "--mode", "apply", "--confirm-run-id", new_run_id], url)
        assert moved.returncode == 0, moved.stderr
        with psycopg.connect(url) as conn:
            now = {r[0] for r in conn.execute(
                "select recipe_id from public.candidate_recipe_membership where import_row_id=%s "
                "and recipe_version like '%%@compiled.%%'", (row_ids[TRACER],)).fetchall()}
            # heirs + title went UNKNOWN on the newer facts: retired, not preserved
            assert now == {"tax_threshold_watch"}
    finally:
        admin.execute(f"drop database if exists {name} with (force)")
        admin.close()
