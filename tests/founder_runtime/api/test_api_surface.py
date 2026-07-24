"""OpenAPI contract, SSE stream, adapter matrix, no-silent-fallback proof."""

from __future__ import annotations

import tempfile

from recipe_engine.founder_runtime.api.adapters import (
    DISABLED_BY_POLICY, FIXTURE, LIVE_READ_ONLY, UNAVAILABLE, organ_status,
)
from recipe_engine.founder_runtime.api.openapi import generate


def test_openapi_machine_readable():
    spec = generate()
    assert spec["openapi"].startswith("3.")
    paths = spec["paths"]
    for required in ("/v1/health", "/v1/readiness", "/v1/missions",
                     "/v1/missions/{mission_id}", "/v1/missions/{mission_id}/decision-packet",
                     "/v1/missions/{mission_id}/plan", "/v1/missions/{mission_id}/approve",
                     "/v1/missions/{mission_id}/reject", "/v1/missions/{mission_id}/pause",
                     "/v1/missions/{mission_id}/resume", "/v1/missions/{mission_id}/cancel",
                     "/v1/missions/{mission_id}/events", "/v1/missions/{mission_id}/events/stream",
                     "/v1/missions/{mission_id}/receipts", "/v1/missions/{mission_id}/recover",
                     "/v1/adapters/status"):
        assert required in paths, f"missing path {required}"


def test_sse_stream_emits_all_events(running_server):
    client, _ = running_server
    res = client.create_mission(objective="status", idempotency_key="sse1")
    mid = res["envelope"]["mission_id"]
    via_http = client.events(mid)["count"]
    streamed = list(client.stream_events(mid))
    # last SSE frame is the end marker; the rest are events
    assert len(streamed) == via_http + 1
    assert streamed[-1]["status"] == "stream_complete"


def test_adapter_matrix_classifications():
    status = organ_status(probe_live=False)
    classifications = {m["classification"] for m in status["matrix"]}
    assert classifications <= {LIVE_READ_ONLY, FIXTURE, UNAVAILABLE, DISABLED_BY_POLICY}
    # structurally disabled entries are always present and honest
    disabled = [m["service"] for m in status["matrix"] if m["classification"] == DISABLED_BY_POLICY]
    assert "axon_mutation" in disabled
    assert "workers_dispatch" in disabled
    assert "external_action" in disabled


def test_no_silent_live_to_fixture_fallback():
    # When live probing is off/unavailable, organs are UNAVAILABLE — never
    # silently reclassified as fixture.
    status = organ_status(probe_live=False)
    live_organs = [m for m in status["matrix"]
                   if m["service"] in ("mother", "iar", "aegis", "governance", "axon_gateway")]
    assert all(m["classification"] == UNAVAILABLE for m in live_organs)
    # fixtures are explicitly declared FIXTURE, not a fallback
    fixtures = [m for m in status["matrix"] if m["classification"] == FIXTURE]
    assert all(m["service"].startswith("fixture_") for m in fixtures)


def test_unavailable_organ_does_not_break_api(service):
    # organ probing is independent of the mission pipeline; API stays useful
    res = service.create_mission(objective="status", idempotency_key="org1")
    assert res["envelope"]["state"] == "AWAITING_APPROVAL"
