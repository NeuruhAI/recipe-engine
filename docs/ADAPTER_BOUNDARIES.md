# Adapter Boundaries

All P1 adapters require `mode="fixture"`. Invalid mode fails closed. No silent live fallback.

| Adapter | Behavior |
|---------|----------|
| IAR | Schema-valid fixture interpretation |
| Governance | Fixture policy decision |
| Mother | Health/provenance fixtures; does not plan |
| AXON | Pre-authored read-only receipts; no shell |
| Vault/Qdrant | Opaque fixture docs; no live retrieval |
| GitHub | Repo state fixtures; no write |
| Cockpit | Status fixture; no UI mutation |
