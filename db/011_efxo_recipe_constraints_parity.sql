-- 011_efxo_recipe_constraints_parity.sql — T6 supporting-system convergence (2026-09-13).
--
-- WHY: the 2026-09-08 cutover copied recipe.* to efxowqudmsfqjmbeidjc as DATA ONLY. On the
-- production authority recipe.sale_events has no primary key and recipe.run_manifests has
-- neither its primary key nor its run_id unique key (efxo: 1 CHECK constraint, 0 indexes;
-- legacy spine: 3 constraints + 1 index; verified 2026-09-13). recipe_engine/persist.py
-- writes `insert into recipe.sale_events ... on conflict (listing_id)` and
-- `insert into recipe.run_manifests ... on conflict (run_id)`, so the daily hunt cannot be
-- pointed at efxo until these exist (it would fail exactly like landos-engine /evaluate did).
--
-- WHAT: the legacy post-data DDL for schema recipe (pg_dump 17.10 --section=post-data
-- -n recipe), guarded so the file is idempotent. No data change (0 duplicate listing_id /
-- run_id on efxo, verified). Apply in one transaction:
--   psql "$DSN" -X -v ON_ERROR_STOP=1 -1 -f db/011_efxo_recipe_constraints_parity.sql

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'run_manifests_pkey' and conrelid = 'recipe.run_manifests'::regclass) then
    alter table only recipe.run_manifests add constraint run_manifests_pkey PRIMARY KEY (id);
  end if;
end $$;
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'run_manifests_run_id_key' and conrelid = 'recipe.run_manifests'::regclass) then
    alter table only recipe.run_manifests add constraint run_manifests_run_id_key UNIQUE (run_id);
  end if;
end $$;
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'sale_events_pkey' and conrelid = 'recipe.sale_events'::regclass) then
    alter table only recipe.sale_events add constraint sale_events_pkey PRIMARY KEY (listing_id);
  end if;
end $$;
create index if not exists idx_sale_events_territory on recipe.sale_events using btree (in_territory, county_slug);
