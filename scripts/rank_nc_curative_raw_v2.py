#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from scripts import rank_nc_curative_raw as base


def institutional(text: str) -> bool:
    return bool(re.search(
        r"\b(?:REAL\s+ESTATE|HOLDINGS?|LLC|L\.L\.C|INC|CORP(?:ORATION)?|COMPANY|BANK|AUTHORITY|CHURCH|ASSOCIATION)\b",
        text,
    ))


def strict_heir(owner: Any, current: Any) -> bool:
    text = re.sub(r"\s+", " ", str(owner or "").upper()).strip()
    # Explicit HEIR/HEIRS and ESTATE OF are strong owner-name signals.
    if re.search(r"\bHEIRS?\b|\bESTATE\s+OF\b", text):
        return True
    if re.search(r"\bESTATE\b", text) and not re.search(r"\bREAL\s+ESTATE\b", text):
        return True
    if re.search(r"\bHRS\b", text) and not institutional(text):
        return True
    # Preserve a prior true flag only when the owner text itself is not an obvious
    # institutional false positive such as REAL ESTATE or HRS HOLDING CO.
    return base.truthy(current) and not institutional(text)


def strict_fragmented(owner: Any, current: Any) -> bool:
    text = re.sub(r"\s+", " ", str(owner or "").upper()).strip()
    inst = institutional(text)
    if re.search(r"\bET\s+ALS?\b|\bETALS\b|\bHEIRS?\b", text):
        return True
    if re.search(r"\bHRS\b", text) and not inst:
        return True
    if " & " in text and not inst:
        return True
    return base.truthy(current) and not inst


def run(input_csv: Path, output_dir: Path) -> dict[str, Any]:
    with input_csv.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    repaired = 0
    for row in rows:
        old_heir = base.truthy(row.get("heir_indicator"))
        old_frag = base.truthy(row.get("fragmented_ownership_indicator"))
        new_heir = strict_heir(row.get("owner_name"), row.get("heir_indicator"))
        new_frag = strict_fragmented(row.get("owner_name"), row.get("fragmented_ownership_indicator"))
        if old_heir != new_heir or old_frag != new_frag:
            repaired += 1
        row["heir_indicator"] = new_heir
        row["fragmented_ownership_indicator"] = new_frag

    with tempfile.TemporaryDirectory(prefix="nc-curative-rank-v2-") as td:
        patched = Path(td) / "repaired.csv"
        if rows:
            with patched.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        else:
            patched.write_text("", encoding="utf-8")
        summary = base.run(patched, output_dir)

    summary["classification_repairs"] = repaired
    summary["heir_classifier"] = "strict_v2"
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
