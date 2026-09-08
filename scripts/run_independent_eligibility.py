#!/usr/bin/env python3
"""Thin CLI over ``evaluate_recipes``. No daemon, no store, no network.

Reads one eligibility-context.v1 plus one or more eligibility-policy.v1
documents from files or stdin, evaluates every policy independently, and
writes MATCH / NO_MATCH / UNKNOWN receipts as JSONL on stdout.

This is a harness wrapper around the library producer already on main
(``recipe_engine.opportunity_intelligence.eligibility.evaluate_recipes``).
It does not rank, mint a candidate, write CRM, touch Supabase or DeedSonar,
or open a listening service.

    python scripts/run_independent_eligibility.py \\
        --fixture tests/opportunity_intelligence/fixtures/p1_independent_eligibility.json

    python scripts/run_independent_eligibility.py \\
        --context context.json --policy a.json --policy b.json

    python scripts/run_independent_eligibility.py < bundle.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipe_engine.opportunity_intelligence.eligibility import (  # noqa: E402
    ELIGIBILITY_POLICY_CONTRACT,
    EligibilityError,
    evaluate_recipes,
    to_intake_receipt,
)

DECISIONS = ("MATCH", "NO_MATCH", "UNKNOWN")


def iter_policies(value: Any) -> list[Mapping[str, Any]]:
    """A single policy, a list of policies, or a label->policy map (P1 fixture)."""
    if isinstance(value, Mapping):
        if value.get("schema_version") == ELIGIBILITY_POLICY_CONTRACT:
            return [value]
        return [item for item in value.values()]
    if isinstance(value, (list, tuple)):
        if not value:
            raise EligibilityError("policies must declare at least one policy")
        return list(value)
    raise EligibilityError("policies must be a policy, a list of policies, or a label map")


def bundle_from_parts(
    context: Mapping[str, Any],
    policies: Iterable[Any],
) -> dict[str, Any]:
    collected: list[Mapping[str, Any]] = []
    for item in policies:
        collected.extend(iter_policies(item))
    return {"context": context, "policies": collected}


def evaluate_bundle(
    bundle: Mapping[str, Any],
    *,
    intake: bool = False,
) -> tuple[dict[str, Any], ...]:
    """I/O-free call path: context + policies -> receipt dicts.

    Rank / primary_recipe / score fields are refused by the producer, not
    rewritten here. Decision vocabulary is unchanged.
    """
    if not isinstance(bundle, Mapping):
        raise EligibilityError("bundle must be an object")
    context = bundle.get("context")
    if not isinstance(context, Mapping):
        raise EligibilityError("bundle.context must be an eligibility-context.v1 object")
    if "policies" in bundle:
        policies = iter_policies(bundle["policies"])
    elif "policy" in bundle:
        policies = iter_policies(bundle["policy"])
    else:
        raise EligibilityError("bundle must include policy or policies")
    receipts = evaluate_recipes(context, policies)
    if intake:
        return tuple(to_intake_receipt(receipt) for receipt in receipts)
    return tuple(receipt.to_dict() for receipt in receipts)


def receipt_jsonl(receipts: Sequence[Mapping[str, Any]]) -> str:
    """One receipt per line. Compact, sorted keys; hashes are not recomputed."""
    lines = []
    for receipt in receipts:
        if receipt.get("decision") not in DECISIONS:
            raise EligibilityError("receipt.decision must be MATCH, NO_MATCH or UNKNOWN")
        lines.append(json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return ("\n".join(lines) + "\n") if lines else ""


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_invocation(
    *,
    fixture: Path | None,
    context: Path | None,
    policies: Sequence[Path],
    stdin_text: str,
) -> Mapping[str, Any]:
    chosen = sum(bool(x) for x in (fixture, context, stdin_text.strip()))
    if fixture and (context or policies):
        raise EligibilityError("use --fixture XOR --context/--policy XOR stdin")
    if fixture:
        return _load_json(fixture)
    if context:
        if not policies:
            raise EligibilityError("--context requires at least one --policy")
        return bundle_from_parts(
            _load_json(context),
            [_load_json(path) for path in policies],
        )
    if stdin_text.strip():
        if policies:
            raise EligibilityError("stdin bundle cannot be combined with --policy")
        loaded = json.loads(stdin_text)
        if not isinstance(loaded, Mapping):
            raise EligibilityError("stdin bundle must be an object")
        return loaded
    if chosen == 0 and not policies:
        raise EligibilityError("provide --fixture, --context/--policy, or a JSON bundle on stdin")
    raise EligibilityError("use --fixture XOR --context/--policy XOR stdin")


def main(argv: list[str] | None = None, *, stdin_text: str | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, help="P1-style bundle with context + policies")
    parser.add_argument("--context", type=Path, help="eligibility-context.v1 JSON file")
    parser.add_argument(
        "--policy",
        dest="policies",
        type=Path,
        action="append",
        default=[],
        help="eligibility-policy.v1 JSON file (repeatable)",
    )
    parser.add_argument(
        "--intake",
        action="store_true",
        help="emit lead-recipe-os intake projections (FIXTURE mode only)",
    )
    args = parser.parse_args(argv)
    if stdin_text is not None:
        raw_stdin = stdin_text
    elif args.fixture or args.context:
        raw_stdin = ""
    else:
        raw_stdin = sys.stdin.read()
    try:
        bundle = load_invocation(
            fixture=args.fixture,
            context=args.context,
            policies=args.policies,
            stdin_text=raw_stdin,
        )
        receipts = evaluate_bundle(bundle, intake=args.intake)
    except (EligibilityError, json.JSONDecodeError, OSError) as exc:
        print(f"eligibility: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(receipt_jsonl(receipts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
