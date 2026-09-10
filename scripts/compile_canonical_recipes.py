#!/usr/bin/env python3
"""Thin CLI over the recipe compiler. No daemon, no store, no network.

    # compile the canonical 30 and (re)write the committed artifacts
    python scripts/compile_canonical_recipes.py compile

    # check the committed artifacts are exactly what the compiler emits
    python scripts/compile_canonical_recipes.py compile --check

    # evaluate one recipe-evaluation-input.v1 against every compiled recipe
    python scripts/compile_canonical_recipes.py evaluate --input candidate.json > evaluation.json

``evaluate`` writes one recipe-evaluation-set.v1 document: every membership
(MATCH / NO_MATCH / UNKNOWN), per-recipe score vector, missing evidence, NBE,
economics and qualification, plus the P1 eligibility receipt for every recipe
that lowers exactly.  It never ranks, mints a candidate, writes CRM or
authorizes contact.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipe_engine.opportunity_intelligence.eligibility import EligibilityError  # noqa: E402
from recipe_engine.opportunity_intelligence.recipe_compiler import (  # noqa: E402
    compile_canonical_recipes,
    compiled_summary_rows,
    evaluate_canonical_recipes,
)

ARTIFACT_DIR = ROOT / "artifacts" / "recipe_compiler"
COMPILED_JSON = ARTIFACT_DIR / "COMPILED_CANONICAL_30.v1.json"
COMPILED_TSV = ARTIFACT_DIR / "COMPILED_CANONICAL_30.tsv"


def render_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_tsv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def cmd_compile(args: argparse.Namespace) -> int:
    compiled = compile_canonical_recipes()
    outputs = {COMPILED_JSON: render_json(compiled), COMPILED_TSV: render_tsv(compiled_summary_rows(compiled))}
    if args.check:
        stale = [str(path.relative_to(ROOT)) for path, text in outputs.items()
                 if not path.exists() or path.read_text(encoding="utf-8") != text]
        if stale:
            print(f"STALE: {', '.join(stale)} (run without --check)", file=sys.stderr)
            return 1
        print(f"OK set_digest={compiled['set_digest']}")
        return 0
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    for path, text in outputs.items():
        path.write_text(text, encoding="utf-8")
    summary = compiled["summary"]
    print(f"set_digest={compiled['set_digest']} recipes={summary['recipe_count']} "
          f"p1_lowered={','.join(summary['p1_lowered']) or '-'}")
    for state, ids in summary["compile_states"].items():
        print(f"  {state:20} {len(ids):2}")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    source = sys.stdin if args.input == "-" else open(args.input, encoding="utf-8")
    with source:
        payload = json.load(source)
    try:
        result = evaluate_canonical_recipes(payload)
    except EligibilityError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(render_json(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    compile_parser = sub.add_parser("compile", help="compile the canonical 30")
    compile_parser.add_argument("--check", action="store_true", help="fail if committed artifacts are stale")
    compile_parser.set_defaults(func=cmd_compile)
    evaluate_parser = sub.add_parser("evaluate", help="evaluate one candidate against every compiled recipe")
    evaluate_parser.add_argument("--input", default="-", help="recipe-evaluation-input.v1 JSON (default stdin)")
    evaluate_parser.set_defaults(func=cmd_evaluate)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
