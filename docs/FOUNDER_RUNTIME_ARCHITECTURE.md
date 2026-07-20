# Founder Runtime Architecture (P1 Candidate)

**Status:** P1 candidate — fixture adapters only.  
**Not:** live execution, production persistence, Cockpit integration, Mohnal/Vision, autonomous production.

## Position

Founder Runtime is the T5 mission orchestration layer inside `NeuruhAI/recipe-engine`.

It compiles founder intent into durable missions, plans via P0 autonomy core, records events, recovers, and verifies completion through receipts.

## Operator corrections vs PDF

| Topic | PDF | P1 implementation |
|-------|-----|-------------------|
| Final policy | AEGIS broad ownership | `autonomous-governance-core` final authority; IAR/AEGIS opinion only |
| Mission store | Supabase permanent | Local SQLite + interface; future governed adapter TBD |
| Language | TypeScript tree | Python, matching P0 |
| Custody | — | Stacked branch on P0 SHA; P0 immutable |
| Adapters | Mixed | Fixture only; mode explicit |

## Layers

- **contracts/** — versioned mission objects
- **state/** — explicit state machine
- **persistence/** — SQLite event store
- **runtime/** — mission kernel, locks, recovery, completion
- **adapters/** — fixture IAR, Governance, Mother, AXON, Vault, Qdrant, GitHub, Cockpit
- **vertical_slices/** — system-status read-only slice
- **autonomy/** (P0) — scenario collapse, plan proposals, reconciliation

## Non-claims

- No live AXON/IAR/Governance/Mother/Vault/Qdrant calls
- No production DB, no ports, no deploys
- No second organism

## P2-A
Adapter foundation landed on branch feat/founder-runtime-p2a-adapter-foundation. Fixture-only runtime path unchanged. Live adapters deferred to P2-B.
