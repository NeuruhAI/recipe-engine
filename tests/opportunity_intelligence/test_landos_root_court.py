"""Live-shape court against landos-engine ``evaluate_opportunity`` at 1779abf.

Skip is UNWIRED, not PASS. A present checkout at the wrong evaluate blob is FAIL.
HTTP evaluate is a separate seam and is not mocked into PASS here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from recipe_engine.opportunity_intelligence import (
    CANONICAL_RECIPE_ID,
    CANONICAL_RECIPE_VERSION,
    LAND_ACQUISITION_RECIPE_V1_DIGEST,
    LandosInProcessEvaluator,
    LandosOpportunityClient,
    OpportunityEvaluateError,
    request_opportunity_evaluation,
)
from tests.opportunity_intelligence.test_landos_evaluate_caller import (
    _unknown_tax_payload,
)


LANDOS_ENGINE_COMMIT = "1779abf265814f5595e1153a531c4a0ee09a0faa"
LANDOS_OPPORTUNITY_INTELLIGENCE_BLOB = "bc99185b492bbd7328ee9ceb3a9af1b94ad303f6"
LANDOS_RECEIPT_BLOB = "e1ac93ea0d7a53378ddf1da730942df45fa0a545"
LANDOS_ROOT_VERIFICATION_SHA256 = (
    "ab75e4dfb0e3a0a396950c43088c826b4e6bca91e1972d997f596bc88afd7eb3"
)
UNKNOWN_TAX_LANDOS_RECEIPT_SHA256 = (
    "a5f24ae5363f4448591c1377fb39d71a55b3bd573871327e62bf8d9776cba082"
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COURT_JSON = REPO_ROOT / "docs" / "courts" / "landos-evaluate-1779abf.json"
SIBLING_ROOT = Path("/home/ubuntu/neuruh-siblings/landos-engine")


def _landos_root() -> Path | None:
    env = (os.environ.get("LANDOS_ROOT") or "").strip()
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates.append(SIBLING_ROOT)
    candidates.append(REPO_ROOT.parent / "landos-engine")
    for root in candidates:
        if (root / "app" / "opportunity_intelligence.py").is_file():
            return root.resolve()
    return None


def _blob_sha(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def _require_landos_root() -> Path:
    root = _landos_root()
    if root is None:
        pytest.skip(
            "UNWIRED: no landos-engine checkout with app/opportunity_intelligence.py "
            f"(looked at LANDOS_ROOT, {SIBLING_ROOT}, ../landos-engine). "
            "Not a PASS."
        )
    return root


def _assert_evaluate_blobs(root: Path) -> None:
    oi_blob = _blob_sha(root / "app" / "opportunity_intelligence.py")
    receipt_blob = _blob_sha(root / "app" / "receipt.py")
    if oi_blob != LANDOS_OPPORTUNITY_INTELLIGENCE_BLOB:
        raise AssertionError(
            "FAIL closed: landos evaluate blob "
            f"{oi_blob} != {LANDOS_OPPORTUNITY_INTELLIGENCE_BLOB} "
            f"(required commit {LANDOS_ENGINE_COMMIT})"
        )
    if receipt_blob != LANDOS_RECEIPT_BLOB:
        raise AssertionError(
            "FAIL closed: landos receipt blob "
            f"{receipt_blob} != {LANDOS_RECEIPT_BLOB} "
            f"(required commit {LANDOS_ENGINE_COMMIT})"
        )
    git_dir = root / ".git"
    if git_dir.exists():
        head = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        if head != LANDOS_ENGINE_COMMIT:
            raise AssertionError(
                "FAIL closed: landos-engine HEAD "
                f"{head} != {LANDOS_ENGINE_COMMIT}"
            )


def _import_landos(root: Path) -> Any:
    resolved = str(root)
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
    from app import opportunity_intelligence as landos  # noqa: PLC0415

    return landos


def test_landos_in_process_evaluator_refuses_missing_evaluate():
    with pytest.raises(OpportunityEvaluateError, match="missing"):
        LandosInProcessEvaluator(None)


def test_committed_landos_root_receipt_pins_1779abf_sha():
    committed = json.loads(COURT_JSON.read_text())
    assert committed["verification_sha256"] == LANDOS_ROOT_VERIFICATION_SHA256
    assert committed["validator_parity"]["status"] == "PASSED"
    assert committed["fixture_count"] == 7
    assert committed["source_factory_actual_acceptance"]["status"] == "NOT_REQUESTED"
    assert committed["recipe_digest"] == LAND_ACQUISITION_RECIPE_V1_DIGEST


def test_landos_root_court_runs_evaluate_at_1779abf():
    root = _require_landos_root()
    _assert_evaluate_blobs(root)
    raw = subprocess.check_output(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "verify_opportunity_intelligence.py"),
            "--landos-root",
            str(root),
        ],
        text=True,
    )
    receipt = json.loads(raw)
    assert receipt["validator_parity"]["status"] == "PASSED"
    assert receipt["fixture_count"] == 7
    assert receipt["source_factory_actual_acceptance"]["status"] == "NOT_REQUESTED"
    assert receipt["verification_sha256"] == LANDOS_ROOT_VERIFICATION_SHA256
    committed = json.loads(COURT_JSON.read_text())
    assert receipt["verification_sha256"] == committed["verification_sha256"]


def test_unknown_is_not_absent_zero_against_real_landos_evaluate():
    root = _require_landos_root()
    _assert_evaluate_blobs(root)
    landos = _import_landos(root)
    payload = _unknown_tax_payload()
    evaluator = LandosInProcessEvaluator(landos.evaluate_opportunity)
    decision = request_opportunity_evaluation(payload, evaluator)
    factors = {
        str(factor["factor_id"]): factor
        for factor in decision.result["stacked_signals"]["normalized_factors"]
    }
    tax = factors["tax_delinquent"]
    assert tax["state"] == "UNKNOWN"
    assert tax.get("delta", 0) == 0
    assert any(
        item["factor_id"] == "tax_delinquent"
        for item in decision.result["explanation"]["unknown_material_factors"]
    )
    assert tax["state"] != "ABSENT"
    assert decision.receipt.evaluate_called is True
    assert decision.receipt.recipe_id == CANONICAL_RECIPE_ID
    assert decision.receipt.recipe_version == CANONICAL_RECIPE_VERSION
    assert decision.receipt.config_digest == LAND_ACQUISITION_RECIPE_V1_DIGEST
    assert (
        decision.receipt.landos_receipt_sha256 == UNKNOWN_TAX_LANDOS_RECEIPT_SHA256
    )


def test_live_http_evaluate_is_not_invented_pass():
    """HTTP evaluate is UNWIRED unless a real LandOS process answers. Never mock it."""
    if os.environ.get("LANDOS_LIVE_HTTP") != "1":
        pytest.skip(
            "UNWIRED: LIVE HTTP POST /v1/opportunity-intelligence/evaluate was not "
            "invokable (no listener on 127.0.0.1:8400/8000; LANDOS_SECRET unset; "
            "landos app.main not in the evaluate-only checkout). Not a PASS."
        )
    base = (os.environ.get("LANDOS_ENGINE_URL") or "http://127.0.0.1:8400").rstrip("/")
    secret = os.environ.get("LANDOS_SECRET") or ""
    client = LandosOpportunityClient(base_url=base, secret=secret)
    decision = request_opportunity_evaluation(_unknown_tax_payload(), client)
    assert decision.receipt.evaluate_called is True
    assert decision.receipt.config_digest == LAND_ACQUISITION_RECIPE_V1_DIGEST
    tax = next(
        factor
        for factor in decision.result["stacked_signals"]["normalized_factors"]
        if factor["factor_id"] == "tax_delinquent"
    )
    assert tax["state"] == "UNKNOWN"
