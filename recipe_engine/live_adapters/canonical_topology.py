"""Canonical promoted-organ topology for the P2-B live read-only path.

Pins the fixed loopback endpoint allowlist and identity SHAs to the canonical
promoted organs, and rejects any deviation (non-loopback, obsolete port, endpoint
override, wrong service SHA).  Mother and AEGIS endpoints are supplied from their
sealed GREEN compatibility audits; every other organ port is fixed here.

This is a composition-root helper (it may read the sealed audit files to extract
the reviewed Mother/AEGIS endpoints); the adapters it configures never read files
or process environment.  It does NOT weaken loopback confinement, allow
caller-controlled endpoints, add fixture fallback, authorize mutation, authorize
OpenRouter, or change the eight-organ order.
"""

from __future__ import annotations

import re
from pathlib import Path

from recipe_engine.live_adapters.settings import LiveAdapterSettings, LiveEndpoint, LiveConfigError, RepoRoot

# Fixed canonical loopback ports for the promoted organs (Mother/AEGIS come from
# their reviewed GREEN audits and are validated, not fixed here).
CANONICAL_PORTS = {
    "qdrant": 6333,
    "iar": 8090,          # canonical local IAR
    "governance": 8848,   # promoted UP-2 service
    "axon": 8300,         # authenticated promoted gateway (start.ts: AXON_PORT ?? '8300')
}
# Obsolete/legacy AXON ports that must never be accepted as the gateway.
OBSOLETE_AXON_PORTS = frozenset({8080, 8788})

# Promoted identities.
CANONICAL_AXON_SHA = "f3a94d1a68ac6b07525e962692901411bd43e6bf"
CANONICAL_AXON_VERSION = "2.4.0-p1.1"
CANONICAL_GOVERNANCE_SHA = "8e03131b628ba611d038551a47741623c3a80f4b"
CANONICAL_GOVERNANCE_SERVICE = "autonomous-governance-core"

# The canonical eight-organ order (unchanged).
CANONICAL_ORGAN_ORDER = ("github", "vault", "qdrant", "mother", "iar", "aegis", "governance", "axon_readonly")


class TopologyError(LiveConfigError):
    """Raised when a topology deviates from the canonical promoted organ set."""


def _require_loopback_port(service: str, endpoint: LiveEndpoint) -> None:
    if endpoint.host not in ("127.0.0.1", "::1", "localhost"):
        raise TopologyError(f"{service} endpoint is not loopback: {endpoint.host}")
    if endpoint.scheme != "http":
        raise TopologyError(f"{service} endpoint must be plain-http loopback")


def verify_canonical_topology(settings: LiveAdapterSettings) -> None:
    """Fail closed unless ``settings`` matches the canonical promoted topology.

    Rejects: non-loopback endpoints, endpoint overrides of a fixed organ port,
    the obsolete AXON port(s), and a wrong AXON/Governance identity SHA.
    """
    eps = settings.endpoint_map()
    for service in ("qdrant", "mother", "iar", "aegis", "governance", "axon"):
        if service not in eps:
            raise TopologyError(f"canonical topology is missing the {service} endpoint")
        _require_loopback_port(service, eps[service])

    # Fixed organ ports may not be overridden by a caller.
    for service, port in CANONICAL_PORTS.items():
        if eps[service].port != port:
            raise TopologyError(
                f"{service} endpoint override rejected: expected canonical port {port}, got {eps[service].port}"
            )
    # The AXON gateway must be the canonical port, never an obsolete one.
    if eps["axon"].port in OBSOLETE_AXON_PORTS:
        raise TopologyError(f"obsolete AXON port rejected: {eps['axon'].port}")

    # Mother/AEGIS come from their GREEN audits: loopback + a plausible port.
    for service in ("mother", "aegis"):
        p = eps[service].port
        if isinstance(p, bool) or not isinstance(p, int) or not (0 < p < 65536):
            raise TopologyError(f"{service} endpoint port is invalid: {p}")

    # Promoted identity SHAs.
    if settings.axon_source_sha != CANONICAL_AXON_SHA:
        raise TopologyError("wrong AXON service SHA (expected promoted f3a94d1)")
    if settings.governance_source_sha != CANONICAL_GOVERNANCE_SHA:
        raise TopologyError("wrong Governance service SHA (expected promoted 8e03131b)")


def build_canonical_topology(
    *,
    repo_roots: tuple[RepoRoot, ...],
    mother_port: int,
    aegis_port: int,
    host_recovery_audit_base: str | None = None,
    recipe_engine_sha: str = "",
    axon_secret: str | None = None,
) -> LiveAdapterSettings:
    """Build the pinned canonical topology.  Fixed organ ports are non-overridable;
    Mother/AEGIS ports come from their reviewed GREEN audits."""
    endpoints = (
        LiveEndpoint("qdrant", "127.0.0.1", CANONICAL_PORTS["qdrant"], "loopback"),
        LiveEndpoint("mother", "127.0.0.1", int(mother_port), "loopback"),
        LiveEndpoint("iar", "127.0.0.1", CANONICAL_PORTS["iar"], "loopback"),
        LiveEndpoint("aegis", "127.0.0.1", int(aegis_port), "loopback"),
        LiveEndpoint("governance", "127.0.0.1", CANONICAL_PORTS["governance"], "loopback"),
        LiveEndpoint(
            "axon", "127.0.0.1", CANONICAL_PORTS["axon"], "loopback",
            auth_header="x-axon-secret" if axon_secret else None,
            auth_secret=axon_secret,
        ),
    )
    settings = LiveAdapterSettings(
        repo_roots=repo_roots,
        endpoints=endpoints,
        axon_source_sha=CANONICAL_AXON_SHA,
        governance_source_sha=CANONICAL_GOVERNANCE_SHA,
        host_recovery_audit_base=host_recovery_audit_base,
        recipe_engine_sha=recipe_engine_sha,
    )
    verify_canonical_topology(settings)
    return settings


# --- audit endpoint extraction (composition-root read of the sealed audits) ---
_PORT_RE = re.compile(r"127\.0\.0\.1:(\d{2,5})")


def read_endpoint_port_from_audit(audit_dir: str | Path, service: str) -> int:
    """Extract the exact reviewed loopback port for ``service`` from its sealed
    GREEN audit.  Looks for a 127.0.0.1:<port> loopback endpoint in the audit's
    verdict / endpoint deliverables.  Raises TopologyError if none is found."""
    audit = Path(audit_dir)
    candidates = [
        "FINAL_VERDICT.md", "ENDPOINT.md", "ENDPOINT_CONTRACT.md", "CANONICAL_ENDPOINT.md",
        f"{service.upper()}_ENDPOINT.md", "READONLY_CONTRACT.md", "CONTRACT.md", "HTTP_CONTRACT.md",
    ]
    seen: set[int] = set()
    for name in candidates:
        f = audit / name
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _PORT_RE.finditer(text):
            seen.add(int(m.group(1)))
    # If exactly one loopback port is referenced, that is the endpoint.
    if len(seen) == 1:
        return next(iter(seen))
    # Otherwise scan every file in the audit for a single consistent port.
    if not seen:
        for f in sorted(audit.glob("*")):
            if f.is_file():
                try:
                    for m in _PORT_RE.finditer(f.read_text(encoding="utf-8", errors="replace")):
                        seen.add(int(m.group(1)))
                except OSError:
                    continue
    if len(seen) == 1:
        return next(iter(seen))
    raise TopologyError(
        f"could not determine a single canonical {service} loopback port from {audit} (found {sorted(seen)})"
    )
