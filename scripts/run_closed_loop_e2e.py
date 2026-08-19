#!/usr/bin/env python3
"""Run the closed-loop synthetic court and write its evidence.

    python3 scripts/run_closed_loop_e2e.py --out-dir /path/to/evidence

Writes `loop-ledger.jsonl` (the hash-chained event ledger) and `summary.json`
(the artifacts and the reconstruction) into `--out-dir`, which must be supplied:
this script never writes into the repository by default, because a court that
leaves evidence lying in a source tree eventually gets committed by accident.

`--mode live` runs the identical path with live-labelled evidence and reaches
PROMOTION_ELIGIBLE. It still promotes nothing: eligibility is not approval, and
`promote()` requires an external approval reference this script cannot mint.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from recipe_engine.closed_loop.e2e import run_full_loop           # noqa: E402
from recipe_engine.closed_loop.events import grammar_digest, grammar_manifest  # noqa: E402
from recipe_engine.closed_loop.identity import WorldMode          # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="directory to write the ledger and summary into")
    parser.add_argument("--mode", default="synthetic",
                        choices=[m.value for m in WorldMode],
                        help="world mode of the run (default: synthetic)")
    parser.add_argument("--emit-grammar", action="store_true",
                        help="also write event-grammar.json for an external verifier")
    args = parser.parse_args(argv)

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = out_dir / "loop-ledger.jsonl"
    if ledger_path.exists():
        ledger_path.unlink()

    artifacts = run_full_loop(mode=WorldMode(args.mode), ledger_path=ledger_path)

    summary = {
        "mode": args.mode,
        "grammar_digest": grammar_digest(),
        "event_count": len(artifacts.ledger),
        "ledger_tip": artifacts.ledger.tip,
        "event_types": [e.event_type.value for e in artifacts.ledger.events],
        "world_state": artifacts.world_state.to_dict(),
        "world_state_delta": artifacts.world_delta.to_dict(),
        "recipe_evaluation": artifacts.evaluation.to_dict(),
        "decision": artifacts.decision.to_dict(),
        "action_intent": artifacts.intent.to_dict(),
        "action_receipt": artifacts.receipt.to_dict(),
        "execution_binding": artifacts.execution_binding,
        "forecast": artifacts.forecast.to_dict(),
        "outcome": artifacts.outcome.to_dict(),
        "attribution": artifacts.attribution.to_dict(),
        "calibration": artifacts.calibration.to_dict(),
        "source_ranking": artifacts.source_ranking.to_dict(),
        "recipe_scorecard": artifacts.recipe_scorecard.to_dict(),
        "recipe_diagnosis": artifacts.recipe_diagnosis.to_dict(),
        "learning_proposal": artifacts.proposal.to_dict(),
        "promotion_decision": artifacts.promotion_decision.to_dict(),
        "reconstruction": artifacts.reconstruction.to_dict(),
        "reconstruction_digest": artifacts.reconstruction.digest,
        "versions_promoted": list(artifacts.reconstruction.versions_promoted),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.emit_grammar:
        (out_dir / "event-grammar.json").write_text(
            json.dumps(grammar_manifest(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

    decision = artifacts.promotion_decision
    print(f"mode              : {args.mode}")
    print(f"events            : {len(artifacts.ledger)}  tip {artifacts.ledger.tip[:16]}…")
    print(f"grammar digest    : {grammar_digest()}")
    print(f"replay digest     : {artifacts.reconstruction.digest}")
    print(f"recipe constraint : {artifacts.recipe_diagnosis.constraint} — "
          f"{artifacts.recipe_diagnosis.verdict}")
    first = artifacts.source_ranking.recommended_first()
    print(f"source first      : {first.source_type if first else 'none measured'}")
    print(f"candidate version : {artifacts.proposal.candidate_version}")
    print(f"promotion         : {decision.outcome.value.upper()} "
          f"(deployment_authority={decision.deployment_authority})")
    for reason in decision.reasons:
        print(f"  · {reason}")
    print(f"versions promoted : {len(artifacts.reconstruction.versions_promoted)}")
    print(f"written           : {ledger_path}  {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
