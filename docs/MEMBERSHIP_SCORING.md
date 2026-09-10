# Membership scoring — `rank(opportunity, recipe, strategy)`

STATUS: BUILT. Files only. Not loaded into any database.

## The gap

`scripts/rank_nc_curative_raw.py` computes one additive
`curative_priority_score` per property and ranks it only under the property's
primary recipe. Multi-membership emission (`docs/MULTI_MEMBERSHIP_PROJECTION.md`,
PR #21) lets one property satisfy several recipes, but every secondary
membership reaches DeedSonar `candidate_recipe_membership` with
`recipe_score` / `recipe_rank` null. The score itself is one number: nothing
says which dimension a factor measures, which inputs were UNKNOWN, what argues
against the opportunity, or what the economics rest on. And `truthy(None)` is
`False`, so on the Aug-27 ranking a blank `tax_foreclosure_indicator` (18,370
of 20,734 rows) and a blank `years_owned` (all 20,734) read as known negatives.

## The seam

`recipe_engine/opportunity_intelligence/membership_scoring.py`

| call | returns |
|---|---|
| `load_strategy(strategy_id)` | a sha-pinned strategy (`strategies/<id>.json`, registry in `STRATEGY_REGISTRY`) |
| `score_membership(row, recipe_id, strategy=…)` | one membership's score vector, evidence, counter-evidence, economics |
| `rank(scored_memberships)` | a `RankedUniverse`; `universe.get(opportunity_key, recipe_id)` is `rank(opportunity, recipe, strategy)` |
| `to_score_vector(receipt, tenant_id=…, universe_id=…)` | Lead Recipe OS `score-vector.v1`, re-hashed identically by its kernel |
| `membership_projection_row(receipt)` | one loader row for `candidate_recipe_membership` |

### Receipt `neuruh.recipe-engine.membership-score.v1`

- `opportunity` — `county_fips|parcel_apn` key, `property_key`, `source_url`.
- `recipe` — `recipe_id`, `recipe_version`, `membership` (`PRIMARY` / `SECONDARY` /
  null), `match_rank`, the why-matched `input_hash` and receipt sha.
- `strategy` — id, document sha, score name and semantics, `recipe_conditioned`.
- `score_vector.components` — the ten dimensions of
  `RECIPE_SCORE_VECTOR_COVERAGE.csv`, in census order:

  | dimension | state here | in composite |
  |---|---|---|
  | signal_strength | EVALUATED (heir, fragmentation, out-of-state, tenure, multi-trigger) | yes |
  | urgency | EVALUATED (published tax distress, active tax foreclosure) | yes |
  | economic_value | EVALUATED (assessed-value and tax-balance proxies), UNKNOWN when both inputs are blank | yes |
  | data_completeness | EVALUATED: known material inputs / 9 | no |
  | source_independence_diversity | EVALUATED from the v3 annotation, else NOT_EVALUATED | no |
  | strategic_fit, downside_risk | NOT_EVALUATED | no |
  | evidence_confidence, contactability, liquidity | UNWIRED (owned elsewhere) | no |

  A non-EVALUATED component has `value: null`, never 0. An EVALUATED component
  carries `receipt_sha256` over the factor records it was computed from.
- `composite` — capped sum of the in-composite dimensions, `uncapped`, `cap_applied`,
  tier, the factor contributions (`factor_id`, `delta`) and `undetermined_factors`
  (factors that could still fire on an UNKNOWN input).
- `evidence` — every fired factor with its delta, the inputs it read and its
  source family.
- `counter_evidence` — known-false flags (`NOT_OBSERVED_IN_SOURCE_NOT_A_LEGAL_FACT`),
  the ranker's contradictions, UNKNOWN inputs, unknown material factors, and the
  disqualifiers as `NOT_EVALUATED` (no source feeds them).
- `economics` — `PROXY_NOT_VERIFIED_EQUITY`: assessed value, tax balance, spread,
  tax years, market value UNKNOWN, verified equity UNKNOWN, and the ranker's
  `potential_equity_proxy` as received (with a note when it says UNKNOWN beside a
  KNOWN assessed value below its 75,000 floor).
- `received` — parity with the ranker's receipt (`EQUAL` / `NO_RECEIVED_RECEIPT` /
  `NOT_APPLICABLE`) and the received why-ranked sha and universe id.
- `rank` — `global_rank` (the opportunity's best membership), `recipe_rank`
  (1-based within `recipe_id` + `recipe_version`), `recipe_membership_count`,
  `ranking_universe_id`, `ranking_run_id`, the tie-break.
- `content_sha256` — Lead Recipe OS consumer canon.

No receipt carries a phone, contact authorization or outreach field.

### Strategy `neuruh.recipe-engine.scoring-strategy.v1`

A strategy weighs the canonical factors; it cannot add one. `factor_deltas` must
name exactly the 14 canonical factors with integer deltas; `recipe_factor_deltas`
may override deltas per recipe (a recipe-conditioned strategy); `tiers` descend to
a floorless tier; `tie_break` leads with `composite_desc` and uses only the
ranker's keys. A strategy id is immutable — a changed weight is a new id and a new
`STRATEGY_REGISTRY` pin. Only `nc_curative_rank.v3` ships: it is the ranker's own
point model, recipe-unconditioned, so every recipe a property satisfies carries the
same score and the recipe rank orders the recipe's full membership.

## The proof is the existing receipts

Under `nc_curative_rank.v3`:

1. `score_membership` refuses (`ReceiptDriftError`) any row whose received
   `curative_priority_score`, `score_reasons`, `trigger_count` or `priority_tier`
   disagrees with the recomputation.
2. `scripts/score_rank_memberships.py` ranks the primary memberships alone and
   fails closed unless that reproduces the ranker's `rank`, `recipe_rank`,
   `recipe_membership_count` and `ranking_universe_id` on every row.

Court (`tests/opportunity_intelligence/test_membership_scoring.py`): factor
parity with `base_score` on 32,400 input combinations; full receipts on a sample;
a shuffled v3-ranked fixture reproduces the ranker's ranks and universe id under
three shuffles; UNKNOWN ≠ FALSE; strategy refusal cases; tamper evidence. Six
mutants (tie-break direction, UNKNOWN→False, global rank as recipe rank, no drift
check, no cap, UNKNOWN dimension as 0) each fail the court.

Cross-repo court (`test_membership_score_lead_recipe_os_court.py`, set
`LEAD_RECIPE_OS_KERNEL_PATH` to lead-recipe-os @40d48d4 in a venv with pydantic +
pydantic-settings): the consumer kernel validates the vector and re-hashes it to
the same sha, refuses an UNKNOWN dimension dressed as 0, and
`plan_next_best_evidence` opens gaps on exactly the dimensions scoring left open.

## Running

```
PYTHONPATH=. python3 scripts/rank_nc_curative_raw_v3.py --input <ranker input> --output <run dir>
PYTHONPATH=. python3 scripts/project_deedsonar_receiver.py --ranked <run dir>/NC_CURATIVE_ALL.csv \
    --input <ranker input> --output-dir <projection dir> --run-date YYYYMMDD      # PR #21
PYTHONPATH=. python3 scripts/score_rank_memberships.py --ranked <run dir>/NC_CURATIVE_ALL.csv \
    --membership <projection dir>/DEEDSONAR_MEMBERSHIP_PROJECTION.csv --output-dir <score dir>
```

Without `--membership` only primaries are scored. Emits
`MEMBERSHIP_SCORE_RECEIPTS.jsonl`, `MEMBERSHIP_SCORE_PROJECTION.csv` and
`MEMBERSHIP_SCORE_SUMMARY.json`. Files only: no database write, no outbound, no
spend. Loading the projection into `candidate_recipe_membership` is a production
write (`scoring_version = nc_curative_rank.v3`, `recipe_score`, `recipe_rank`,
`source_receipt_id = membership_score_receipt_sha256`, `explanation =
why_matched_receipt`); the membership class of a secondary row stays the loader's
call under DeedSonar `docs/courts/CANDIDATE_RECIPE_MEMBERSHIP_V1_20260907.md`.

Two things a loader must not get wrong:

- **Which universe a `recipe_rank` belongs to.** The projection's `recipe_rank`
  is rank within the recipe's FULL membership (primary + secondary) under the
  strategy, and its `ranking_universe_id` names that universe. The ranks already
  seeded into `candidate_recipe_membership` (from `recipe_engine->>'recipe_rank'`)
  are primary-only ranks in the ranker's universe. For a recipe with secondary
  members the two differ (e.g. `fractional_family_ownership` 976 primary vs 1,528
  members); never mix them in one column without the universe id.
- **The primary row already exists.** The v1 seed wrote each primary with
  `scoring_version = ''` (the definition's blank) and the table allows one
  `PRIMARY_PROJECTION` per import row. Inserting the primary again under
  `scoring_version = 'nc_curative_rank.v3'` creates a second key for the same
  membership; update or supersede the seeded row instead.
