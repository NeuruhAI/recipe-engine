#!/usr/bin/env python3
"""DeedSonar receiver projections from a ranked NC curative run.

Two artifacts, one ranked input (``NC_CURATIVE_ALL.csv`` from
``scripts/rank_nc_curative_raw_v3.py``):

1. ``DEEDSONAR_RECEIVER_PROJECTION.csv`` -- the courted 27-column single-winner
   contract.  One row per property, ``recipe_id`` is the ladder's first match.
   This is a verbatim port of the private Aug-27 materializer
   (``CURRENT_RECIPE_RANK_RUN_20260827/private/execute_current_rank.py::
   projection_court``) so the bytes it emits are reproducible from the
   repository.  Nothing about this contract changes here.

2. ``DEEDSONAR_MEMBERSHIP_PROJECTION.csv`` -- the additive membership contract.
   One row per (property_key, recipe_id) MATCH from
   ``scripts.rank_nc_curative_raw.recipe_matches`` -- the same predicates the
   ladder evaluates, without the first-match-wins collapse.  ``is_primary`` is
   true on exactly one row per property and that row is asserted equal to the
   single-winner row above.  ``why_matched_receipt`` is built by the engine's
   own ``build_why_this_matched`` for every membership; for the primary it is
   asserted byte-identical to the receipt already carried by the ranked row.

Law (fail closed):

* ``recipe_matches(row)[0] != row["recipe_id"]`` anywhere -> ``ProjectionParityError``.
* a property with zero or two primaries -> ``ProjectionParityError``.
* a recomputed primary receipt that differs from the ranked row's receipt ->
  ``ProjectionParityError``.
* nothing here scores, ranks, weights, infers a recipe from a score factor,
  contacts anyone, or writes to a database.  Files only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from recipe_engine.opportunity_intelligence import build_why_this_matched
from scripts.rank_nc_curative_raw import RECIPE_FALLTHROUGH_ID, recipe_matches

RECEIVER_PROJECTION_NAME = "DEEDSONAR_RECEIVER_PROJECTION.csv"
MEMBERSHIP_PROJECTION_NAME = "DEEDSONAR_MEMBERSHIP_PROJECTION.csv"
PROJECTION_RECEIPT_NAME = "PROJECTION_RECEIPT.json"

RECEIVER_PROJECTION_CONTRACT = "deedsonar.receiver-projection.v1"
MEMBERSHIP_PROJECTION_CONTRACT = "deedsonar.membership-projection.v1"

#: The courted 27-column receiver contract, in emitted order.  Do not edit.
RECEIVER_PROJECTION_FIELDS: tuple[str, ...] = (
    "candidate_id",
    "source_row_id",
    "property_key",
    "county_fips",
    "parcel_apn",
    "recipe_id",
    "recipe_version",
    "curative_priority_score",
    "score_name",
    "score_semantics",
    "score_reasons",
    "component_contributions",
    "priority_tier",
    "global_rank",
    "recipe_rank",
    "recipe_membership_count",
    "ranking_version",
    "ranking_universe_id",
    "why_matched_receipt",
    "why_ranked_receipt",
    "next_best_evidence_receipt",
    "source_families",
    "independent_source_family_count",
    "unknown_material_factors",
    "contradictions",
    "input_hash",
    "ranking_run_id",
)

#: The additive membership contract, in emitted order.
MEMBERSHIP_PROJECTION_FIELDS: tuple[str, ...] = (
    "property_key",
    "county_fips",
    "parcel_apn",
    "recipe_id",
    "recipe_version",
    "match_rank",
    "is_primary",
    "why_matched_receipt",
    "ranking_run_id",
    "input_hash",
)


class ProjectionParityError(RuntimeError):
    """The membership artifact would not agree with the single-winner artifact."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def identity(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("county_fips") or ""), str(row.get("parcel_apn") or ""))


def ranking_run_id(run_date: str, input_sha256: str) -> str:
    """``ranking_run_<YYYYMMDD>_<first 12 hex of the input sha256>``.

    The date is an explicit argument, never the wall clock, so a re-run on the
    same inputs with the same declared date reproduces the same bytes.
    """
    if len(run_date) != 8 or not run_date.isdigit():
        raise ValueError("run_date must be YYYYMMDD")
    if len(input_sha256) < 12:
        raise ValueError("input_sha256 must be a sha256 hex digest")
    return f"ranking_run_{run_date}_{input_sha256[:12]}"


def write_csv(path: Path, rows: list[dict[str, Any]], fields: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------
# artifact 1 -- single-winner receiver projection (verbatim port)
# --------------------------------------------------------------------------

def receiver_projection_rows(
    ranked: list[dict[str, str]],
    master: list[dict[str, str]] | None,
    *,
    run_id: str,
    input_hash: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    master_by: dict[tuple[str, str], dict[str, str]] = {}
    dup_prop: Counter[tuple[str, str]] = Counter()
    for row in master or []:
        k = (str(row.get("county_fips") or ""), str(row.get("parcel_apn") or ""))
        dup_prop[k] += 1
        master_by[k] = row
    rows_out: list[dict[str, Any]] = []
    joinable = unjoinable = cid_attached = cid_missing = srid_attached = 0
    conflicts = 0
    for row in ranked:
        k = identity(row)
        m = master_by.get(k)
        if m is None:
            unjoinable += 1
            property_key = row.get("property_key") or ""
            source_row_id = row.get("source_row_id") or ""
            candidate_id = row.get("candidate_id") or ""
        else:
            joinable += 1
            property_key = m.get("property_key") or row.get("property_key") or ""
            source_row_id = m.get("external_lead_id") or row.get("source_row_id") or ""
            candidate_id = m.get("candidate_id") or row.get("candidate_id") or ""
            if m.get("property_key") and row.get("property_key") and m.get("property_key") != row.get("property_key"):
                conflicts += 1
        if candidate_id:
            cid_attached += 1
        else:
            cid_missing += 1
        if source_row_id:
            srid_attached += 1
        rows_out.append({
            "candidate_id": candidate_id,
            "source_row_id": source_row_id,
            "property_key": property_key,
            "county_fips": row.get("county_fips"),
            "parcel_apn": row.get("parcel_apn"),
            "recipe_id": row.get("recipe_id"),
            "recipe_version": row.get("recipe_version"),
            "curative_priority_score": row.get("curative_priority_score") or row.get("score"),
            "score_name": row.get("score_name") or "curative_priority_score",
            "score_semantics": row.get("score_semantics") or "DETERMINISTIC_ADDITIVE_CURATIVE_PRIORITY",
            "score_reasons": row.get("score_reasons"),
            "component_contributions": row.get("component_contributions"),
            "priority_tier": row.get("priority_tier"),
            "global_rank": row.get("global_rank") or row.get("rank"),
            "recipe_rank": row.get("recipe_rank"),
            "recipe_membership_count": row.get("recipe_membership_count"),
            "ranking_version": row.get("ranking_version") or row.get("scoring_version"),
            "ranking_universe_id": row.get("ranking_universe_id"),
            "why_matched_receipt": row.get("why_matched_receipt"),
            "why_ranked_receipt": row.get("why_ranked_receipt"),
            "next_best_evidence_receipt": row.get("next_best_evidence_receipt"),
            "source_families": row.get("source_families"),
            "independent_source_family_count": row.get("independent_source_family_count"),
            "unknown_material_factors": row.get("unknown_material_factors"),
            "contradictions": row.get("contradictions"),
            "input_hash": row.get("input_hash") or input_hash,
            "ranking_run_id": run_id,
        })
    stats = {
        "PROJECTION_ROWS": len(rows_out),
        "PROPERTY_JOINABLE": joinable,
        "PROPERTY_UNJOINABLE": unjoinable,
        "DUPLICATE_PROPERTY_KEYS": sum(1 for n in dup_prop.values() if n > 1),
        "CANDIDATE_ID_ATTACHED": cid_attached,
        "CANDIDATE_ID_MISSING": cid_missing,
        "SOURCE_ROW_ID_ATTACHED": srid_attached,
        "IDENTITY_CONFLICTS": conflicts,
        "join_key": "county_fips+parcel_apn",
        "owner_name_join": False,
        "master_supplied": master is not None,
    }
    return rows_out, stats


# --------------------------------------------------------------------------
# artifact 2 -- membership projection (additive)
# --------------------------------------------------------------------------

def _receipt_json(receipt: dict[str, Any]) -> str:
    # Byte-compatible with scripts/rank_nc_curative_raw_v3.annotate.
    return json.dumps(receipt, sort_keys=True, separators=(",", ":"))


def membership_projection_rows(
    ranked: list[dict[str, str]],
    receiver_rows: list[dict[str, Any]],
    *,
    run_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One row per (property_key, recipe_id) match, primary first.

    ``ranked`` and ``receiver_rows`` must be the same rows in the same order
    (the receiver projection is built from ``ranked``).
    """
    if len(ranked) != len(receiver_rows):
        raise ProjectionParityError(
            f"ranked rows ({len(ranked)}) != receiver rows ({len(receiver_rows)})"
        )

    rows_out: list[dict[str, Any]] = []
    primaries: Counter[str] = Counter()
    pairs: set[tuple[str, str]] = set()
    per_property: Counter[str] = Counter()
    cohorts: Counter[str] = Counter()
    secondary_cohorts: Counter[str] = Counter()

    for row, receiver in zip(ranked, receiver_rows):
        if identity(row) != identity(receiver):
            raise ProjectionParityError(
                f"ranked/receiver order mismatch at {identity(row)} vs {identity(receiver)}"
            )
        property_key = str(receiver.get("property_key") or "")
        if not property_key:
            raise ProjectionParityError(f"missing property_key for {identity(row)}")
        primary_id = str(receiver.get("recipe_id") or "")
        matches = recipe_matches(row)
        if matches[0] != primary_id:
            raise ProjectionParityError(
                f"{property_key}: recipe_matches primary {matches[0]!r} != "
                f"single-winner recipe_id {primary_id!r}"
            )
        if RECIPE_FALLTHROUGH_ID in matches and len(matches) != 1:
            raise ProjectionParityError(
                f"{property_key}: fallthrough {RECIPE_FALLTHROUGH_ID} emitted beside a positive match"
            )
        stored_receipt = str(receiver.get("why_matched_receipt") or "")
        stored_input_hash = str(receiver.get("input_hash") or "")
        recipe_version = str(row.get("recipe_version") or "")

        for match_rank, recipe_id in enumerate(matches, 1):
            shadow = dict(row)
            shadow["recipe_id"] = recipe_id
            matched = build_why_this_matched(shadow)
            receipt_text = _receipt_json(matched)
            is_primary = match_rank == 1
            if is_primary:
                if receipt_text != stored_receipt:
                    raise ProjectionParityError(
                        f"{property_key}: recomputed primary why_matched_receipt differs from the ranked row's receipt"
                    )
                if matched["input_hash"] != stored_input_hash:
                    raise ProjectionParityError(
                        f"{property_key}: recomputed primary input_hash differs from the ranked row's input_hash"
                    )
                primaries[property_key] += 1
            pair = (property_key, recipe_id)
            if pair in pairs:
                raise ProjectionParityError(f"duplicate membership {pair}")
            pairs.add(pair)
            per_property[property_key] += 1
            cohorts[recipe_id] += 1
            if not is_primary:
                secondary_cohorts[recipe_id] += 1
            rows_out.append({
                "property_key": property_key,
                "county_fips": row.get("county_fips"),
                "parcel_apn": row.get("parcel_apn"),
                "recipe_id": recipe_id,
                "recipe_version": recipe_version,
                "match_rank": match_rank,
                "is_primary": "true" if is_primary else "false",
                "why_matched_receipt": receipt_text,
                "ranking_run_id": run_id,
                "input_hash": matched["input_hash"],
            })

    bad_primary = {k: n for k, n in primaries.items() if n != 1}
    missing_primary = [k for k in per_property if k not in primaries]
    if bad_primary or missing_primary:
        raise ProjectionParityError(
            f"primary invariant violated: {len(bad_primary)} properties with !=1 primary, "
            f"{len(missing_primary)} with none"
        )

    distribution = Counter(per_property.values())
    stats = {
        "MEMBERSHIP_ROWS": len(rows_out),
        "PROPERTIES": len(per_property),
        "PROPERTIES_MULTI": sum(1 for n in per_property.values() if n > 1),
        "MAX_MEMBERSHIPS_PER_PROPERTY": max(per_property.values()) if per_property else 0,
        "MEMBERSHIPS_PER_PROPERTY_DISTRIBUTION": {str(k): v for k, v in sorted(distribution.items())},
        "COHORT_SIZES": dict(sorted(cohorts.items())),
        "SECONDARY_COHORT_SIZES": dict(sorted(secondary_cohorts.items())),
        "PRIMARY_PARITY_WITH_RECEIVER": "ASSERTED",
        "FALLTHROUGH_ID": RECIPE_FALLTHROUGH_ID,
        "FALLTHROUGH_POLICY": "member only when no positive ladder predicate holds",
    }
    return rows_out, stats


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def project(
    *,
    ranked_csv: Path,
    input_csv: Path,
    output_dir: Path,
    run_date: str,
    master_csv: Path | None = None,
) -> dict[str, Any]:
    input_sha = sha256_file(input_csv)
    run_id = ranking_run_id(run_date, input_sha)
    ranked = load_csv(ranked_csv)
    master = load_csv(master_csv) if master_csv else None

    receiver_rows, receiver_stats = receiver_projection_rows(
        ranked, master, run_id=run_id, input_hash=input_sha
    )
    membership_rows, membership_stats = membership_projection_rows(
        ranked, receiver_rows, run_id=run_id
    )

    receiver_path = output_dir / RECEIVER_PROJECTION_NAME
    membership_path = output_dir / MEMBERSHIP_PROJECTION_NAME
    write_csv(receiver_path, receiver_rows, RECEIVER_PROJECTION_FIELDS)
    write_csv(membership_path, membership_rows, MEMBERSHIP_PROJECTION_FIELDS)

    receipt = {
        "ranking_run_id": run_id,
        "run_date": run_date,
        "input_csv": str(input_csv),
        "input_sha256": input_sha,
        "ranked_csv": str(ranked_csv),
        "ranked_sha256": sha256_file(ranked_csv),
        "master_csv": str(master_csv) if master_csv else None,
        "receiver_projection": {
            "contract": RECEIVER_PROJECTION_CONTRACT,
            "path": str(receiver_path),
            "sha256": sha256_file(receiver_path),
            "fields": list(RECEIVER_PROJECTION_FIELDS),
            **receiver_stats,
        },
        "membership_projection": {
            "contract": MEMBERSHIP_PROJECTION_CONTRACT,
            "path": str(membership_path),
            "sha256": sha256_file(membership_path),
            "fields": list(MEMBERSHIP_PROJECTION_FIELDS),
            **membership_stats,
        },
        "db_mutated": False,
        "outbound": False,
    }
    (output_dir / PROJECTION_RECEIPT_NAME).write_text(
        json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8"
    )
    return receipt


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ranked", required=True, type=Path, help="NC_CURATIVE_ALL.csv from rank_nc_curative_raw_v3")
    p.add_argument("--input", required=True, type=Path, help="the ranker input CSV (hashed into ranking_run_id)")
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--run-date", required=True, help="YYYYMMDD stamped into ranking_run_id (explicit, not wall clock)")
    p.add_argument("--master", type=Path, default=None, help="optional DeedSonar master CSV for the identity join")
    args = p.parse_args()
    receipt = project(
        ranked_csv=args.ranked,
        input_csv=args.input,
        output_dir=args.output_dir,
        run_date=args.run_date,
        master_csv=args.master,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
