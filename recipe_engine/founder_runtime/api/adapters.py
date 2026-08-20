"""Organ status probe with honest classification.

Every adapter is classified exactly one of:
  LIVE_READ_ONLY, FIXTURE, UNAVAILABLE, DISABLED_BY_POLICY.

No silent live-to-fixture fallback: a live probe that fails is UNAVAILABLE and
the API reports it; fixture substitution happens only where the mission pipeline
is explicitly fixture-mode by policy.
"""

from __future__ import annotations

from typing import Any

LIVE_READ_ONLY = "LIVE_READ_ONLY"
FIXTURE = "FIXTURE"
UNAVAILABLE = "UNAVAILABLE"
DISABLED_BY_POLICY = "DISABLED_BY_POLICY"


def default_probe(service_id: str, host: str, port: int) -> dict[str, Any]:
    """Pure default: no socket I/O in the kernel boundary. Reports UNAVAILABLE.

    The serving process injects a real loopback prober (see serve.py); the
    kernel-reachable module itself performs no network I/O.
    """
    return {
        "service": service_id,
        "classification": UNAVAILABLE,
        "reachable": False,
        "detail": {"probe": "not_injected"},
    }


# Loopback organs promoted in P2-B (read-only). We probe; we never assume.
_LIVE_CANDIDATES = (
    ("mother", "127.0.0.1", 8077),
    ("iar", "127.0.0.1", 8090),
    ("aegis", "127.0.0.1", 8055),
    ("governance", "127.0.0.1", 8848),
    ("axon_gateway", "127.0.0.1", 8300),
)


def organ_status(*, probe_live: bool = True, prober=None) -> dict[str, Any]:
    """Return the adapter matrix with honest, evidence-backed classification."""
    matrix: list[dict[str, Any]] = []
    for service_id, host, port in _LIVE_CANDIDATES:
        if probe_live:
            probe = prober or default_probe
            matrix.append(probe(service_id, host, port))
        else:
            matrix.append({
                "service": service_id,
                "classification": UNAVAILABLE,
                "reachable": False,
                "detail": {"probe": "disabled"},
            })
    # Structurally disabled by policy.
    matrix.append({
        "service": "axon_mutation",
        "classification": DISABLED_BY_POLICY,
        "reachable": False,
        "detail": {"policy": "double-walled hard stop; mutation not authorized"},
    })
    matrix.append({
        "service": "workers_dispatch",
        "classification": DISABLED_BY_POLICY,
        "reachable": False,
        "detail": {"policy": "Workers not connected to missions in V1"},
    })
    matrix.append({
        "service": "external_action",
        "classification": DISABLED_BY_POLICY,
        "reachable": False,
        "detail": {"policy": "external send/spend/deploy structurally disabled"},
    })
    # Fixture adapters used by the mission pipeline (explicit, declared).
    for svc in ("fixture_mother", "fixture_github", "fixture_governance", "fixture_axon", "fixture_iar"):
        matrix.append({
            "service": svc,
            "classification": FIXTURE,
            "reachable": True,
            "detail": {"mode": "fixture", "note": "declared fixture; no live fallback"},
        })
    live_count = sum(1 for m in matrix if m["classification"] == LIVE_READ_ONLY)
    return {
        "matrix": matrix,
        "summary": {
            "live_read_only": live_count,
            "fixture": sum(1 for m in matrix if m["classification"] == FIXTURE),
            "unavailable": sum(1 for m in matrix if m["classification"] == UNAVAILABLE),
            "disabled_by_policy": sum(1 for m in matrix if m["classification"] == DISABLED_BY_POLICY),
        },
    }
