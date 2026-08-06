from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from recipe_engine.nc_dps_contract import HARD_STOP_FIELDS, validate_p0_candidate


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def choose_strategy(packet: dict[str, Any]) -> tuple[str, bool, list[str], str]:
    triggers = set(packet["scores"]["trigger_classes"])
    priority = packet["scores"]["composite_priority"]
    source_urls = {
        source.get("source_url")
        for source in packet.get("source_records", [])
        if source.get("source_url")
    }
    corroborated = len(source_urls) >= 2

    if "death_or_probate" in triggers:
        return "heir_property_jv", corroborated and priority >= 55, [], "Research heirs, probate status, and a counsel-approved joint-venture path."
    if "title_chain_problem" in triggers or "recorded_lien_or_judgment" in triggers:
        return "title_cure_and_resale", corroborated and priority >= 55, [], "Complete title-curative diligence and compare cure-to-resale economics."
    if "court_action" in triggers and "tax_distress" in triggers:
        return "stalled_tax_case_resolution", corroborated and priority >= 55, [], "Verify the filed case, service history, cure amount, ownership, and resolution options."
    if "tax_distress" in triggers and "absentee_or_out_of_state" in triggers and corroborated and priority >= 45:
        return "whole_property_acquisition", True, [], "Complete ownership, debt, condition, and valuation diligence before considering acquisition."
    return "monitor", True, [], "Keep in enrichment until parcel, title, court, probate, vacancy, or lien evidence corroborates the distress signal."


def candidate(packet: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    strategy, eligible, disqualifiers, action = choose_strategy(packet)
    value = packet["valuation"]["conservative_as_is_value"]
    result = {
        "schema_version": "nc_dps_recipe_candidate.v1",
        "candidate_id": hashlib.sha256(f"{packet['property_id']}|{strategy}".encode()).hexdigest(),
        "property_id": packet["property_id"],
        "strategy_id": strategy,
        "eligible": eligible,
        "disqualifiers": disqualifiers,
        "economics": {
            "acquisition_cost": None,
            "title_cure_cost": None,
            "legal_cost": None,
            "carrying_cost": None,
            "disposition_cost": None,
            "gross_exit_value": value,
            "expected_net_value": None,
            "risk_adjusted_value": None,
        },
        "execution_plan": {
            "action_summary": action,
            "dependencies": [
                "verify_current_owner_and_all_interest_holders",
                "verify_taxes_liens_mortgages_and_bankruptcy",
                "verify_market_value_condition_and_occupancy",
                "obtain_legal_review",
                "obtain_human_approval",
            ],
            "reversible": True,
            "legal_review_required": True,
            "human_approval_required": True,
        },
        "evidence": {
            "property_packet_version": "nc_dps_property_packet.v1",
            "property_packet_sha256": sha256(packet),
            "score_receipt_sha256": sha256(receipt),
            "source_record_count": len(packet["source_records"]),
            "contradictions": [],
        },
        "governance": {field: False for field in HARD_STOP_FIELDS},
    }
    validate_p0_candidate(result)
    return result


def dossier_text(packet: dict[str, Any], cand: dict[str, Any], rank: int) -> str:
    sources = "\n".join(
        f"- {source['source_url']} ({source['evidence_class']}, confidence {source['confidence']})"
        for source in packet["source_records"]
    )
    next_steps = "\n".join(
        f"{index}. {step}"
        for index, step in enumerate(packet["next_research_steps"], 1)
    )
    return f"""# NC DPS Decision Dossier — Rank {rank}

## Property

- Property ID: `{packet['property_id']}`
- County: {packet['county']}
- Parcel: {packet['parcel_id']}
- Address: {packet['identity']['property_address'] or 'Unknown'}
- Owner of record: {packet['identity']['owner_of_record'] or 'Unknown'}
- Mailing address: {packet['identity']['owner_mailing_address'] or 'Unknown'}

## Decision

- Composite priority: **{packet['scores']['composite_priority']}**
- Trigger classes: {', '.join(packet['scores']['trigger_classes']) or 'None'}
- Recommended strategy: **{cand['strategy_id']}**
- Eligible for continued diligence: **{cand['eligible']}**

## Known economics

- Assessed-value proxy: {packet['valuation']['conservative_as_is_value']}
- Published tax balance: {packet['valuation']['tax_balance']}
- Estimated mortgage balance: Unknown
- Verified equity: Unknown

## Risks and limits

{chr(10).join('- ' + risk for risk in packet['risks'])}

No contact, offer, filing, payment, recording, or execution is authorized by this dossier.

## Next five research steps

{next_steps}

## Sources

{sources}
"""


def build(packets_path: Path, receipts_path: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    dossiers = output_dir / "decision_dossiers"
    dossiers.mkdir(parents=True, exist_ok=True)

    packets = read_jsonl(packets_path)
    receipts = {row["property_id"]: row for row in read_jsonl(receipts_path)}
    candidates: list[dict[str, Any]] = []
    monitored = 0

    for rank, packet in enumerate(packets, 1):
        cand = candidate(packet, receipts[packet["property_id"]])
        if cand["strategy_id"] == "monitor":
            monitored += 1
            continue
        candidates.append(cand)
        safe_name = packet["property_id"].replace(":", "_").replace("/", "_")
        (dossiers / f"{rank:03d}_{safe_name}.md").write_text(
            dossier_text(packet, cand, rank),
            encoding="utf-8",
        )

    write_jsonl(output_dir / "strategy_candidates.jsonl", candidates)
    summary = {
        "property_packets": len(packets),
        "strategy_candidates": len(candidates),
        "monitored_or_enrichment": monitored,
        "dossiers": len(list(dossiers.glob("*.md"))),
        "strategies": {
            name: sum(1 for row in candidates if row["strategy_id"] == name)
            for name in sorted({row["strategy_id"] for row in candidates})
        },
    }
    (output_dir / "recipe_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
