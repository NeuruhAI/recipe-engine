#!/usr/bin/env python3
"""NC curative ranking v3 — source-family independence annotation.

Delegates scoring/ranking to v2 (strict heir classifier over the v1 point
model) and then annotates every ranked record with:

- source_families: the independent SOURCE FAMILIES behind its triggers
- independent_source_family_count: how many truly independent families fired
- scoring_version / recipe_version stamps

Trigger labels are not independent merely because they have different names:
three labels derived from one assessor parcel roll are one family. Scores and
ordering are byte-identical to v2; this layer only makes signal independence
explicit and versioned.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts import rank_nc_curative_raw_v2 as v2

SCORING_VERSION = "nc_curative_rank.v3"
RECIPE_VERSION = "nc_curative_recipes.v1"

# Which independently-published source family each trigger is derived from.
# tax_distress comes from the county tax collector's delinquency publication;
# court_action from the county's foreclosure docket publication; everything
# else (owner-name text analysis, mailing state, sale date, assessed value)
# is derived from the assessor parcel roll (NC OneMap).
TRIGGER_FAMILY = {
    "tax_distress": "county_tax_collector_publication",
    "court_action": "county_foreclosure_docket_publication",
    "heir_or_estate": "assessor_parcel_roll",
    "ownership_fragmentation": "assessor_parcel_roll",
    "absentee_or_out_of_state": "assessor_parcel_roll",
    "long_term_ownership": "assessor_parcel_roll",
    "high_value_property": "assessor_parcel_roll",
}


def families_for(trigger_classes: str) -> list[str]:
    triggers = [t for t in str(trigger_classes or "").split("|") if t]
    seen: dict[str, None] = {}
    for trigger in triggers:
        family = TRIGGER_FAMILY.get(trigger)
        if family:
            seen.setdefault(family, None)
    return list(seen)


def annotate(row: dict[str, Any]) -> dict[str, Any]:
    families = families_for(row.get("trigger_classes"))
    row["source_families"] = "|".join(families)
    row["independent_source_family_count"] = len(families)
    row["signal_count"] = row.get("trigger_count", "")
    row["scoring_version"] = SCORING_VERSION
    row["recipe_version"] = RECIPE_VERSION
    return row


def run(input_csv: Path, output_dir: Path) -> dict[str, Any]:
    summary = v2.run(input_csv, output_dir)

    distribution: Counter[int] = Counter()
    for name in ("NC_CURATIVE_ALL.csv", "NC_CURATIVE_TOP_500.csv", "NC_CURATIVE_TOP_100.csv"):
        path = output_dir / name
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8-sig") as fh:
            rows = [annotate(dict(r)) for r in csv.DictReader(fh)]
        if name == "NC_CURATIVE_ALL.csv":
            distribution = Counter(int(r["independent_source_family_count"]) for r in rows)
        if rows:
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)

    summary["scoring_version"] = SCORING_VERSION
    summary["recipe_version"] = RECIPE_VERSION
    summary["independent_source_family_distribution"] = {
        str(k): v for k, v in sorted(distribution.items())
    }
    summary["signal_independence_note"] = (
        "independent_source_family_count counts independently published source "
        "families (tax collector publication, foreclosure docket, assessor parcel "
        "roll), not trigger labels."
    )
    (output_dir / "NC_CURATIVE_RECIPE_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    print(json.dumps(run(args.input, args.output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
