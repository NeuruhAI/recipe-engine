#!/bin/bash
# daily_run.sh — the 6AM hunt. Plain bash + the repo venv. ZERO AXON
# dependency: this script must run standalone forever; AXON mounts later
# as an enhancement, never as a blocker.
#
# Flow:
#   1. health-check the real LandOS engine (127.0.0.1:8400)
#   2. engine up   -> full pipeline (scrape -> persist -> evaluate)
#      engine down -> scrape-only fallback (--no-engine) so the day's
#                     events are never lost; engine leg self-heals next
#                     run (still-live listings get evaluated then)
#   3. LAST STEP, always (even on failure): write the day's receipt —
#      one line in data/receipts.log AND the recipe.run_manifests row
#      run.py already wrote (counts + manifest SHA). The hunt is never
#      invisible.
#
# Manual invocation (how this was tested; launchd arming is an operator
# decision):  bash scripts/daily_run.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

RUN_ID="daily-$(date -u +%Y%m%dT%H%M%SZ)"
RECEIPTS="$REPO/data/receipts.log"
ENGINE_URL="$(grep -E '^LANDOS_ENGINE_URL=' .env | cut -d= -f2-)"
ENGINE_URL="${ENGINE_URL:-http://127.0.0.1:8400}"

mkdir -p "$REPO/data"

ENGINE_MODE="engine"
if ! curl -s --max-time 10 "$ENGINE_URL/health" | grep -q '"db":\s*"up"\|"db": "up"'; then
    ENGINE_MODE="no-engine"
fi

if [ "$ENGINE_MODE" = "engine" ]; then
    "$REPO/.venv/bin/python" run.py --run-id "$RUN_ID"
else
    "$REPO/.venv/bin/python" run.py --run-id "$RUN_ID" --no-engine
fi
EXIT_CODE=$?

# ---- the receipt (always written, last step) -------------------------------
MANIFEST="$REPO/data/manifests/run_${RUN_ID}.manifest.json"
if [ -f "$MANIFEST" ]; then
    SUMMARY="$("$REPO/.venv/bin/python" - "$MANIFEST" <<'PY'
import hashlib, json, sys
body = open(sys.argv[1], "rb").read()
c = json.loads(body)["counts"]
print(f"events={c['events_seen']} upserted={c['events_upserted']} "
      f"in_territory={c['in_territory']} report_only={c['report_only']} "
      f"engine_ok={c['engine_ok']}/{c['engine_called']} "
      f"sha256={hashlib.sha256(body).hexdigest()}")
PY
)"
else
    SUMMARY="NO MANIFEST (run failed before persist)"
fi

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) run_id=$RUN_ID mode=$ENGINE_MODE exit=$EXIT_CODE $SUMMARY" >> "$RECEIPTS"

exit $EXIT_CODE
