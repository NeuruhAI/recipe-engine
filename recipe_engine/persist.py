"""
persist.py — upsert into recipe.sale_events + run manifests.

Idempotency: PK = listing_id. A re-seen listing updates last_seen_at and
run_count; it never duplicates.

THE FORK GUARD: if a row already carries a landos_node_id and the engine
returns a DIFFERENT one for the same listing, that is identity forking —
the exact failure the open gate exists to catch. We raise immediately
(STOP at failures) instead of silently overwriting.
"""
import hashlib
import json
from datetime import timezone, datetime

import psycopg
from psycopg.types.json import Jsonb


class NodeForkError(RuntimeError):
    pass


def connect(database_url: str):
    return psycopg.connect(database_url, autocommit=False)


def upsert_event(cur, ev: dict, county_slug: str, in_territory: bool,
                 lat, lng, geo_precision: str, provenance: str) -> bool:
    """Insert or refresh one event. Returns True if newly inserted."""
    cur.execute(
        """
        insert into recipe.sale_events
            (listing_id, url, title, description, organizer_name,
             organizer_phone, start_at, end_at, street_address, city, state,
             postal_code, county_slug, in_territory, geo_precision, lat, lng,
             payload, provenance)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s)
        on conflict (listing_id) do update set
            title = excluded.title,
            description = excluded.description,
            start_at = excluded.start_at,
            end_at = excluded.end_at,
            street_address = excluded.street_address,
            city = excluded.city,
            state = excluded.state,
            postal_code = excluded.postal_code,
            county_slug = excluded.county_slug,
            in_territory = excluded.in_territory,
            geo_precision = excluded.geo_precision,
            lat = excluded.lat,
            lng = excluded.lng,
            payload = excluded.payload,
            last_seen_at = now(),
            run_count = recipe.sale_events.run_count + 1
        returning (xmax = 0) as inserted
        """,
        (ev["listing_id"], ev["url"], ev["title"], ev["description"],
         ev["organizer_name"], ev["organizer_phone"], ev["start_at"],
         ev["end_at"], ev["street_address"], ev["city"], ev["state"],
         ev["postal_code"], county_slug, in_territory, geo_precision,
         lat, lng, Jsonb(ev["payload"]), provenance),
    )
    return cur.fetchone()[0]


def record_node_id(cur, listing_id: str, node_id: str):
    """Store the engine-returned node_id; raise on identity fork."""
    cur.execute(
        "select landos_node_id from recipe.sale_events where listing_id = %s",
        (listing_id,),
    )
    row = cur.fetchone()
    existing = str(row[0]) if row and row[0] else None
    if existing and existing != node_id:
        raise NodeForkError(
            f"NODE FORK on listing {listing_id}: stored {existing}, "
            f"engine returned {node_id}"
        )
    cur.execute(
        "update recipe.sale_events set landos_node_id = %s where listing_id = %s",
        (node_id, listing_id),
    )


def write_manifest(cur, manifest_dir, run_id: str, manifest: dict):
    """Write the manifest file, then mirror it (with its SHA) into the DB.
    Returns the sha256 of the manifest body — the receipt value."""
    body = json.dumps(manifest, indent=2, sort_keys=True, default=str)
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    manifest_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_dir / f"run_{run_id}.manifest.json"
    path.write_text(body, encoding="utf-8")

    c = manifest["counts"]
    cur.execute(
        """
        insert into recipe.run_manifests
            (run_id, started_at, finished_at, anchors, pages_fetched,
             cache_hits, events_seen, events_upserted, in_territory,
             report_only, engine_called, engine_ok, manifest_sha256, manifest)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (run_id) do nothing
        """,
        (run_id, manifest["started_at"],
         datetime.now(timezone.utc).isoformat(),
         c["anchors"], c["pages_fetched"], c["cache_hits"], c["events_seen"],
         c["events_upserted"], c["in_territory"], c["report_only"],
         c["engine_called"], c["engine_ok"], sha, Jsonb(manifest)),
    )
    return sha, path
