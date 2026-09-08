# recipe-engine

Estate-sale signal scraper (estatesales.net, JSON-LD `SaleEvent`) →
`recipe.sale_events` (own Postgres schema, multi-consumer: TERRITORY + REI-X)
→ real LandOS engine `/evaluate` for identity-node creation (in-territory
events only).

Built 2026-06-12 by extraction per CODE PACK — RECIPE ENGINE W1.5
(donor: `find-sell-profit` estatesales URL shapes; JSON-LD extraction
validated against live pages). estatesales.net ONLY — auctionzip is
dropped by rule (Cloudflare).

## Canon keys (do not change)

- `listing_id` — estatesales.net listing id, PK and idempotency anchor
- `parcel_apn = estate-{listing_id}`
- `owner_hash = sha256(listing_id)`
- county slugs: `berrien-mi` / `st-joseph-in`; everything else
  `report-only:*` (scraped, never pushed — territory expansion is an
  operator decision)

## Layout

- `recipe_engine/cities.py` — 13 anchor cities + per-event county attribution
- `recipe_engine/fetch.py` — polite fetcher (robots.txt, ≥2s spacing, honest
  UA, daily page cache in `data/cache/`)
- `recipe_engine/parse.py` — JSON-LD SaleEvent extraction
- `recipe_engine/persist.py` — upserts, node-fork guard, run manifests
- `recipe_engine/engine.py` — REAL LandOS engine client (no mock in this repo)
- `run.py` — pipeline entrypoint
- `db/010_recipe_schema.sql` — `recipe` schema DDL (never touches landos/public)
- `scripts/daily_run.sh` — scheduler entry (zero AXON dependency)
- `launchd/com.neuruh.recipe-engine.daily.plist` — 6AM agent (UNARMED until
  operator loads it)

## Setup

```bash
cd ~/repos/recipe-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill real values
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/010_recipe_schema.sql
```

## Run

```bash
# real engine must be up: cd ~/Documents/landos-engine &&
#   .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8400
python3 run.py               # full pipeline
python3 run.py --no-engine   # scrape + persist only
```

Exit codes: 0 ok · 1 config/env · 2 zero events (parse failure) ·
3 node fork · 4 engine call failures (run completed, see manifest).

## Autonomy core (candidate)

Cross-domain deterministic scenario collapse lives in `recipe_engine/autonomy/`
(operator re-charter, branch `feat/autonomy-core-p0`).

- Pure library: no DB/network/env in the autonomy package
- Estate-sale pipeline above is preserved and unchanged
- Docs: `docs/RECIPE_ENGINE_CHARTER.md`
- Tests: `python3 -m unittest discover -s tests -v`

**Not live-integrated.** No autonomous execution claims.


## Founder Runtime P1 (candidate)

Persistent mission kernel + recovery + read-only system-status vertical slice.

- Package: `recipe_engine.founder_runtime`
- Fixture adapters only — no live services
- Local SQLite mission store (temp/dev path)
- Stacked on autonomy-core P0

```bash
python -m unittest discover -s tests -v
python -m recipe_engine.founder_runtime.cli run --fixture
```

See `docs/FOUNDER_RUNTIME_ARCHITECTURE.md`.

## Opportunity intelligence contract

Recipe Engine owns the versioned, portable policy sent to LandOS:

- recipe: `neuruh.opportunity-intelligence.recipe.v1`
- input: `neuruh.opportunity-intelligence.input.v1`
- signal/property population: `neuruh.opportunity-intelligence.property-population.v1`
- LandOS result: `neuruh.opportunity-intelligence.result.v1`
- canonical recipe: `land_acquisition_explainable@1.0.0`

## Independent eligibility receipts (Astra P1)

Beside the primary ranking feed, `recipe_engine.opportunity_intelligence.eligibility`
evaluates one candidate independently against each requested recipe version and
emits an immutable `MATCH` / `NO_MATCH` / `UNKNOWN` receipt. UNKNOWN never
becomes NO_MATCH; rank, score and primary_recipe are refused as inputs.

- policy: `neuruh.recipe-engine.eligibility-policy.v1`
- context: `neuruh.recipe-engine.eligibility-context.v1`
- receipt: `neuruh.recipe-engine.eligibility-receipt.v1` (projects onto the
  lead-recipe-os intake contract with a byte-compatible hash)
- ruling and proofs: `docs/P1_INDEPENDENT_ELIGIBILITY_PRODUCER_RULING.md`,
  `scripts/p1_independent_eligibility_proof.py`
- thin CLI (P1B harness bind; stdout JSONL, no daemon/store):
  `python scripts/run_independent_eligibility.py --fixture tests/opportunity_intelligence/fixtures/p1_independent_eligibility.json`
- score: `landos.opportunity-score.v1`

The recipe contains only declared rules and integer deltas. LandOS returns the
positive, negative, unknown and conflicted factors, plus independent confidence,
authority, contact, deal and offer readiness. See
`recipe_engine/opportunity_intelligence/` and the JSON Schemas in `contracts/`.
The population contract separately names raw `signal_observation_count` and
deduped `property_count`. Its source-factory compatibility court preserves the
accepted `source_factory.run_receipt.v1` 14,233-signal / 6,052-property rollup
and one 23-observation Wake property without scoring the repeated rows 23 times.

**Seam status (do not invent PASS):** `--landos-root` in-process
`evaluate_opportunity` against landos-engine `1779abf` is **LIVE / PASSED**
(`verification_sha256` `ab75e4df…`; receipt `docs/courts/landos-evaluate-1779abf.json`).
LIVE HTTP `POST /v1/opportunity-intelligence/evaluate` is **UNWIRED** in this
environment. UNKNOWN is not treated as 0. Details:
`docs/OPPORTUNITY_INTELLIGENCE_LANDOS_SEAM.md` and
`docs/courts/live-shape-receipt.md`.

Run the cross-repo court without writing a log or manifest:

```bash
python scripts/verify_opportunity_intelligence.py --landos-root /path/to/landos-engine
```

The named Willow court is explicitly a cardinality/losslessness fixture, not
the protected real-case payload. The actual 120 Willow replay remains owned by
DeedSonar's T50 regression tests.
