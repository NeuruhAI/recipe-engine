-- 010_recipe_schema.sql — recipe.sale_events + run manifests.
-- Own schema (multi-consumer: TERRITORY + REI-X read from it).
-- RLS ENABLED with NO POLICIES = service-role/direct-Postgres only, by design.
-- DDL touches ONLY the recipe schema — never landos.*, never public.*.

begin;

create schema if not exists recipe;

create table if not exists recipe.sale_events (
    listing_id      text primary key,
    source          text not null default 'estatesales.net',
    url             text not null,
    title           text,
    description     text,
    organizer_name  text,
    organizer_phone text,
    start_at        timestamptz,
    end_at          timestamptz,
    street_address  text,
    city            text,
    state           text,
    postal_code     text,
    -- per-event attribution from the event's own address (anchor pages are
    -- regional market pages; the anchor never decides the county)
    county_slug     text not null,
    in_territory    boolean not null default false,
    geo_precision   text not null default 'none'
                    check (geo_precision in ('exact', 'centroid', 'none')),
    lat             double precision,
    lng             double precision,
    payload         jsonb not null default '{}',
    provenance      text not null,           -- 'estatesales.net:{anchor}:{run_id}'
    -- THE GATE: node identity returned by the REAL LandOS engine.
    -- Same listing across runs must keep the same node_id (no forking);
    -- persist.py raises if the engine ever returns a different one.
    landos_node_id  uuid,
    first_seen_at   timestamptz not null default now(),
    last_seen_at    timestamptz not null default now(),
    run_count       integer not null default 1
);

create index if not exists idx_sale_events_territory
    on recipe.sale_events (in_territory, county_slug);

alter table recipe.sale_events enable row level security;

create table if not exists recipe.run_manifests (
    id              bigserial primary key,
    run_id          text unique not null,
    started_at      timestamptz not null,
    finished_at     timestamptz,
    anchors         integer not null default 0,
    pages_fetched   integer not null default 0,
    cache_hits      integer not null default 0,
    events_seen     integer not null default 0,
    events_upserted integer not null default 0,
    in_territory    integer not null default 0,
    report_only     integer not null default 0,
    engine_called   integer not null default 0,
    engine_ok       integer not null default 0,
    manifest_sha256 text,
    manifest        jsonb not null default '{}'
);

alter table recipe.run_manifests enable row level security;

commit;
