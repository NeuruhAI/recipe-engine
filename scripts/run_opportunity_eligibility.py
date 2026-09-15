#!/usr/bin/env python3
"""The opportunity eligibility runtime: the caller P1B sealed as absent.

    Recipe Engine eligibility producer
      -> THIS runtime invocation (the production ranking input, every property x every compiled recipe)
      -> Lead Recipe OS membership contract (membership_key, MATCH-only positive, observations)
      -> DeedSonar candidate_recipe_membership_eligibility_apply_v1 (ELIGIBLE, unranked, stale MATCH retired)

Three steps, each a receipt:

    # 1. evaluate (files only): the exact input the production ranking run consumed
    python scripts/run_opportunity_eligibility.py evaluate \\
        --input <FULL_UNIVERSE_RANKING_INPUT.csv> --parent-receipt <FULL_UNIVERSE_INPUT_RECEIPT.json> \\
        --ranking-run-id ranking_run_20260914_98c6e952f36d \\
        --tenant-id <uuid> --as-of 2026-09-14T23:50:09Z --output-dir <run dir>

    # 2. forecast: the consumer computes its plan inside a READ ONLY transaction
    python scripts/run_opportunity_eligibility.py apply --run-dir <run dir> --mode forecast

    # 3. activate: one property first, then the run
    python scripts/run_opportunity_eligibility.py apply --run-dir <run dir> --mode apply \\
        --confirm-run-id <run_id> [--property-key NC:37179:09222012]

The source contract is the NC curative estate ranker input (``t6.full-universe-ranking-input.v1``).
Only the ingredients the canonical recipe spec names a producer for on this input are mapped
(``NC_ESTATE_CROSSWALK``). A blank cell is no observation (UNKNOWN), never False; an owner-text
classification is the ranker's own ``strict_v2`` classifier without its blank->False collapse.
Ranked outputs (rank, score, tier, primary recipe) are refused as input: rank is not eligibility.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipe_engine.opportunity_intelligence import eligibility_runtime as rt  # noqa: E402
from recipe_engine.opportunity_intelligence.eligibility import (  # noqa: E402
    ELIGIBILITY_CONTEXT_CONTRACT,
    EligibilityError,
    content_hash,
    parse_timestamp,
    render_timestamp,
)
from recipe_engine.opportunity_intelligence.recipe_compiler import (  # noqa: E402
    RECIPE_EVALUATION_INPUT_CONTRACT,
    compile_canonical_recipes,
)
from scripts.rank_nc_curative_raw_v2 import strict_fragmented, strict_heir  # noqa: E402

SOURCE_CONTRACT = "t6.full-universe-ranking-input.v1"
#: The same 50-column ranker input under the two names its producers stamp: the T6 lane's
#: full-universe assembler (v1) and the landos-engine estate producer's universe step (v2).
#: The column court (REQUIRED_COLUMNS, RANKED_OUTPUT_COLUMNS) is what admits a file; the
#: parent receipt is what names it.
SOURCE_CONTRACTS = (SOURCE_CONTRACT, "landos.full-universe-ranking-input.v2")
CROSSWALK_VERSION = "neuruh.recipe-engine.nc-estate-eligibility-crosswalk.v1"
DEFAULT_UNIVERSE_ID = "deedsonar.nc-curative-estate"
DEFAULT_VALIDITY_SECONDS = 7 * 24 * 3600  # the estate's weekly source cadence (nc-weekly)
DEEDSONAR_FUNCTION = "public.candidate_recipe_membership_eligibility_apply_v1"

DECISIONS_FILE = "PROPERTY_DECISIONS.jsonl"
MEMBERSHIPS_FILE = "ELIGIBILITY_MEMBERSHIPS.jsonl"
RUN_RECEIPT_FILE = "ELIGIBILITY_RUN_RECEIPT.json"

FAMILY_TAX = "county_tax_collector_publication"
FAMILY_DOCKET = "county_foreclosure_docket_publication"
FAMILY_ROLL = "assessor_parcel_roll"

#: Columns a ranked output carries.  An eligibility run refuses to start from one:
#: rank, score, tier and primary recipe are consequences of a ranking, not evidence.
RANKED_OUTPUT_COLUMNS = frozenset({
    "rank", "global_rank", "recipe_rank", "recipe_id", "primary_recipe", "curative_priority_score",
    "priority_tier", "score", "recipe_membership_count", "why_matched_receipt", "why_ranked_receipt",
    "component_contributions", "ranking_universe_id", "research_lane", "trigger_classes",
})
REQUIRED_COLUMNS = (
    "county_fips", "parcel_apn", "property_key", "source_row_id", "owner_name", "source_types",
    "heir_indicator", "fragmented_ownership_indicator", "tax_foreclosure_indicator", "out_of_state",
    "owner_mailing_state", "assessed_value", "tax_balance", "tax_years", "facts_as_of", "source_url",
)

#: The declared crosswalk.  Each ingredient is mapped ONLY because the canonical
#: recipe spec names this input's column as its producer (``producer_token`` must
#: appear in the spec's ``producer`` text -- the court checks it).  Every other
#: ingredient stays UNKNOWN on this source: owner_condition, probate filings,
#: code/vacancy tags, deed tenure, land/improvement split, equity and liens have
#: no producer on this input.
NC_ESTATE_CROSSWALK: tuple[dict[str, str], ...] = (
    {"ingredient": "tax.delinquent", "channel": "signal", "columns": "source_types",
     "rule": "PRESENT when source_types lists taxdelinquent; otherwise no observation",
     "producer_token": "taxdelinquent", "family": FAMILY_TAX, "confidence": "HIGH"},
    {"ingredient": "tax.foreclosed", "channel": "signal",
     "columns": "tax_foreclosure_indicator|source_types|court_case_numbers",
     "rule": "PRESENT when the indicator is true or source_types lists tax_foreclosure_case; "
             "ABSENT when the indicator is explicitly false; blank -> no observation",
     "producer_token": "tax_foreclosure_case", "family": FAMILY_DOCKET, "confidence": "HIGH"},
    {"ingredient": "heirship.unresolved", "channel": "signal", "columns": "owner_name|heir_indicator",
     "rule": "PRESENT when strict_v2 classifies heirs; ABSENT when it does not and heir_indicator is "
             "stated; blank heir_indicator without owner-text markers -> no observation",
     "producer_token": "heir_indicator", "family": FAMILY_ROLL, "confidence": "MEDIUM"},
    {"ingredient": "title.fractional", "channel": "signal",
     "columns": "owner_name|fragmented_ownership_indicator",
     "rule": "PRESENT when strict_v2 classifies fragmentation; ABSENT when it does not and the "
             "indicator is stated; blank indicator without owner-text markers -> no observation",
     "producer_token": "fragmented_ownership_indicator", "family": FAMILY_ROLL, "confidence": "MEDIUM"},
    {"ingredient": "absentee.out_of_state", "channel": "signal", "columns": "out_of_state|owner_mailing_state",
     "rule": "PRESENT/ABSENT from out_of_state only when a mailing state is on the row; otherwise no observation",
     "producer_token": "out_of_state_owner_flag", "family": FAMILY_ROLL, "confidence": "HIGH"},
    {"ingredient": "valuation.assessed_value", "channel": "measurement", "columns": "assessed_value",
     "rule": "ESTIMATED USD when positive; blank or <= 0 is a placeholder, not a valuation -> no measurement",
     "producer_token": "assessed_value", "family": FAMILY_ROLL, "confidence": "HIGH"},
    {"ingredient": "tax.delinquent_amount", "channel": "measurement", "columns": "tax_balance",
     "rule": "VERIFIED USD when stated; blank -> no measurement",
     "producer_token": "tax_delinquent_amount", "family": FAMILY_TAX, "confidence": "HIGH"},
    {"ingredient": "tax.delinquent_years", "channel": "measurement", "columns": "tax_years",
     "rule": "VERIFIED count of the distinct years listed; blank -> no measurement",
     "producer_token": "tax_years_due_count", "family": FAMILY_TAX, "confidence": "HIGH"},
)

_TRUE = {"1", "true", "yes", "y", "t"}
_FALSE = {"0", "false", "no", "n", "f"}
_YEAR = re.compile(r"^\d{4}$")
_RUN_ID = re.compile(r"^ranking_run_\d{8}_([0-9a-f]{12})$")


class RuntimeRefusal(RuntimeError):
    """The runtime refuses to evaluate, forecast or apply as asked."""


# --------------------------------------------------------------------------
# small pure helpers
# --------------------------------------------------------------------------

def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _flag(value: Any) -> bool | None:
    """True / False / None.  None is UNKNOWN: a blank or unrecognized cell is not False."""
    text = _text(value).lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def _number(value: Any) -> float | None:
    text = _text(value)
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _source_types(row: dict[str, Any]) -> set[str]:
    return {part.strip() for part in _text(row.get("source_types")).split("|") if part.strip()}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return render_timestamp(datetime.now(timezone.utc).replace(microsecond=0))


# --------------------------------------------------------------------------
# the live eligibility-context producer (NC curative estate input)
# --------------------------------------------------------------------------

def estate_row_evaluation_input(
    row: dict[str, Any],
    *,
    tenant_id: str,
    universe_id: str,
    as_of: str,
    input_sha256: str,
) -> dict[str, Any]:
    """One ranker-input row -> ``recipe-evaluation-input.v1``.

    Reads only the crosswalk columns.  Never reads rank, score, tier, recipe,
    stage or contact columns, and refuses a row that carries a ranked-output
    column."""
    leaked = sorted(RANKED_OUTPUT_COLUMNS & {k for k, v in row.items() if _text(v)})
    if leaked:
        raise RuntimeRefusal(f"row carries ranked-output columns {leaked}: rank is not eligibility")
    property_key = _text(row.get("property_key"))
    fips, apn = _text(row.get("county_fips")), _text(row.get("parcel_apn"))
    if not property_key or not fips or not apn:
        raise RuntimeRefusal("row has no property_key / county_fips / parcel_apn")
    if property_key != f"NC:{fips}:{apn}":
        raise RuntimeRefusal(f"{property_key}: property_key does not bind county_fips and parcel_apn")
    source_row_id = _text(row.get("source_row_id")) or property_key
    as_of_ts = parse_timestamp(as_of, "as_of")

    facts_date = _text(row.get("facts_as_of"))
    if facts_date:
        observed = parse_timestamp(f"{facts_date[:10]}T00:00:00Z", "facts_as_of")
        basis = "facts_as_of"
    else:
        observed, basis = as_of_ts, "run_as_of (the source states no observation date)"
    if observed > as_of_ts:
        raise RuntimeRefusal(f"{property_key}: facts_as_of {facts_date} is after as_of {as_of}")
    observed_at = render_timestamp(observed)

    types = _source_types(row)
    owner = _text(row.get("owner_name"))
    evidence: list[dict[str, Any]] = []
    families: dict[str, str] = {}

    def add_evidence(family: str, source_id: str, facts: dict[str, Any]) -> str:
        evidence_id = f"{family}:{property_key}"
        evidence.append({
            "evidence_id": evidence_id,
            "source_id": source_id,
            "source_family": family,
            "content_sha256": content_hash({"property_key": property_key, "family": family, "facts": facts}, omit=()),
            "observed_at": observed_at,
            "ledger_state": "BOUND",
            "ledger_ref": f"sha256:{input_sha256}#{source_row_id}#{family}",
        })
        families[family] = evidence_id
        return evidence_id

    tax_facts = {"taxdelinquent_listed": "taxdelinquent" in types, "tax_balance": _text(row.get("tax_balance")),
                 "tax_years": _text(row.get("tax_years")), "source_url": _text(row.get("source_url"))}
    if tax_facts["taxdelinquent_listed"] or tax_facts["tax_balance"] or tax_facts["tax_years"]:
        add_evidence(FAMILY_TAX, tax_facts["source_url"] or f"{FAMILY_TAX}:{fips}", tax_facts)
    docket_facts = {"tax_foreclosure_indicator": _text(row.get("tax_foreclosure_indicator")),
                    "tax_foreclosure_case_listed": "tax_foreclosure_case" in types,
                    "court_case_numbers": _text(row.get("court_case_numbers"))}
    if docket_facts["tax_foreclosure_indicator"] or docket_facts["tax_foreclosure_case_listed"] \
            or docket_facts["court_case_numbers"]:
        add_evidence(FAMILY_DOCKET, f"{FAMILY_DOCKET}:{fips}", docket_facts)
    roll_facts = {"owner_name": owner, "owner_name_2": _text(row.get("owner_name_2")),
                  "owner_mailing_state": _text(row.get("owner_mailing_state")),
                  "out_of_state": _text(row.get("out_of_state")), "heir_indicator": _text(row.get("heir_indicator")),
                  "fragmented_ownership_indicator": _text(row.get("fragmented_ownership_indicator")),
                  "assessed_value": _text(row.get("assessed_value")),
                  "parcel_match_status": _text(row.get("parcel_match_status"))}
    if owner or roll_facts["assessed_value"]:
        add_evidence(FAMILY_ROLL, f"{FAMILY_ROLL}:{fips}", roll_facts)

    signals: list[dict[str, Any]] = []
    measurements: list[dict[str, Any]] = []

    def signal(ingredient: str, state: str, confidence: str, family: str, why: str) -> None:
        signals.append({
            "signal_id": f"{ingredient}:{property_key}",
            "signal_type": ingredient,
            "state": state,
            "confidence": confidence,
            "evidence_refs": [families[family]],
            "source_families": [family],
            "observed_at": observed_at,
            "note": f"{why}; observed_at basis: {basis}",
        })

    def measure(ingredient: str, value: Any, unit: str, basis_: str, family: str) -> None:
        measurements.append({
            "measurement_id": f"{ingredient}:{property_key}",
            "ingredient": ingredient,
            "value": value,
            "unit": unit,
            "basis": basis_,
            "confidence": "HIGH",
            "evidence_refs": [families[family]],
            "observed_at": observed_at,
            "note": f"observed_at basis: {basis}",
        })

    if "taxdelinquent" in types:
        signal("tax.delinquent", "PRESENT", "HIGH", FAMILY_TAX, "listed on the county delinquency publication")

    foreclosure_flag = _flag(row.get("tax_foreclosure_indicator"))
    if foreclosure_flag is True or "tax_foreclosure_case" in types:
        signal("tax.foreclosed", "PRESENT", "HIGH", FAMILY_DOCKET, "tax foreclosure case on the docket publication")
    elif foreclosure_flag is False:
        signal("tax.foreclosed", "ABSENT", "MEDIUM", FAMILY_DOCKET,
               "docket source states no tax foreclosure case (negative evidence, not the opposite fact)")

    if FAMILY_ROLL in families:
        heir_stated = _flag(row.get("heir_indicator"))
        if strict_heir(owner, row.get("heir_indicator")):
            signal("heirship.unresolved", "PRESENT", "MEDIUM", FAMILY_ROLL, "strict_v2 owner-text heir classification")
        elif heir_stated is not None:
            signal("heirship.unresolved", "ABSENT", "MEDIUM", FAMILY_ROLL,
                   "no heir marker in owner text and the source states heir_indicator (negative evidence)")
        frag_stated = _flag(row.get("fragmented_ownership_indicator"))
        if strict_fragmented(owner, row.get("fragmented_ownership_indicator")):
            signal("title.fractional", "PRESENT", "MEDIUM", FAMILY_ROLL,
                   "strict_v2 owner-text fragmentation classification")
        elif frag_stated is not None:
            signal("title.fractional", "ABSENT", "MEDIUM", FAMILY_ROLL,
                   "no co-tenancy marker in owner text and the source states the indicator (negative evidence)")
        mailing = _text(row.get("owner_mailing_state"))
        absentee = _flag(row.get("out_of_state"))
        if mailing and absentee is not None:
            signal("absentee.out_of_state", "PRESENT" if absentee else "ABSENT", "HIGH", FAMILY_ROLL,
                   f"owner mailing state {mailing} vs situs NC")
        assessed = _number(row.get("assessed_value"))
        if assessed is not None and assessed > 0:
            measure("valuation.assessed_value", assessed, "USD", "ESTIMATED", FAMILY_ROLL)

    if FAMILY_TAX in families:
        balance = _number(row.get("tax_balance"))
        if balance is not None and balance >= 0:
            measure("tax.delinquent_amount", balance, "USD", "VERIFIED", FAMILY_TAX)
        years = sorted({y.strip() for y in _text(row.get("tax_years")).split("|") if _YEAR.match(y.strip())})
        if years:
            measure("tax.delinquent_years", len(years), "years", "VERIFIED", FAMILY_TAX)

    return {
        "schema_version": RECIPE_EVALUATION_INPUT_CONTRACT,
        "context": {
            "schema_version": ELIGIBILITY_CONTEXT_CONTRACT,
            "mode": "LIVE",
            "tenant_id": tenant_id,
            "universe_id": universe_id,
            "opportunity_id": property_key,
            "asset_id": f"parcel:{fips}:{apn}",
            "as_of": render_timestamp(as_of_ts),
            "evidence": evidence,
            "signals": signals,
        },
        "measurements": measurements,
    }


# --------------------------------------------------------------------------
# code provenance
# --------------------------------------------------------------------------

def code_provenance(root: Path = ROOT) -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                              text=True).stdout.strip()
    commit = git("rev-parse", "HEAD")
    # Untracked files count: an uncommitted module would otherwise run under a
    # commit that does not contain it.
    dirty = git("status", "--porcelain")
    try:
        subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, "origin/main"],
                       check=True, capture_output=True)
        on_main = True
    except subprocess.CalledProcessError:
        on_main = False
    return {"repo": "NeuruhAI/recipe-engine", "commit": commit, "tree_clean": not dirty,
            "on_origin_main": on_main}


# --------------------------------------------------------------------------
# evaluate
# --------------------------------------------------------------------------

def load_input(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        rows = list(reader)
    ranked = sorted(RANKED_OUTPUT_COLUMNS & set(header))
    if ranked:
        raise RuntimeRefusal(f"{path.name} is a ranked output (columns {ranked}); evaluate the ranking INPUT")
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise RuntimeRefusal(f"{path.name} is not a {SOURCE_CONTRACT} file: missing {missing}")
    keys = [_text(r.get("property_key")) for r in rows]
    if len(set(keys)) != len(keys):
        raise RuntimeRefusal(f"{path.name}: duplicate property_key")
    return rows, header


def _parent_block(parent: Path | None, input_sha: str) -> dict[str, Any] | None:
    """The receipt that produced --input. It must declare this input's sha256.

    Two shapes are courted: the T6 full-universe input receipt (``output.sha256``) and the
    landos-engine estate producer receipt (``universe.output.sha256``). A parent that names
    no input, names another input, or names an uncourted contract is refused: a receipt
    that does not bind the input is not its parent."""
    if parent is None:
        return None
    body = json.loads(parent.read_text(encoding="utf-8"))
    universe = body.get("universe") if isinstance(body.get("universe"), dict) else {}
    if (body.get("output") or {}).get("sha256"):
        kind, declared, contract = "full_universe_input_receipt", body["output"]["sha256"], body.get("contract")
    elif (universe.get("output") or {}).get("sha256"):
        kind, declared, contract = "estate_producer_receipt", universe["output"]["sha256"], universe.get("contract")
    else:
        raise RuntimeRefusal(f"parent receipt {parent.name} declares no input sha256")
    if declared != input_sha:
        raise RuntimeRefusal(f"parent receipt declares input {declared} != input sha256 {input_sha}")
    if contract not in SOURCE_CONTRACTS:
        raise RuntimeRefusal(f"parent receipt names source contract {contract!r}; courted: {list(SOURCE_CONTRACTS)}")
    block = {"path": str(parent), "sha256": sha256_file(parent), "kind": kind, "contract": contract,
             "declares_input_sha256": declared}
    for key in ("producer_run_id", "producer_version"):
        if body.get(key):
            block[key] = body[key]
    return block


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input).resolve()
    rows, _header = load_input(input_path)
    rows_in_file = len(rows)
    input_sha = sha256_file(input_path)
    ranking_run_id = args.ranking_run_id
    if ranking_run_id:
        match = _RUN_ID.match(ranking_run_id)
        if not match or match.group(1) != input_sha[:12]:
            raise RuntimeRefusal(f"ranking run {ranking_run_id} did not consume this input (sha256 {input_sha[:12]}...)")
    parent = _parent_block(Path(args.parent_receipt).resolve() if args.parent_receipt else None, input_sha)
    as_of = render_timestamp(parse_timestamp(args.as_of, "--as-of"))
    selected = set(args.property_key or [])
    if selected:
        unknown = selected - {_text(r["property_key"]) for r in rows}
        if unknown:
            raise RuntimeRefusal(f"--property-key not in the input: {sorted(unknown)}")
        rows = [r for r in rows if _text(r["property_key"]) in selected]
    rows.sort(key=lambda r: _text(r["property_key"]))

    compiled = rt.validated_compiled_set(compile_canonical_recipes())
    config = {
        "crosswalk_version": CROSSWALK_VERSION,
        "crosswalk": list(NC_ESTATE_CROSSWALK),
        "tenant_id": args.tenant_id,
        "universe_id": args.universe_id,
        "validity_seconds": args.validity_seconds,
        "compiled_set_digest": compiled["set_digest"],
        "scope": sorted(selected) if selected else "FULL_INPUT",
    }
    config_sha = content_hash(config, omit=())
    run_id = rt.run_id_for(as_of=as_of, input_sha256=input_sha, config_sha256=config_sha)
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    census = rt.EligibilityCensus([r["recipe_id"] for r in compiled["recipes"] if r["kind"] != "META"])

    decisions_path, memberships_path = out / DECISIONS_FILE, out / MEMBERSHIPS_FILE
    written = 0
    with decisions_path.open("w", encoding="utf-8") as dfh, memberships_path.open("w", encoding="utf-8") as mfh:
        for index, row in enumerate(rows, 1):
            evaluation_input = estate_row_evaluation_input(
                row, tenant_id=args.tenant_id, universe_id=args.universe_id, as_of=as_of, input_sha256=input_sha)
            result = rt.evaluate_property(
                evaluation_input, compiled, property_key=_text(row["property_key"]),
                source_row_id=_text(row.get("source_row_id")) or _text(row["property_key"]),
                run_id=run_id, input_sha256=input_sha, validity_seconds=args.validity_seconds)
            census.add(result["decisions"])
            dfh.write(json.dumps(result["decisions"], sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
            for membership in result["memberships"]:
                mfh.write(json.dumps(membership, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
                written += 1
            if args.progress and index % 2000 == 0:
                print(f"  evaluated {index}/{len(rows)}", file=sys.stderr, flush=True)

    outputs = {
        "property_decisions": {"path": DECISIONS_FILE, "sha256": sha256_file(decisions_path), "rows": len(rows)},
        "memberships": {"path": MEMBERSHIPS_FILE, "sha256": sha256_file(memberships_path), "rows": written},
    }
    receipt = rt.build_run_receipt(
        run_id=run_id,
        code=code_provenance(),
        compiled_set=compiled,
        tenant_id=args.tenant_id,
        universe_id=args.universe_id,
        as_of=as_of,
        validity_seconds=args.validity_seconds,
        generated_at=_utc_now(),
        input_block={"path": str(input_path), "sha256": input_sha, "rows_in_file": rows_in_file,
                     "properties_evaluated": len(rows),
                     "contract": parent["contract"] if parent else SOURCE_CONTRACT,
                     "contract_basis": "parent_receipt" if parent else "column_court_only",
                     "ranking_run_id": ranking_run_id, "scope": config["scope"],
                     "crosswalk_version": CROSSWALK_VERSION},
        parent_receipt=parent,
        config=config,
        outputs=outputs,
        census=census.to_dict(),
        source_contract=parent["contract"] if parent else SOURCE_CONTRACT,
    )
    (out / RUN_RECEIPT_FILE).write_text(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
    return receipt


# --------------------------------------------------------------------------
# forecast / apply (DeedSonar consumer)
# --------------------------------------------------------------------------

def load_run(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    receipt = rt.validate_run_receipt(json.loads((run_dir / RUN_RECEIPT_FILE).read_text(encoding="utf-8")))
    for name, block in receipt["outputs"].items():
        actual = sha256_file(run_dir / block["path"])
        if actual != block["sha256"]:
            raise RuntimeRefusal(f"{block['path']} sha256 {actual} != run receipt {block['sha256']} ({name})")
    decisions = [json.loads(line) for line in (run_dir / DECISIONS_FILE).read_text(encoding="utf-8").splitlines() if line]
    memberships = [json.loads(line) for line in (run_dir / MEMBERSHIPS_FILE).read_text(encoding="utf-8").splitlines() if line]
    return receipt, decisions, memberships


def load_env_file(path: Path | None) -> None:
    if path is None or not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def apply(args: argparse.Namespace) -> dict[str, Any]:
    from recipe_engine.db_authority import LOCAL, PRODUCTION, resolve_database_url

    run_dir = Path(args.run_dir).resolve()
    receipt, decisions, memberships = load_run(run_dir)
    mode = args.mode
    load_env_file(Path(args.env_file) if args.env_file else ROOT / ".env")
    authority = resolve_database_url()
    if authority.klass not in (PRODUCTION, LOCAL):
        raise RuntimeRefusal(f"database class {authority.klass} is neither production nor a local court")
    if mode == "apply":
        if args.confirm_run_id != receipt["run_id"]:
            raise RuntimeRefusal("--mode apply needs --confirm-run-id equal to the run's run_id")
        code = receipt["code"]
        committed = bool(code.get("tree_clean")) and bool(code.get("on_origin_main"))
        if authority.klass == PRODUCTION and not committed:
            raise RuntimeRefusal("production writes need a run evaluated from a clean tree at a commit on "
                                 "recipe-engine origin/main (no override)")
        if authority.klass == LOCAL and not committed and not args.allow_unmerged_code:
            raise RuntimeRefusal("uncommitted or unmerged code: pass --allow-unmerged-code for a local court")

    by_property = {d["property_key"]: d for d in decisions}
    scope = sorted(by_property)
    if args.property_key:
        missing = sorted(set(args.property_key) - set(by_property))
        if missing:
            raise RuntimeRefusal(f"--property-key not in the run: {missing}")
        scope = sorted(set(args.property_key))
    members_by_property: dict[str, list[dict[str, Any]]] = {}
    for membership in memberships:
        members_by_property.setdefault(membership["property_key"], []).append(membership)

    run_block = rt.deedsonar_run_block(receipt)
    import psycopg
    from psycopg.types.json import Jsonb

    started = _utc_now()
    results = []
    with psycopg.connect(authority.dsn, autocommit=False, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            if mode == "forecast":
                cur.execute("set transaction read only")
            cur.execute("set local statement_timeout = '600s'")
            for chunk in chunked(scope, args.chunk_size):
                rows = rt.deedsonar_membership_rows(m for key in chunk for m in members_by_property.get(key, []))
                cur.execute(
                    f"select {DEEDSONAR_FUNCTION}(%s, %s, %s, %s)",
                    (Jsonb(run_block), Jsonb(chunk), Jsonb(rows), mode == "apply"),
                )
                result = cur.fetchone()[0]
                results.append(result)
                if args.progress:
                    print(f"  {mode} chunk {len(results)}: {json.dumps(result.get('counts', result), sort_keys=True)}",
                          file=sys.stderr, flush=True)
        if mode == "apply":
            conn.commit()
        else:
            conn.rollback()

    totals: dict[str, int] = {}
    for result in results:
        for key, value in (result.get("counts") or {}).items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    body = {
        "schema_version": "neuruh.recipe-engine.opportunity-eligibility-apply.v1",
        "mode": mode.upper(),
        "run_id": receipt["run_id"],
        "parent_receipt": {"path": RUN_RECEIPT_FILE, "sha256": receipt["content_sha256"]},
        "database": {"ref": authority.ref, "class": authority.klass, "source": authority.source},
        "function": DEEDSONAR_FUNCTION,
        "scope": "PROPERTIES" if args.property_key else "RUN",
        "scope_properties": len(scope),
        "chunks": len(results),
        "totals": totals,
        "results": results,
        "started_at": started,
        "finished_at": _utc_now(),
        "committed": mode == "apply",
    }
    body["content_sha256"] = content_hash(body)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (run_dir / f"{mode.upper()}_RECEIPT_{stamp}.json").write_text(
        json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return body


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    ev = sub.add_parser("evaluate", help="evaluate every property x every compiled recipe (files only)")
    ev.add_argument("--input", required=True)
    ev.add_argument("--parent-receipt")
    ev.add_argument("--ranking-run-id", help="refuse unless this production ranking run consumed --input")
    ev.add_argument("--tenant-id", required=True)
    ev.add_argument("--universe-id", default=DEFAULT_UNIVERSE_ID)
    ev.add_argument("--as-of", required=True, help="evaluation boundary (ISO-8601, timezone aware)")
    ev.add_argument("--validity-seconds", type=int, default=DEFAULT_VALIDITY_SECONDS)
    ev.add_argument("--property-key", action="append", help="evaluate only these properties (repeatable)")
    ev.add_argument("--output-dir", required=True)
    ev.add_argument("--progress", action="store_true")
    ev.set_defaults(func=evaluate)

    ap = sub.add_parser("apply", help="forecast (read-only) or apply the run to DeedSonar")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--mode", choices=("forecast", "apply"), required=True)
    ap.add_argument("--confirm-run-id")
    ap.add_argument("--property-key", action="append", help="controlled activation: only these properties")
    ap.add_argument("--chunk-size", type=int, default=1500)
    ap.add_argument("--env-file", help="default: <repo>/.env (DEEDSONAR_DATABASE_URL)")
    ap.add_argument("--allow-unmerged-code", action="store_true", help="local court only; production never")
    ap.add_argument("--progress", action="store_true")
    ap.set_defaults(func=apply)

    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except (RuntimeRefusal, EligibilityError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    summary = {k: result[k] for k in ("run_id", "content_sha256") if k in result}
    if args.command == "evaluate":
        summary.update(census=result["census"]["decisions_total"],
                       memberships=result["census"]["memberships_projected"],
                       properties=result["census"]["properties"])
    else:
        summary.update(mode=result["mode"], totals=result["totals"], committed=result["committed"])
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
