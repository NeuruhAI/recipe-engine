"""P1B court: thin CLI bind over evaluate_recipes.

Proves MATCH + NO_MATCH + UNKNOWN on the sealed P1 fixture, hash identity
unchanged, rank/primary_recipe refused, and the wrapper performs no I/O
during evaluation. Does not invent a daemon, store, or second membership path.
"""

from __future__ import annotations

import builtins
import importlib.util
import io
import json
import socket
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from recipe_engine.opportunity_intelligence.eligibility import EligibilityError

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_independent_eligibility.py"
FIXTURE = Path(__file__).parent / "fixtures" / "p1_independent_eligibility.json"

_spec = importlib.util.spec_from_file_location("run_independent_eligibility", SCRIPT)
assert _spec is not None and _spec.loader is not None
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

A, B, C, D = (
    "published_tax_distress",
    "stalled_tax_foreclosure_heirship",
    "open_probate_property",
    "long_term_absentee_potential_equity",
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _by_recipe(receipts: tuple[dict, ...]) -> dict[str, dict]:
    return {receipt["recipe_id"]: receipt for receipt in receipts}


def test_evaluate_bundle_emits_match_no_match_unknown_with_sealed_hashes():
    body = _fixture()
    receipts = _by_recipe(cli.evaluate_bundle(body))
    assert {r["decision"] for r in receipts.values()} == {"MATCH", "NO_MATCH", "UNKNOWN"}
    assert receipts[A]["decision"] == "MATCH"
    assert receipts[B]["decision"] == "NO_MATCH"
    assert receipts[C]["decision"] == "UNKNOWN"
    assert receipts[D]["decision"] == "MATCH"
    for recipe_id, expected in body["expected"].items():
        receipt = receipts[recipe_id]
        assert receipt["content_sha256"] == expected["content_sha256"]
        assert receipt["membership_key"] == expected["membership_key"]
        assert receipt["policy_digest"] == expected["policy_digest"]
        assert receipt["context_digest"] == expected["context_digest"]


def test_intake_projection_preserves_decisions_and_sealed_hashes():
    body = _fixture()
    projected = _by_recipe(cli.evaluate_bundle(body, intake=True))
    assert projected[A]["decision"] == "MATCH"
    assert projected[B]["decision"] == "NO_MATCH"
    assert projected[C]["decision"] == "UNKNOWN"
    for recipe_id, expected in body["expected"].items():
        assert projected[recipe_id]["content_sha256"] == expected["intake_content_sha256"]


def test_jsonl_contains_all_three_decisions_and_does_not_rehash():
    body = _fixture()
    receipts = cli.evaluate_bundle(body)
    text = cli.receipt_jsonl(receipts)
    lines = [json.loads(line) for line in text.splitlines()]
    assert {line["decision"] for line in lines} == {"MATCH", "NO_MATCH", "UNKNOWN"}
    by_id = {line["recipe_id"]: line for line in lines}
    for recipe_id, expected in body["expected"].items():
        assert by_id[recipe_id]["content_sha256"] == expected["content_sha256"]


@pytest.mark.parametrize("key", ["primary_recipe", "rank", "score"])
def test_rank_and_primary_recipe_are_refused(key):
    body = _fixture()
    poisoned = deepcopy(body)
    poisoned["context"][key] = "not-evidence"
    with pytest.raises(EligibilityError, match="not eligibility authority"):
        cli.evaluate_bundle(poisoned)


def test_unknown_is_not_rewritten_to_no_match():
    body = _fixture()
    receipts = _by_recipe(cli.evaluate_bundle({"context": body["context"], "policy": body["policies"]["C"]}))
    assert receipts[C]["decision"] == "UNKNOWN"
    assert receipts[C]["decision"] != "NO_MATCH"


def test_evaluate_bundle_performs_no_io(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the eligibility CLI core must not touch files, sockets or databases")

    body = _fixture()
    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(sqlite3, "connect", refuse)
    receipts = cli.evaluate_bundle(body)
    assert {r["decision"] for r in receipts} == {"MATCH", "NO_MATCH", "UNKNOWN"}
    assert cli.receipt_jsonl(receipts).count("\n") == 4


def test_main_fixture_path_writes_jsonl(capsys):
    assert cli.main(["--fixture", str(FIXTURE)]) == 0
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines()]
    decisions = {line["recipe_id"]: line["decision"] for line in lines}
    assert decisions[A] == "MATCH"
    assert decisions[B] == "NO_MATCH"
    assert decisions[C] == "UNKNOWN"
    assert captured.err == ""


def test_main_reads_stdin_bundle_and_writes_jsonl(monkeypatch, capsys):
    body = _fixture()
    bundle = {"context": body["context"], "policies": body["policies"]}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bundle)))
    assert cli.main([]) == 0
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines()]
    decisions = {line["recipe_id"]: line["decision"] for line in lines}
    assert decisions[A] == "MATCH"
    assert decisions[B] == "NO_MATCH"
    assert decisions[C] == "UNKNOWN"
    assert captured.err == ""


def test_main_refuses_rank_authority_on_stdin(monkeypatch, capsys):
    body = _fixture()
    poisoned = {"context": dict(body["context"], rank=1), "policies": body["policies"]}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(poisoned)))
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not eligibility authority" in captured.err


def test_script_is_not_a_daemon():
    source = SCRIPT.read_text(encoding="utf-8")
    for name in (
        "HTTPServer",
        "serve_forever",
        "uvicorn",
        "fastapi",
        "supabase",
        "deedsonar",
        "requests",
        "psycopg",
        "sqlite3",
    ):
        assert name not in source
