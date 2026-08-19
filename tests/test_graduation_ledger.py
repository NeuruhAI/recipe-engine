"""
The durable graduation decision ledger.

Two properties matter: a decision must be re-derivable and chained, and a replay
must NOT append. The second is not theoretical — the continuity journal reached
614 MB on 2026-08-18 because an unchanged scan kept appending.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recipe_engine.graduation_ledger import (  # noqa: E402
    append_decisions, build_entry, decision_identity, entry_hash, read_ledger, verify_chain,
)


def decision(signal_id="5038419", classification="research_property", **over):
    base = dict(signal_id=signal_id, classification=classification,
                policy_version="graduation.v1", evidence_hash="a" * 64,
                decided_at="2026-08-19T10:00:00Z", property_key="MI:26021:11-02",
                identity_verdict="RESOLVED", missing=[], run_id="run-1")
    base.update(over)
    return base


def test_a_decision_records_what_decided_it(tmp_path):
    path = tmp_path / "led.jsonl"
    append_decisions(path, [decision()])
    row = read_ledger(path)[0]
    assert row["policy_version"] == "graduation.v1"
    assert row["evidence_hash"] == "a" * 64
    assert row["classification"] == "research_property"
    assert row["sequence"] == 0
    assert row["previous_entry_hash"] is None


def test_a_decision_without_an_outcome_is_not_calibration_eligible(tmp_path):
    path = tmp_path / "led.jsonl"
    append_decisions(path, [decision()])
    row = read_ledger(path)[0]
    assert row["calibration_eligible"] is False
    assert "outcome" in row["calibration_blocked_reason"]


def test_disposition_and_outcome_together_make_it_eligible(tmp_path):
    path = tmp_path / "led.jsonl"
    append_decisions(path, [decision(disposition={"by": "operator", "verdict": "agree"},
                                     outcome={"result": "acquired"})])
    row = read_ledger(path)[0]
    assert row["calibration_eligible"] is True
    assert row["calibration_blocked_reason"] is None


def test_disposition_alone_is_not_enough(tmp_path):
    path = tmp_path / "led.jsonl"
    append_decisions(path, [decision(disposition={"by": "operator"})])
    assert read_ledger(path)[0]["calibration_eligible"] is False


# ---- replay is a fixed point -------------------------------------------------

def test_replaying_the_same_decision_appends_nothing(tmp_path):
    path = tmp_path / "led.jsonl"
    assert append_decisions(path, [decision()]) == {"appended": 1, "retained": 0}
    assert append_decisions(path, [decision()]) == {"appended": 0, "retained": 1}
    assert len(read_ledger(path)) == 1


def test_a_new_run_id_alone_is_the_same_decision(tmp_path):
    path = tmp_path / "led.jsonl"
    append_decisions(path, [decision(run_id="run-1")])
    append_decisions(path, [decision(run_id="run-2", decided_at="2026-08-20T00:00:00Z")])
    assert len(read_ledger(path)) == 1, (
        "re-running the classifier over unchanged evidence is not a new decision")


def test_a_changed_classification_is_a_new_decision(tmp_path):
    path = tmp_path / "led.jsonl"
    append_decisions(path, [decision(classification="observed_signal")])
    append_decisions(path, [decision(classification="research_property")])
    assert len(read_ledger(path)) == 2


def test_a_changed_policy_version_is_a_new_decision(tmp_path):
    path = tmp_path / "led.jsonl"
    append_decisions(path, [decision()])
    append_decisions(path, [decision(policy_version="graduation.v2")])
    assert len(read_ledger(path)) == 2


# ---- the chain ---------------------------------------------------------------

def test_the_chain_links_and_verifies(tmp_path):
    path = tmp_path / "led.jsonl"
    append_decisions(path, [decision("a"), decision("b"), decision("c")])
    result = verify_chain(path)
    assert result["ok"] is True
    assert result["entries"] == 3
    rows = read_ledger(path)
    assert rows[1]["previous_entry_hash"] == rows[0]["entry_hash"]


def test_tampering_is_detected(tmp_path):
    path = tmp_path / "led.jsonl"
    append_decisions(path, [decision("a"), decision("b")])
    rows = read_ledger(path)
    rows[0]["classification"] = "qualified_candidate"      # forge a promotion
    path.write_text("\n".join(json.dumps(r, sort_keys=True, separators=(",", ":"))
                              for r in rows) + "\n")
    result = verify_chain(path)
    assert result["ok"] is False
    assert result["broken_at"] == 0


def test_an_empty_ledger_verifies(tmp_path):
    assert verify_chain(tmp_path / "nothing.jsonl")["ok"] is True


def test_a_torn_final_line_does_not_lose_the_chain(tmp_path):
    path = tmp_path / "led.jsonl"
    append_decisions(path, [decision("a")])
    with path.open("a") as handle:
        handle.write('{"partial": ')
    assert len(read_ledger(path)) == 1


def test_decision_identity_ignores_run_and_time():
    a = decision_identity("s", "graduation.v1", "h", "research_property")
    b = decision_identity("s", "graduation.v1", "h", "research_property")
    assert a == b
    assert a != decision_identity("s", "graduation.v1", "h", "corroborated")


def test_entry_hash_covers_the_body_not_itself():
    entry = build_entry(signal_id="s", classification="c", policy_version="v",
                        evidence_hash="h", decided_at="t", sequence=0,
                        previous_entry_hash=None)
    assert entry["entry_hash"] == entry_hash(entry)
