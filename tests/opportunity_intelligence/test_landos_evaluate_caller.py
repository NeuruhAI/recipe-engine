"""Caller court: recipe-engine must actually call LandOS evaluate.

These tests do not run landos-engine.  They prove the recipe-engine adapter
fails closed when evaluate is skipped, mocked as success without the owned
result contract, or when UNKNOWN is collapsed to a known 0/ABSENT.
"""

from __future__ import annotations

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any, Mapping

import json
import pytest

from recipe_engine.closed_loop.identity import digest
from recipe_engine.opportunity_intelligence import (
    CANONICAL_RECIPE_ID,
    CANONICAL_RECIPE_VERSION,
    DECISION_RECEIPT_VERSION,
    EVALUATE_PATH,
    LAND_ACQUISITION_RECIPE_V1_DIGEST,
    LandosInProcessEvaluator,
    LandosOpportunityClient,
    OpportunityEvaluateError,
    RECEIPT_CONTRACT_VERSION,
    RESULT_CONTRACT_VERSION,
    SCORE_VERSION,
    SkippedEvaluator,
    fixture_by_id,
    request_opportunity_evaluation,
    validate_input,
    validate_result,
)


def _unknown_tax_payload() -> dict[str, Any]:
    payload = deepcopy(fixture_by_id("vacant_land_stacked_distress").payload)
    payload["fixture_id"] = "unknown_tax_delinquency_not_absent"
    payload["signals"] = [
        {
            "signal_id": "u-tax",
            "signal_type": "tax_delinquent",
            "state": "UNKNOWN",
            "confidence": "UNKNOWN",
            "evidence_refs": ["fixture:unknown-tax"],
            "source_families": ["COUNTY_TAX_ROLL"],
            "observed_at": payload["as_of"],
            "note": "Synthetic UNKNOWN court; not a live finding.",
        }
    ]
    population = payload["property_population"]
    population["signal_observation_count"] = 1
    population["unresolved_signal_count"] = 0
    population["signal_amplification"] = 1.0
    population["identity_kinds"] = {"parcel": 1}
    return validate_input(payload)


def _contract_result(
    payload: Mapping[str, Any],
    *,
    tax_state: str = "UNKNOWN",
    recipe_id: str | None = None,
    recipe_version: str | None = None,
    config_digest: str | None = None,
) -> dict[str, Any]:
    """Hand-built result document.  Not LandOS scoring."""
    recipe = payload["recipe"]
    bound_id = recipe_id or recipe["recipe_id"]
    bound_version = recipe_version or recipe["version"]
    bound_digest = config_digest or recipe["config_digest"]
    genome = {
        "subject": dict(payload["subject"]),
        "recipe_ref": {
            "recipe_id": bound_id,
            "version": bound_version,
            "config_digest": bound_digest,
            "score_version": recipe["score_version"],
        },
    }
    genome_digest = digest(genome)
    factor = {
        "factor_id": "tax_delinquent",
        "label": "verified tax delinquency",
        "state": tax_state,
        "delta": 0,
        "points_if_present": 22,
        "points_if_absent": 0,
        "material": True,
        "why": "All supplied observations explicitly state UNKNOWN.",
    }
    unknown_factors = []
    if tax_state == "UNKNOWN":
        unknown_factors.append({
            "factor_id": "tax_delinquent",
            "state": "UNKNOWN",
            "possible_delta": {"minimum": 0, "maximum": 22},
            "why_unknown": factor["why"],
        })
    result = {
        "schema_version": RESULT_CONTRACT_VERSION,
        "subject_id": payload["subject"]["subject_id"],
        "recipe": {
            "schema_version": recipe["schema_version"],
            "recipe_id": bound_id,
            "version": bound_version,
            "config_digest": bound_digest,
            "score_version": SCORE_VERSION,
        },
        "opportunity_genome": genome,
        "caller_top_level_extensions": {},
        "genome_digest": genome_digest,
        "asset_classification": {
            "asset_class": "vacant_land",
            "state": "KNOWN",
        },
        "stacked_signals": {
            "observation_count": len(payload["signals"]),
            "raw_observations": list(payload["signals"]),
            "normalized_factors": [factor],
            "unrecognized_observations": [],
        },
        "property_population": deepcopy(payload["property_population"]),
        "opportunity_score": {
            "score_version": SCORE_VERSION,
            "formula": "fixture: named deltas are owned by LandOS, not this caller",
            "score": 0,
            "components": [
                {
                    "factor_id": factor["factor_id"],
                    "state": factor["state"],
                    "delta": factor["delta"],
                }
            ],
        },
        "confidence_score": {
            "score": 0,
            "formula": "fixture: confidence is owned by LandOS",
            "components": [],
        },
        "readiness": {"authority_readiness": {"state": "UNKNOWN"}},
        "research_results": {"raw_attempts": []},
        "next_best_source": {"source_id": "COUNTY_TAX_ROLL", "mode": "AUTOMATIC"},
        "next_best_action": {"action": "ESTABLISH_LEGAL_AUTHORITY", "route": "AUTHORITY_RESEARCH"},
        "priority": {"priority": "P3_RESEARCH"},
        "routing": {"route": "AUTHORITY_RESEARCH", "priority": "P3_RESEARCH"},
        "explanation": {
            "why": ["UNKNOWN tax delinquency must remain unknown."],
            "positive_factors": [],
            "negative_factors": [],
            "unknown_material_factors": unknown_factors,
        },
        "safety": {
            "opaque_ml": False,
            "seller_contact_performed": False,
            "offer_sent_or_made": False,
            "execution_authority_granted": False,
        },
    }
    receipt_body = {
        "schema_version": RECEIPT_CONTRACT_VERSION,
        "input_digest": digest(payload),
        "recipe_digest": bound_digest,
        "genome_digest": genome_digest,
        "result_digest": digest({k: v for k, v in result.items()}),
    }
    result["receipt"] = {
        **receipt_body,
        "receipt_sha256": digest(receipt_body),
    }
    return result


class _FakeLandos:
    def __init__(self, result: Mapping[str, Any] | None = None, payload_holder: list | None = None):
        self.result = result
        self.calls = 0
        self.payload_holder = payload_holder if payload_holder is not None else []

    def evaluate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls += 1
        self.payload_holder.append(payload)
        if self.result is None:
            return _contract_result(payload)
        return deepcopy(dict(self.result))


def test_skipped_evaluate_fails_closed():
    payload = _unknown_tax_payload()
    with pytest.raises(OpportunityEvaluateError, match="skipped"):
        request_opportunity_evaluation(payload, None)
    with pytest.raises(OpportunityEvaluateError, match="skipped"):
        request_opportunity_evaluation(payload, SkippedEvaluator())
    with pytest.raises(OpportunityEvaluateError, match="missing"):
        LandosInProcessEvaluator(None)


def test_http_transport_failure_is_not_score_zero():
    payload = _unknown_tax_payload()
    client = LandosOpportunityClient(
        base_url="http://127.0.0.1:1",
        secret="fixture-secret",
    )
    with pytest.raises(OpportunityEvaluateError, match="transport failed"):
        request_opportunity_evaluation(payload, client)


def test_mocked_success_without_contract_fails_closed():
    payload = _unknown_tax_payload()

    class MockOk:
        def evaluate(self, _payload: Mapping[str, Any]) -> Mapping[str, Any]:
            return {"ok": True, "score": 0, "engine_called": 1}

    with pytest.raises(OpportunityEvaluateError, match="non-contract success"):
        request_opportunity_evaluation(payload, MockOk())


def test_unknown_collapsed_to_absent_zero_fails_closed():
    payload = _unknown_tax_payload()
    collapsed = _contract_result(payload, tax_state="ABSENT")
    with pytest.raises(OpportunityEvaluateError, match="collapsed to ABSENT"):
        request_opportunity_evaluation(payload, _FakeLandos(collapsed))


def test_contract_faithful_unknown_binds_recipe_decision_receipt():
    payload = _unknown_tax_payload()
    fake = _FakeLandos()
    decision = request_opportunity_evaluation(payload, fake)
    assert fake.calls == 1
    assert decision.receipt.evaluate_called is True
    assert decision.receipt.schema_version == DECISION_RECEIPT_VERSION
    assert decision.receipt.recipe_id == CANONICAL_RECIPE_ID
    assert decision.receipt.recipe_version == CANONICAL_RECIPE_VERSION
    assert decision.receipt.config_digest == LAND_ACQUISITION_RECIPE_V1_DIGEST
    assert decision.receipt.score_version == SCORE_VERSION
    assert decision.receipt.subject_id == payload["subject"]["subject_id"]
    assert decision.result["stacked_signals"]["normalized_factors"][0]["state"] == "UNKNOWN"
    assert decision.result["opportunity_score"]["score"] == 0
    assert any(
        item["factor_id"] == "tax_delinquent"
        for item in decision.result["explanation"]["unknown_material_factors"]
    )


def test_wrong_recipe_version_cannot_be_credited():
    payload = _unknown_tax_payload()
    mismatched = _contract_result(payload, recipe_version="9.9.9")
    with pytest.raises(OpportunityEvaluateError, match="recipe version"):
        request_opportunity_evaluation(payload, _FakeLandos(mismatched))

    digest_mismatch = _contract_result(
        payload,
        config_digest="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    with pytest.raises(OpportunityEvaluateError, match="config_digest"):
        request_opportunity_evaluation(payload, _FakeLandos(digest_mismatch))


def test_validate_result_rejects_ok_true():
    with pytest.raises(Exception, match="schema_version"):
        validate_result({"ok": True, "score": 0})


def test_http_client_posts_documented_evaluate_path_and_secret():
    payload = _unknown_tax_payload()
    seen: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            seen["path"] = self.path
            seen["secret"] = self.headers.get("X-LANDOS-SECRET")
            length = int(self.headers.get("Content-Length") or "0")
            body = json.loads(self.rfile.read(length).decode("utf-8") or "null")
            seen["body"] = body
            if self.path != EVALUATE_PATH:
                self.send_response(404)
                self.end_headers()
                return
            if self.headers.get("X-LANDOS-SECRET") != "fixture-secret":
                self.send_response(401)
                self.end_headers()
                return
            raw = json.dumps(_contract_result(body)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        client = LandosOpportunityClient(
            base_url=f"http://{host}:{port}",
            secret="fixture-secret",
        )
        decision = request_opportunity_evaluation(payload, client)
        assert seen["path"] == EVALUATE_PATH
        assert seen["secret"] == "fixture-secret"
        assert seen["body"]["recipe"]["config_digest"] == LAND_ACQUISITION_RECIPE_V1_DIGEST
        assert decision.receipt.evaluate_called is True
        assert decision.receipt.config_digest == LAND_ACQUISITION_RECIPE_V1_DIGEST
    finally:
        server.shutdown()
        server.server_close()


def test_old_identity_evaluate_path_is_not_this_seam():
    payload = _unknown_tax_payload()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(200 if self.path == "/evaluate" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"node_id":"not-this-seam","ok":true}')

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        client = LandosOpportunityClient(
            base_url=f"http://{host}:{port}",
            secret="fixture-secret",
        )
        with pytest.raises(OpportunityEvaluateError, match=EVALUATE_PATH):
            request_opportunity_evaluation(payload, client)
    finally:
        server.shutdown()
        server.server_close()
