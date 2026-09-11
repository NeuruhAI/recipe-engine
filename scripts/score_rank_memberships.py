#!/usr/bin/env python3
"""Score and rank every (opportunity, recipe) membership of one ranked run.

Inputs are files the ranker already emits:

* ``--ranked``      NC_CURATIVE_ALL.csv from ``rank_nc_curative_raw_v3.py``
                    (one row per property, with the ranker's receipts)
* ``--membership``  DEEDSONAR_MEMBERSHIP_PROJECTION.csv from
                    ``project_deedsonar_receiver.py`` (one row per property x
                    matched recipe).  Omitted -> the primary recipe only.

Outputs, files only (no database write, no outbound, no spend):

* ``MEMBERSHIP_SCORE_RECEIPTS.jsonl``  one ``membership-score.v1`` receipt per
  membership, in rank order
* ``MEMBERSHIP_SCORE_PROJECTION.csv``  loader rows for DeedSonar
  ``candidate_recipe_membership`` (recipe_score / recipe_rank /
  scoring_version / receipt sha); the membership class stays the loader's call
* ``MEMBERSHIP_SCORE_SUMMARY.json``    counts, universe ids and the parity court

Under the canonical strategy the run fails closed unless ranking the primary
memberships alone reproduces the ranker's ``rank``, ``recipe_rank``,
``recipe_membership_count`` and ``ranking_universe_id`` on every row.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from recipe_engine.opportunity_intelligence.membership_scoring import (
    CANONICAL_STRATEGY_ID,
    MEMBERSHIP_PROJECTION_COLUMNS,
    ScoringError,
    load_strategy,
    membership_projection_row,
    rank,
    score_membership,
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("county_fips") or "").strip(), str(row.get("parcel_apn") or "").strip()


def _memberships(ranked: list[dict[str, str]], membership_path: Path | None) -> list[dict[str, Any]]:
    """(ranked row, membership row) pairs.  Every property keeps exactly its ranked primary."""
    by_key = {_key(row): row for row in ranked}
    if len(by_key) != len(ranked):
        raise ScoringError("ranked input repeats a (county_fips, parcel_apn)")
    if membership_path is None:
        return [
            {"row": row, "recipe_id": row["recipe_id"], "is_primary": True, "match_rank": 1,
             "recipe_version": row.get("recipe_version") or None, "why_matched_receipt": row.get("why_matched_receipt") or None}
            for row in ranked
        ]
    pairs: list[dict[str, Any]] = []
    primaries: Counter[tuple[str, str]] = Counter()
    for member in _read_csv(membership_path):
        key = _key(member)
        row = by_key.get(key)
        if row is None:
            raise ScoringError(f"membership {key} has no ranked row")
        is_primary = str(member.get("is_primary") or "").strip().lower() == "true"
        if is_primary:
            primaries[key] += 1
            if member["recipe_id"] != row["recipe_id"]:
                raise ScoringError(f"{key}: primary membership {member['recipe_id']} != ranked {row['recipe_id']}")
        pairs.append({
            "row": row,
            "recipe_id": member["recipe_id"],
            "is_primary": is_primary,
            "match_rank": int(member["match_rank"]) if str(member.get("match_rank") or "").strip() else None,
            "recipe_version": member.get("recipe_version") or None,
            "why_matched_receipt": member.get("why_matched_receipt") or None,
        })
    missing = [key for key in by_key if primaries[key] != 1]
    if missing:
        raise ScoringError(f"{len(missing)} ranked properties lack exactly one primary membership, e.g. {missing[:3]}")
    return pairs


def _primary_parity(universe, ranked: list[dict[str, str]]) -> dict[str, Any]:
    mismatches = []
    for row in ranked:
        receipt = universe.get(f"{_key(row)[0]}|{_key(row)[1]}", row["recipe_id"])
        got = receipt["rank"]
        want = (int(row["rank"]), int(row["recipe_rank"]), int(row["recipe_membership_count"]))
        if (got["global_rank"], got["recipe_rank"], got["recipe_membership_count"]) != want:
            mismatches.append({"key": _key(row), "want": want})
    received_universes = {row.get("ranking_universe_id", "") for row in ranked}
    return {
        "rows": len(ranked),
        "rank_mismatches": len(mismatches),
        "first_mismatches": mismatches[:5],
        "received_ranking_universe_id": sorted(received_universes),
        "reproduced_ranking_universe_id": universe.ranking_universe_id,
        "universe_equal": received_universes == {universe.ranking_universe_id},
    }


def run(ranked_path: Path, membership_path: Path | None, output_dir: Path, *,
        strategy_id: str = CANONICAL_STRATEGY_ID, ranking_run_id: str | None = None) -> dict[str, Any]:
    strategy = load_strategy(strategy_id)
    ranked = _read_csv(ranked_path)
    pairs = _memberships(ranked, membership_path)
    if ranking_run_id is None and membership_path is not None:
        run_ids = {m.get("ranking_run_id", "") for m in _read_csv(membership_path)} - {""}
        ranking_run_id = run_ids.pop() if len(run_ids) == 1 else None

    scored = [
        score_membership(
            pair["row"], pair["recipe_id"], strategy=strategy, is_primary=pair["is_primary"],
            match_rank=pair["match_rank"], recipe_version=pair["recipe_version"],
            why_matched_receipt=pair["why_matched_receipt"],
        )
        for pair in pairs
    ]
    why_matched = {
        (s.opportunity_key, s.recipe_id): pair["why_matched_receipt"] or ""
        for s, pair in zip(scored, pairs)
    }

    parity: dict[str, Any] = {"state": "NOT_APPLICABLE", "reason": f"strategy {strategy_id} is not canonical"}
    if strategy_id == CANONICAL_STRATEGY_ID and ranked and ranked[0].get("rank"):
        primary_universe = rank([s for s in scored if s.receipt["recipe"]["membership"] == "PRIMARY"])
        parity = _primary_parity(primary_universe, ranked)
        parity["state"] = "PASS" if parity["rank_mismatches"] == 0 and parity["universe_equal"] else "FAIL"
        if parity["state"] != "PASS":
            raise ScoringError(f"canonical parity court FAILED: {json.dumps(parity, default=str)[:2000]}")

    universe = rank(scored, ranking_run_id=ranking_run_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "MEMBERSHIP_SCORE_RECEIPTS.jsonl").open("w", encoding="utf-8") as fh:
        for receipt in universe.receipts:
            fh.write(json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
    with (output_dir / "MEMBERSHIP_SCORE_PROJECTION.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(MEMBERSHIP_PROJECTION_COLUMNS))
        writer.writeheader()
        for receipt in universe.receipts:
            key = (receipt["opportunity"]["opportunity_key"], receipt["recipe"]["recipe_id"])
            writer.writerow(membership_projection_row(receipt, why_matched_receipt=why_matched[key]))

    receipts = universe.receipts
    summary = {
        "strategy_id": strategy.strategy_id,
        "strategy_sha256": strategy.sha256,
        "ranked_input": str(ranked_path),
        "membership_input": str(membership_path) if membership_path else None,
        "ranking_run_id": ranking_run_id,
        "properties": len(ranked),
        "memberships": len(receipts),
        "secondary_memberships": sum(1 for r in receipts if r["recipe"]["membership"] == "SECONDARY"),
        "membership_ranking_universe_id": universe.ranking_universe_id,
        "recipe_cohorts": dict(sorted(Counter(r["recipe"]["recipe_id"] for r in receipts).items())),
        "tier_counts": dict(sorted(Counter(r["composite"]["tier"] for r in receipts).items())),
        "dimension_states": {
            name: dict(sorted(Counter(
                c["evaluation_state"] for r in receipts for c in r["score_vector"]["components"] if c["name"] == name
            ).items()))
            for name in [c["name"] for c in receipts[0]["score_vector"]["components"]]
        } if receipts else {},
        "undetermined_factor_counts": dict(sorted(Counter(
            f for r in receipts for f in r["composite"]["undetermined_factors"]
        ).items())),
        "received_parity_states": dict(Counter(r["received"]["parity"] for r in receipts)),
        "canonical_primary_parity": parity,
        "db_mutated": False,
        "outbound": False,
        "contact_authorized": False,
    }
    (output_dir / "MEMBERSHIP_SCORE_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ranked", required=True, type=Path)
    parser.add_argument("--membership", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--strategy", default=CANONICAL_STRATEGY_ID)
    parser.add_argument("--ranking-run-id")
    args = parser.parse_args()
    try:
        summary = run(args.ranked, args.membership, args.output_dir,
                      strategy_id=args.strategy, ranking_run_id=args.ranking_run_id)
    except ScoringError as exc:
        print(json.dumps({"state": "REFUSED", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
