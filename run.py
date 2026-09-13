#!/usr/bin/env python3
"""
run.py — the full pipeline: fetch -> parse -> attribute -> persist ->
real-engine evaluate (in-territory only) -> manifest.

  python3 run.py                 # full run
  python3 run.py --no-engine     # scrape+persist only (no LandOS calls)
  python3 run.py --run-id X      # explicit run id (default: UTC timestamp)
  python3 run.py --print-authority  # resolve + preflight the database; writes nothing

Env (.env in repo root): DEEDSONAR_DATABASE_URL — the ONE production
authority (efxowqudmsfqjmbeidjc); the legacy-named DATABASE_URL is consulted
only when it is absent, and the retired lvdi spine is refused either way
(recipe_engine/db_authority.py) — plus LANDOS_ENGINE_URL, LANDOS_SECRET,
TENANT_ID.

STOP-at-failures doctrine: zero events across ALL anchors = parse failure
(exit 2, inspect cached html by hand). Node fork = exit 3. Engine errors
on individual events are counted and reported but don't abort the run —
the manifest shows engine_called vs engine_ok and the run exits 4 if any
failed. A missing or refused database is exit 1; a database that lacks the
unique keys persist.py's ON CONFLICT depends on is exit 5 — both before the
first write.

NO AXON imports — this module and everything it imports must run
standalone (scheduler invariant).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from recipe_engine.cities import (ANCHOR_CITIES, ANCHOR_QUERIES, attribute_county,
                                  centroid_for)
from recipe_engine.source_api import fetch_anchor_events
from recipe_engine.parse import canon_keys
from recipe_engine.engine import LandosEngine, EngineError
from recipe_engine import persist
from recipe_engine.persist import NodeForkError
from recipe_engine.db_authority import (DatabaseAuthorityError, assert_write_targets,
                                        resolve_database_url, write_target_status)


def load_env():
    """Minimal .env loader — no third-party dependency for the scheduler path."""
    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def print_authority(authority):
    """--print-authority: the deployed runtime's own proof of which database it
    writes. Resolves exactly as a run does, connects READ-ONLY, reports the
    write-target preflight as JSON, and exits 0 (ready) or 5 (unready). It
    never scrapes and never writes."""
    status = {"authority": authority.ref or authority.host or "local-socket",
              "class": authority.klass, "source": authority.source}
    conn = persist.connect(authority.dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction read only")
            status["write_targets"] = write_target_status(cur)
            cur.execute("select current_database(), current_user")
            status["database"], status["role"] = cur.fetchone()
        conn.rollback()
    finally:
        conn.close()
    status["ready"] = all(status["write_targets"].values())
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0 if status["ready"] else 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-engine", action="store_true")
    ap.add_argument("--run-id", default=None)
    ap.add_argument(
        "--print-authority", action="store_true",
        help="Resolve the database authority and run the write-target preflight "
             "read-only, print the result as JSON, exit 0/5. Never writes.")
    ap.add_argument(
        "--gate-listing", default=None, metavar="LISTING_ID",
        help="Push this ONE listing through the engine leg even if it is "
             "report-only. Gate-proof escape hatch for when the territory "
             "has zero live sales; the node it creates is flagged in the "
             "receipt. Never expands TERRITORY.")
    args = ap.parse_args()

    load_env()
    try:
        authority = resolve_database_url()
    except DatabaseAuthorityError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 1
    print(authority.describe())
    database_url = authority.dsn
    if args.print_authority:
        return print_authority(authority)

    now = datetime.now(timezone.utc)
    run_id = args.run_id or now.strftime("%Y%m%dT%H%M%SZ")
    today = now.strftime("%Y%m%d")
    cache_dir = REPO / "data" / "cache"
    manifest_dir = REPO / "data" / "manifests"

    engine = None
    if not args.no_engine:
        url = os.environ.get("LANDOS_ENGINE_URL", "http://127.0.0.1:8400")
        secret = os.environ.get("LANDOS_SECRET")
        tenant = os.environ.get("TENANT_ID")
        if not secret or not tenant:
            print("FATAL: LANDOS_SECRET / TENANT_ID not set", file=sys.stderr)
            return 1
        engine = LandosEngine(url, secret, tenant)
        health = engine.health()
        if health.get("db") != "up":
            print(f"FATAL: engine unhealthy: {health}", file=sys.stderr)
            return 1
        print(f"engine: {url} healthy, db up")

    counts = dict(anchors=0, pages_fetched=0, cache_hits=0, events_seen=0,
                  events_upserted=0, in_territory=0, report_only=0,
                  engine_called=0, engine_ok=0)
    per_anchor = {}
    node_records = []     # (listing_id, parcel_apn, node_id) — the gate proof
    engine_failures = []
    seen_listing_ids = set()

    conn = persist.connect(database_url)
    try:
        cur = conn.cursor()
        # Fail CLOSED before the first upsert if the authority lacks the unique
        # keys every ON CONFLICT below needs (a restore without post-data).
        try:
            assert_write_targets(cur)
        except DatabaseAuthorityError as e:
            conn.rollback()
            print(f"FATAL: {e}", file=sys.stderr)
            return 5
        for slug in ANCHOR_CITIES:
            counts["anchors"] += 1
            events, from_cache, final_url = fetch_anchor_events(
                slug, ANCHOR_QUERIES[slug], cache_dir, today)
            counts["cache_hits" if from_cache else "pages_fetched"] += 1

            anchor_events = 0
            anchor_in_terr = 0
            anchor_dupes = 0
            for ev in events:
                anchor_events += 1
                if ev["listing_id"] in seen_listing_ids:
                    anchor_dupes += 1
                    continue  # anchor radii overlap; the same sale is not two sales
                seen_listing_ids.add(ev["listing_id"])
                counts["events_seen"] += 1

                county_slug, in_terr = attribute_county(
                    ev["state"], ev["postal_code"], ev["city"])
                if in_terr:
                    anchor_in_terr += 1
                # The source carries each sale's OWN coordinates. Use them; fall
                # back to the city centroid only when the source withholds them,
                # and never call a centroid 'exact'.
                if ev.get("latitude") is not None and ev.get("longitude") is not None:
                    lat, lng, geo = ev["latitude"], ev["longitude"], "exact"
                else:
                    cen = centroid_for(ev["state"], ev["city"]) if in_terr else None
                    lat, lng = (cen if cen else (None, None))
                    geo = "centroid" if cen else "none"

                provenance = f"estatesales.net:{slug}:{run_id}"
                if persist.upsert_event(cur, ev, county_slug, in_terr,
                                        lat, lng, geo, provenance):
                    counts["events_upserted"] += 1
                counts["in_territory" if in_terr else "report_only"] += 1

                if engine and (in_terr or ev["listing_id"] == args.gate_listing):
                    keys = canon_keys(ev["listing_id"])
                    counts["engine_called"] += 1
                    try:
                        result = engine.evaluate(
                            keys["parcel_apn"], keys["owner_hash"],
                            county_slug, ev["state"])
                        persist.record_node_id(
                            cur, ev["listing_id"], result["node_id"])
                        counts["engine_ok"] += 1
                        node_records.append(
                            (ev["listing_id"], keys["parcel_apn"],
                             result["node_id"]))
                    except EngineError as e:
                        engine_failures.append(str(e))

            per_anchor[slug] = {"events_on_page": anchor_events,
                                "in_territory": anchor_in_terr,
                                "duplicates_removed": anchor_dupes,
                                "from_cache": from_cache,
                                "final_url": final_url}
            print(f"{slug}: {anchor_events} events "
                  f"({anchor_in_terr} in territory, {anchor_dupes} dupes) "
                  f"({'cache' if from_cache else 'fetched'})")

        if counts["events_seen"] == 0:
            conn.rollback()
            print("FATAL: zero events across ALL anchors = source failure. "
                  "Inspect data/cache/*.api.json by hand.", file=sys.stderr)
            return 2

        manifest = {
            "run_id": run_id,
            "started_at": now.isoformat(),
            "source": "estatesales.net:/api/sale-details",
            "counts": counts,
            "per_anchor": per_anchor,
            "node_records": [
                {"listing_id": l, "parcel_apn": p, "node_id": n}
                for l, p, n in node_records
            ],
            "engine_failures": engine_failures,
        }
        sha, path = persist.write_manifest(cur, manifest_dir, run_id, manifest)
        conn.commit()
        print(f"\nmanifest: {path}")
        print(f"manifest sha256: {sha}")
        print(json.dumps(counts, indent=2))
        if engine_failures:
            print(f"\nENGINE FAILURES ({len(engine_failures)}):", file=sys.stderr)
            for f in engine_failures:
                print(f"  {f}", file=sys.stderr)
            return 4
        return 0

    except NodeForkError as e:
        conn.rollback()
        print(f"FATAL (node fork): {e}", file=sys.stderr)
        return 3
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
