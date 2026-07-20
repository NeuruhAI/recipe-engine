# Founder Runtime P2-A Architecture

**Status:** P2-A candidate — adapter foundation only.  
**Not promoted. P2-B live adapters not started. Mutation not authorized.**

## Scope
Typed AdapterMode, Protocols, result envelopes, identity/provenance, registry/factory,
fixture retrofit, AXON mutation hard stop.

## Non-scope
No live network calls, no real service connections, no production persistence,
no AXON mutation implementation, no P2-B sequence execution.

## Package layout
- `adapters/base.py` — AdapterMode, MutationClass
- `adapters/envelope.py` — AdapterResult
- `adapters/identity.py` — AdapterIdentity, DependencyIdentity, Provenance
- `adapters/protocols.py` — Protocol ABCs
- `adapters/config.py` — AdapterConfig (explicit, no process-environment)
- `adapters/registry.py` — AdapterRegistry
- `adapters/axon_mutation.py` — hard stop
- `adapters/stubs.py` — disabled/unavailable
- fixture_* retrofitted with identity + result envelopes
