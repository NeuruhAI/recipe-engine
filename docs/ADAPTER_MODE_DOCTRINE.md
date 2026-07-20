# Adapter Mode Doctrine

| Mode | Behavior |
|------|----------|
| fixture | Deterministic local evidence; may not claim live provenance; cannot authorize mutation |
| live | Real observation; explicit config; no fixture fallback; missing impl fails closed |
| disabled | Deliberately off; no call; explicit receipt |
| unavailable | Dependency missing; no fixture substitution; bounded failure |

## No-fallback rule
`fallback_used` must always be false. Live never downgrades to fixture.
No boolean live flags. No implicit environment selection.
