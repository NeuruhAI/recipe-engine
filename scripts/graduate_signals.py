"""
graduate_signals.py — run the real signals through ratified policy v1.

    python scripts/graduate_signals.py [--promote-research] [--identity-receipt PATH]

WHAT THE FOUNDER AUTHORISED (2026-08-19):
    AUTOMATIC_RESEARCH_PROMOTION                 AUTHORIZED
    AUTOMATIC_QUALIFIED_CANDIDATE_CLASSIFICATION AUTHORIZED
    AUTOMATIC_PRODUCTION_CANDIDATE_PROMOTION     NOT YET AUTHORIZED

So a signal that reaches RESEARCH_PROPERTY may be written into the estate as a
RESEARCH_CANDIDATE through the courted `data_estate_revise_v1` path. A signal
that reaches candidate eligibility is recorded as SHADOW_CANDIDATE_ELIGIBLE in
the receipt and is NOT written — that mutation waits for shadow calibration and
founder ratification.

Identity comes from the parcel resolver's receipt
(landos-engine/data/signal_identity_runs). A signal with no real parcel stays
OBSERVED_SIGNAL: `estate-<listing_id>` is an anchor for the SALE, not a property.

Idempotent by construction. `data_estate_revise_v1` keys on property_key and
guards source_row_id inside the batch, so a replay RETAINS rather than
duplicating. Default is a dry run.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import psycopg
from psycopg.rows import dict_row

from recipe_engine.autonomy.models import AuthorityLevel, ConflictState
from recipe_engine.graduation import (
    POLICY_VERSION, GraduationPolicy, GraduationState, PropertyIdentity,
    SignalEvidence, classify,
)

IDENTITY_GLOB = str(Path.home() / "neuruh-repos/NeuruhAI/landos-engine"
                    / "data/signal_identity_runs/*.json")
OUT_DIR = REPO / "data" / "graduation_runs"

#: The one source these signals actually have. estatesales.net is a real public
#: listing service, not a county of record — OBSERVED, not DOMAIN_AUTHORITY.
SIGNAL_SOURCE = "estatesales.net"
SIGNAL_FAMILY = "estate_sale"


def load_env():
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def newest_identity_receipt(explicit: str | None):
    path = explicit or (sorted(glob.glob(IDENTITY_GLOB))[-1] if glob.glob(IDENTITY_GLOB) else None)
    if not path:
        raise SystemExit("no identity receipt found — run resolve_signal_parcel_identity first")
    return path, json.loads(Path(path).read_text())


def research_row(signal, identity_row):
    """The estate row a RESEARCH promotion writes. Contact fields are absent by
    design: research is diligence, and this path grants no contact rights."""
    apn = identity_row["REAL_PARCEL_APN"]
    fips = identity_row["COUNTY_FIPS"]
    state = identity_row["STATE"]
    property_key = f"{state}:{fips}:{apn}"
    return {
        "property_key": property_key,
        "source_row_id": f"signal:{signal['listing_id']}",
        "county_fips": fips,
        "parcel_apn": apn,
        "candidate_class": "RESEARCH_CANDIDATE",
        "primary_recipe": "estate_sale_life_event_research",
        "signals": ["estate_sale"],
        "recommended_action": "CONTINUE_PUBLIC_ENRICHMENT",
        "source_as_of": identity_row["OBSERVED_AT"],
        "normalized": {
            "source_system": "recipe_engine.graduation",
            "property_key": property_key,
            "apn": apn,
            "state": state,
            "county_fips": fips,
            "propertyCity": signal.get("city"),
            "propertyZip": signal.get("postal_code"),
            "graduation_state": GraduationState.RESEARCH_PROPERTY.value,
            "graduation_policy_version": POLICY_VERSION,
            "identity_source": identity_row["IDENTITY_RESOLVER"],
            "parcel_provenance": identity_row["PARCEL_PROVENANCE"],
            "signal_source": SIGNAL_SOURCE,
            "signal_listing_id": signal["listing_id"],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="graduate real signals under policy v1")
    ap.add_argument("--promote-research", action="store_true",
                    help="write RESEARCH_PROPERTY rows via data_estate_revise_v1")
    ap.add_argument("--identity-receipt", default=None)
    args = ap.parse_args()
    load_env()

    identity_path, identity = newest_identity_receipt(args.identity_receipt)
    by_signal = {r["SIGNAL_ID"]: r for r in identity["results"]}
    run_id = f"graduation-v1-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    policy = GraduationPolicy()
    now = datetime.now(timezone.utc)

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        cur = conn.cursor()
        cur.execute("""
            select listing_id, city, state, postal_code, county_slug, last_seen_at
              from recipe.sale_events
             where in_territory and geo_precision = 'exact'
             order by listing_id""")
        signals = cur.fetchall()

    results, research_rows = [], []
    for signal in signals:
        ident = by_signal.get(signal["listing_id"], {})
        resolved = ident.get("IDENTITY_VERDICT") == "RESOLVED"
        identity_obj = PropertyIdentity(
            parcel_apn=ident.get("REAL_PARCEL_APN") or f"estate-{signal['listing_id']}",
            county_fips=ident.get("COUNTY_FIPS"),
            apn_is_real_parcel=resolved,
            address_verified=False,
            identity_source=ident.get("IDENTITY_RESOLVER"),
            parcel_provenance=ident.get("PARCEL_PROVENANCE"),
        )
        evidence = [SignalEvidence(
            source_type=SIGNAL_SOURCE, family=SIGNAL_FAMILY,
            authority_level=AuthorityLevel.OBSERVED, freshness="current",
            observed_at=signal["last_seen_at"].isoformat().replace("+00:00", "Z"),
        )]
        verdict = classify(identity_obj, evidence, conflict=ConflictState.NONE,
                           opportunity_score=None, policy=policy, now=now)

        results.append({
            "SIGNAL_ID": signal["listing_id"],
            "STATE": verdict.state.value,
            "WHY": verdict.why,
            "MISSING": list(verdict.missing),
            "SOURCES": verdict.corroborating_sources,
            "FAMILIES": list(verdict.distinct_families),
            "SCORE": None,
            "EVIDENCE_AGE_DAYS": list(verdict.evidence_ages_days),
            "PROVENANCE_COMPLETE": verdict.provenance_complete,
            "POLICY_VERSION": verdict.policy_version,
            "EVIDENCE_HASH": verdict.evidence_hash,
            "REAL_PARCEL_APN": ident.get("REAL_PARCEL_APN"),
            "IDENTITY_VERDICT": ident.get("IDENTITY_VERDICT"),
            "WOULD_PROMOTE_TO_RESEARCH": verdict.would_promote_to_research,
            "SHADOW_CANDIDATE_ELIGIBLE": verdict.would_promote_to_candidate,
        })
        if verdict.would_promote_to_research and resolved:
            research_rows.append(research_row(signal, ident))

    tally = Counter(r["STATE"] for r in results)
    receipt = {
        "run_id": run_id,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "policy_version": POLICY_VERSION,
        "policy": dict(sorted(policy.__dict__.items())),
        "identity_receipt": identity_path,
        "signals_total": len(results),
        "states": dict(tally),
        "research_eligible": sum(r["WOULD_PROMOTE_TO_RESEARCH"] for r in results),
        "shadow_candidate_eligible": sum(r["SHADOW_CANDIDATE_ELIGIBLE"] for r in results),
        "research_promoted": bool(args.promote_research),
        "production_candidate_promoted": False,   # NOT authorised
        "results": results,
    }

    if args.promote_research and research_rows:
        with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
            cur = conn.cursor()
            cur.execute("select * from public.data_estate_revise_v1(%s,%s,%s::jsonb)",
                        (run_id, "recipe_engine.graduation.v1", json.dumps(research_rows)))
            outcome = cur.fetchone()
            conn.commit()
        outcome["batch_id"] = str(outcome["batch_id"])
        receipt["revise_result"] = outcome

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{run_id}.json"
    path.write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")

    for r in results:
        print(f"  {r['SIGNAL_ID']}  {r['STATE']:<18} research={str(r['WOULD_PROMOTE_TO_RESEARCH']):<5} "
              f"shadow_candidate={str(r['SHADOW_CANDIDATE_ELIGIBLE']):<5} apn={r['REAL_PARCEL_APN'] or '-'}")
        if r["MISSING"]:
            print(f"      missing: {r['MISSING']}")
    print()
    print(json.dumps({k: receipt[k] for k in
                      ("signals_total", "states", "research_eligible",
                       "shadow_candidate_eligible", "research_promoted",
                       "production_candidate_promoted")}, indent=2))
    if "revise_result" in receipt:
        print("  revise_result:", receipt["revise_result"])
    print("  receipt:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
