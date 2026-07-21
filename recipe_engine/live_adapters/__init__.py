"""Founder Runtime P2-B — LIVE read-only adapter implementations.

This package is the LIVE-I/O half of the P2-A.1 adapter framework.  It is a
deliberate confinement boundary, NOT a second framework:

  * ``recipe_engine/founder_runtime/adapters/`` stays pure contracts + fixtures
    and is AST-scanned to forbid ``socket``/``subprocess``/``urllib``/``http``/
    ``requests`` and ``os.environ`` (tests ``ConfinementTests`` /
    ``SafetyRegressionTests`` / ``SecurityTests``).
  * This package performs the actual bounded, read-only I/O (local ``git`` and
    loopback HTTP).  It imports the founder_runtime contracts ONE WAY
    (live_adapters -> founder_runtime).  No founder_runtime module imports this
    package, so the transitive security graph seeded from ``founder_runtime.*``
    never reaches — and never has to forgive — the I/O here.

Every live adapter here implements the SAME ``adapters.protocols`` Protocols,
returns the SAME ``AdapterResult`` envelope via ``make_result``, uses the SAME
``timeouts`` policy table and ``contract_validation`` validators, and registers
into the SAME ``AdapterRegistry`` via :func:`registration.register_live_adapters`.

P2-B invariants (per adapter, enforced in code):
  read-only classification; explicit operation id; mutation-shaped input
  rejection; bounded operation-specific timeout; canonical request/response
  hashes; exact source/provenance identity; LIVE/UNAVAILABLE/DEGRADED/DENIED
  distinction; fail-closed; NO live-to-fixture fallback; no caller-controlled
  filesystem paths; no arbitrary endpoint injection; loopback/root binding;
  deterministic receipts; credential redaction; previous state preserved on
  failure; completion is NEVER claimed from an adapter response alone.
"""

from __future__ import annotations

__all__ = ["LIVE_POLICY_VERSION"]

LIVE_POLICY_VERSION = "p2b.live.v1"
