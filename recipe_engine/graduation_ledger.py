"""
graduation_ledger.py — durable, hash-chained record of every graduation decision.

WHY THIS IS NOT A 017 CALIBRATION ENTRY (yet). neuruh_outcome_record's
`to_017_calibration_entry` requires calibration_eligible, a PRE-OUTCOME forecast
probability, and a human grade — it refuses anything else, correctly. A
graduation decision made today has no outcome and no operator disposition, so
manufacturing a forecast or a grade to satisfy that shape would be fabricating
the very evidence calibration exists to measure.

So a decision is recorded here the moment it is made, with the slots that make
it BINDABLE later:

    policy_version      which thresholds decided it
    evidence_hash       the exact inputs, re-derivable
    classification      the state it was given
    disposition         operator/founder judgement, when it arrives
    outcome             what actually happened, when it is known

`calibration_eligible` stays false until both arrive, and the reason is stated
rather than implied. Once they do, an entry carries everything
neuruh_outcome_record needs to bind it into the 017 chain.

Same chain discipline as the 017 ledger: append-only, sequence + previous_entry_hash
+ entry_hash. Replay is a fixed point — an identical decision for the same
signal is NOT re-appended, so this cannot become another journal that grows on
every scan.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "neuruh.graduation-decision-ledger.v0.1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def entry_hash(entry: dict[str, Any]) -> str:
    """Digest over everything except the digest itself."""
    body = {k: v for k, v in entry.items() if k != "entry_hash"}
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # a torn final line is not a reason to lose the chain
    return rows


def decision_identity(signal_id: str, policy_version: str, evidence_hash: str,
                      classification: str) -> str:
    """What makes two decisions THE SAME decision. Deliberately excludes run_id
    and timestamps: re-running the classifier over unchanged evidence under an
    unchanged policy is the same decision, not a new one."""
    return hashlib.sha256(
        canonical_json([signal_id, policy_version, evidence_hash, classification]).encode()
    ).hexdigest()


def build_entry(*, signal_id: str, classification: str, policy_version: str,
                evidence_hash: str, decided_at: str, sequence: int,
                previous_entry_hash: str | None,
                property_key: str | None = None,
                identity_verdict: str | None = None,
                missing: Iterable[str] = (),
                run_id: str | None = None,
                disposition: dict[str, Any] | None = None,
                outcome: dict[str, Any] | None = None) -> dict[str, Any]:
    """One decision, ready to append."""
    entry = {
        "schema_version": SCHEMA_VERSION,
        "sequence": sequence,
        "previous_entry_hash": previous_entry_hash,
        "decision_id": decision_identity(signal_id, policy_version, evidence_hash, classification),
        "signal_id": signal_id,
        "property_key": property_key,
        "identity_verdict": identity_verdict,
        "classification": classification,
        "missing_requirements": list(missing),
        "policy_version": policy_version,
        "evidence_hash": evidence_hash,
        "decided_at": decided_at,
        "run_id": run_id,
        # Filled in later, by a human and by reality respectively.
        "disposition": disposition,
        "outcome": outcome,
        # A decision with no outcome cannot score a forecast. Say why.
        "calibration_eligible": bool(disposition and outcome),
        "calibration_blocked_reason": (
            None if (disposition and outcome)
            else "no operator disposition and/or no observed outcome yet"),
    }
    entry["entry_hash"] = entry_hash(entry)
    return entry


def append_decisions(path: Path, decisions: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Append only decisions the ledger has not already recorded.

    Returns {appended, retained}. Retained means the identical decision is
    already in the chain — which is what makes a replay a fixed point.
    """
    existing = read_ledger(path)
    seen = {row.get("decision_id") for row in existing}
    sequence = len(existing)
    previous = existing[-1]["entry_hash"] if existing else None

    appended = 0
    retained = 0
    lines = []
    for decision in decisions:
        identity = decision_identity(
            decision["signal_id"], decision["policy_version"],
            decision["evidence_hash"], decision["classification"])
        if identity in seen:
            retained += 1
            continue
        entry = build_entry(sequence=sequence, previous_entry_hash=previous, **decision)
        lines.append(canonical_json(entry))
        seen.add(identity)
        previous = entry["entry_hash"]
        sequence += 1
        appended += 1

    if lines:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return {"appended": appended, "retained": retained}


def verify_chain(path: Path) -> dict[str, Any]:
    """Walk the chain. Reports the first break rather than raising."""
    rows = read_ledger(path)
    previous = None
    for index, row in enumerate(rows):
        if row.get("previous_entry_hash") != previous:
            return {"ok": False, "entries": len(rows), "broken_at": index,
                    "reason": "previous_entry_hash does not match the prior entry"}
        if row.get("entry_hash") != entry_hash(row):
            return {"ok": False, "entries": len(rows), "broken_at": index,
                    "reason": "entry_hash does not match the entry body"}
        previous = row["entry_hash"]
    return {"ok": True, "entries": len(rows), "tip": previous}
