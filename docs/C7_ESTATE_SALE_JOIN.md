# C7 — Estate-sale daily feed identity join

MISSION_ID=NEURUH-DEEDSONAR-LIVE-SYSTEM-CONVERGENCE-20260908 / CHILD_7_ESTATE_SALE_JOIN
ROLE=RECIPE_ENGINE_IDENTITY_JOIN_CONSUMER
UNLOCK=ESTATE_SALE_JOIN
GAP=IDENTITY_JOIN_MISSING → JOIN_EXISTING_DAILY_FEED
RECIPE=estate_sale_property
RISK=SOURCE_ONLY
SUPABASE_MUTATED=NO
DEEDSONAR_MUTATED=NO
MERGE=NO

## Success criterion

**IDENTITY_JOIN_MISSING → JOIN_EXISTING_DAILY_FEED**

Join existing daily estatesales.net harvest rows to parcel identity using the
strongest seam already in this repo, and emit a testable MATCH / NO_MATCH /
UNKNOWN projection. Do not invent a scraper, LandOS call, DeedSonar write,
membership writer, or Founder Runtime capability.

## Strongest existing seams (reuse, not invent)

| Seam | Where | What it already is |
|---|---|---|
| Daily harvest | `run.py` → `recipe.sale_events` | LIVE estatesales.net `/api/sale-details` / JSON-LD `SaleEvent` |
| Canon keys | `recipe_engine/parse.py::canon_keys` | `parcel_apn = estate-{listing_id}` — sale idempotency, **not** a parcel |
| Identity consumer | `scripts/graduate_signals.py` | Joins `recipe.sale_events.listing_id` to landos-engine `SIGNAL_ID` |
| Graduation court | `data/graduation_runs/graduation-v1-20260819T104146Z.json` | Sealed 9 in-territory signals + `IDENTITY_VERDICT` / `REAL_PARCEL_APN` |

`engine.py` POST `/evaluate` is the old identity-**node** create on the synthetic
`estate-{listing_id}` key. It is not address→parcel resolve. P1B already ruled
it SOURCE_REFRESH, not a join. This unlock does not call it.

## Identity owner (not in this repo)

**NeuruhAI/landos-engine**

- `scripts/resolve_signal_parcel_identity.py`
- `app/resolvers/address_to_apn.py::AddressApnResolver`

That owner spatial-intersects an **exact** point against a county parcel layer
and returns a PIN only when exactly one real parcel intersects. Zero, several,
sentinel `99-` / `-9999-9999-`, or an unconfigured county is a refusal.

recipe-engine has **no** parcel identity API. The honest join stays adapter /
consumer-only.

## What this unlock binds

`recipe_engine/estate_sale_identity_join.py` consumes:

1. daily-feed rows (`listing_id`, optional address/geo fields already on the feed);
2. identity-receipt rows (landos-engine `signal_identity_runs` shape **or** the
   graduation-result shape `graduate_signals.py` already stores).

Join key: `listing_id == SIGNAL_ID` (the existing consumer key).

Projection:

| Identity receipt | Decision |
|---|---|
| `RESOLVED` + real APN | **MATCH** |
| `SENTINEL_PARCEL_REJECTED` / `NO_PARCEL_AT_POINT` | **NO_MATCH** |
| `NO_RESOLVER_FOR_COUNTY` / `RESOLVER_UNAVAILABLE` | **UNKNOWN** (`IDENTITY_ABSENT` for that county / resolver) |
| `AMBIGUOUS_MULTIPLE_PARCELS` | **UNKNOWN** (refuse to pick) |
| no receipt / no row / unknown verdict / `estate-*` / sentinel PIN claimed as RESOLVED | **UNKNOWN** |

`estate-{listing_id}` is never MATCH. Missing stays UNKNOWN. No fabricated parcel.

Sealed 2026-08-19 daily feed (9 in-territory listings): **4 MATCH / 1 NO_MATCH / 4 UNKNOWN**.

## Explicitly not done

- No new scraper, marketplace, or daemon.
- No edit to `run.py`, `daily_run.sh`, `engine.py`, `graduate_signals.py`, LandOS, DeedSonar, or Supabase.
- No Founder Runtime capability.
- No `evaluate_recipes` bind (P1B CALLER_ABSENT still holds).
- No LIVE product claim.

## Lock

`tests/test_estate_sale_identity_join.py`

Receipt: `artifacts/C7_ESTATE_SALE_JOIN_RECEIPT.json`
