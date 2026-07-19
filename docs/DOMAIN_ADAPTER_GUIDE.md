# Domain Adapter Guide

1. Implement `DomainAdapter.to_opportunity(raw)` and `candidate_strategies(...)`.
2. Supply all economic metrics with `source=domain_supplied|operator_supplied`.
3. Do not call Recipe Engine autonomy to invent valuations.
4. Estate-sale: use `EstateSaleAdapter` with fixtures only in P0.
5. Future: migrate physical scraper under `adapters/` without breaking `run.py`.
