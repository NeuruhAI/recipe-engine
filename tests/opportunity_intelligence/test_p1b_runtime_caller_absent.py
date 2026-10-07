"""P1B court, as bound by T11: exactly one runtime calls the eligibility producer.

2026-09-08 (P1B, e93f2ac) sealed RUNTIME_CALL_PATH=CALLER_ABSENT: LIBRARY + HARNESS +
CLI existed and no runtime loop called them. 2026-09-15 (T11) bound the one caller the
three missing seams described -- ``scripts/run_opportunity_eligibility.py`` over the
compiled canonical 30, persisted through DeedSonar ``candidate_recipe_membership``.

What stays locked: the named runtime candidates (the ranker ladder, the daily estate-sale
hunt, Founder Runtime, the LandOS score caller, closed loop, autonomy) still never import
or invoke the producer; the bound runtime reaches it only through the compiler.

Does not invent a daemon, a store, or a second membership path.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from recipe_engine.opportunity_intelligence.eligibility import evaluate_recipes

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "p1_independent_eligibility.json"
CENSUS = ROOT / "artifacts" / "P1B_RUNTIME_CALLER_CENSUS.tsv"
RECEIPT = ROOT / "artifacts" / "P1B_RUNTIME_CALL_PATH_RECEIPT.json"

ALLOWED_CALLERS = frozenset({
    "recipe_engine/opportunity_intelligence/eligibility.py",
    "recipe_engine/opportunity_intelligence/__init__.py",
    # Library, not runtime: the pure recipe compiler lowers exactly-expressible
    # canonical recipes to eligibility-policy.v1 and asserts decision parity.
    # No runtime candidate may import it (test_recipe_compiler locks that).
    "recipe_engine/opportunity_intelligence/recipe_compiler.py",
    "scripts/p1_independent_eligibility_proof.py",
    "scripts/run_independent_eligibility.py",
})

RUNTIME_CANDIDATES = (
    "run.py",
    "scripts/daily_run.sh",
    "scripts/rank_nc_curative_raw.py",
    "scripts/rank_nc_curative_raw_v2.py",
    "scripts/rank_nc_curative_raw_v3.py",
    "scripts/verify_opportunity_intelligence.py",
    "recipe_engine/opportunity_intelligence/evaluate.py",
    "recipe_engine/opportunity_intelligence/source_factory.py",
    "recipe_engine/opportunity_intelligence/oppack_receipts.py",
    "recipe_engine/closed_loop/recipe_eval.py",
    "recipe_engine/closed_loop/e2e.py",
    "recipe_engine/closed_loop/ledger.py",
    "recipe_engine/graduation.py",
    "recipe_engine/autonomy/collapse.py",
    "recipe_engine/nc_dps_contract.py",
    "recipe_engine/engine.py",
)

FORBIDDEN_TOKENS = (
    "evaluate_recipes",
    "evaluate_eligibility",
    "eligibility-policy.v1",
    "eligibility-context.v1",
    "eligibility-receipt.v1",
)

A, B, C = (
    "published_tax_distress",
    "stalled_tax_foreclosure_heirship",
    "open_probate_property",
)


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _python_sources() -> list[Path]:
    skip_parts = {".venv", ".git", "__pycache__", "node_modules"}
    sources: list[Path] = []
    for path in ROOT.rglob("*.py"):
        if skip_parts & set(path.parts):
            continue
        sources.append(path)
    return sources


def _imported_or_called(tree: ast.AST, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.names:
            if any(alias.name == name or alias.asname == name for alias in node.names):
                return True
        if isinstance(node, ast.Import):
            if any(alias.name.endswith(f".{name}") or alias.asname == name for alias in node.names):
                return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == name:
                return True
            if isinstance(func, ast.Attribute) and func.attr == name:
                return True
    return False


def test_evaluate_recipes_callers_are_only_library_harness_cli_and_tests():
    unexpected: list[str] = []
    for path in _python_sources():
        rel = _rel(path)
        if rel.startswith("tests/"):
            continue
        if rel in ALLOWED_CALLERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        if _imported_or_called(tree, "evaluate_recipes") or _imported_or_called(
            tree, "evaluate_eligibility"
        ):
            unexpected.append(rel)
    assert unexpected == [], (
        "evaluate_recipes/evaluate_eligibility leaked into a non-allowed path: "
        + ", ".join(sorted(unexpected))
    )


def test_named_runtime_candidates_do_not_mention_eligibility_producer():
    missing = [rel for rel in RUNTIME_CANDIDATES if not (ROOT / rel).is_file()]
    assert missing == [], f"census candidate missing from tree: {missing}"
    hits: list[str] = []
    for rel in RUNTIME_CANDIDATES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            if token in text:
                hits.append(f"{rel}:{token}")
    founder_root = ROOT / "recipe_engine" / "founder_runtime"
    for path in founder_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            if token in text:
                hits.append(f"{_rel(path)}:{token}")
    assert hits == [], "runtime candidate mentions eligibility producer: " + ", ".join(hits)


def test_library_still_emits_match_no_match_unknown_on_sealed_fixture():
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    receipts = {r.recipe_id: r for r in evaluate_recipes(body["context"], body["policies"].values())}
    expected = body["expected"]
    assert receipts[A].decision == "MATCH"
    assert receipts[B].decision == "NO_MATCH"
    assert receipts[C].decision == "UNKNOWN"
    assert receipts[A].content_sha256 == expected[A]["content_sha256"]
    assert receipts[B].content_sha256 == expected[B]["content_sha256"]
    assert receipts[C].content_sha256 == expected[C]["content_sha256"]


RUNTIME = "scripts/run_opportunity_eligibility.py"
RUNTIME_LIBRARY = "recipe_engine/opportunity_intelligence/eligibility_runtime.py"


def test_the_bound_runtime_reaches_the_producer_only_through_the_compiler():
    library = ast.parse((ROOT / RUNTIME_LIBRARY).read_text(encoding="utf-8"))
    assert _imported_or_called(library, "evaluate_canonical_recipes")
    for name in ("evaluate_recipes", "evaluate_eligibility"):
        assert not _imported_or_called(library, name)
    runtime = ast.parse((ROOT / RUNTIME).read_text(encoding="utf-8"))
    assert _imported_or_called(runtime, "evaluate_property")
    assert RUNTIME not in RUNTIME_CANDIDATES and RUNTIME_LIBRARY not in RUNTIME_CANDIDATES


def test_runtime_bound_receipt_and_census_agree():
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    assert receipt["SUCCESS_CRITERION"] == "RUNTIME_CALL_PATH=BOUND"
    assert receipt["RUNTIME_CALL_PATH"] == "BOUND"
    assert receipt["RUNTIME_CALLER"]["cli"] == RUNTIME
    assert (ROOT / RUNTIME).is_file() and (ROOT / RUNTIME_LIBRARY).is_file()
    assert receipt["NEW_DAEMON"] == "NO"
    assert receipt["LEAD_PROMOTION"] == "NO" and receipt["CONTACT_AUTHORITY"] == "NO"
    [history] = receipt["HISTORY"]
    assert history["RUNTIME_CALL_PATH"] == "CALLER_ABSENT" and history["BASE_SHA"].startswith("e93f2ac")
    assert set(receipt["SEAMS_CLOSED"]) == set(history["MISSING_SEAMS"])
    census = CENSUS.read_text(encoding="utf-8").splitlines()
    assert census[0].startswith("candidate\t")
    classes = [line.split("\t")[-1] for line in census[1:]]
    assert classes.count("BOUND_RUNTIME") == 1
    assert {"BOUND_LIBRARY", "BOUND_HARNESS", "BOUND_CLI_NOT_RUNTIME", "BOUND_CATALOG",
            "BOUND_CONTEXT_PRODUCER"} <= set(classes)
    assert "MISSING_SEAM" not in {line.split("\t")[2] for line in census[1:]}
    assert classes.count("CALLER_ABSENT") >= 12   # the named candidates stay unbound
    for rel in history["SEARCHED_NOT_BOUND"]:
        if rel == "recipe_engine/founder_runtime":
            assert (ROOT / rel).is_dir()
            continue
        assert (ROOT / rel).is_file(), rel
