"""
engine.py — the REAL LandOS engine client. No mock lives in this repo.

POST /evaluate with X-LANDOS-SECRET. The engine find-or-creates the
identity node on (tenant_id, parcel_apn, owner_hash) — ON CONFLICT returns
the existing id, which is exactly the no-forking guarantee the open gate
verifies. Only in-territory events are ever pushed; report-only stays
report-only.

Distress/owner/arbitrage inputs are sent as engine defaults (all
unknown/false): an estate sale is a SIGNAL for the R4 lane but is not
verified probate, and unverified signals do not enter canon as data.
Signal enrichment is a later pass.
"""
import requests


class EngineError(RuntimeError):
    pass


class LandosEngine:
    def __init__(self, base_url: str, secret: str, tenant_id: str):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.tenant_id = tenant_id

    def health(self) -> dict:
        r = requests.get(f"{self.base_url}/health", timeout=10)
        r.raise_for_status()
        return r.json()

    def evaluate(self, parcel_apn: str, owner_hash: str,
                 county_slug: str, state: str) -> dict:
        """Returns the engine response incl. node_id. Raises on any non-200."""
        resp = requests.post(
            f"{self.base_url}/evaluate",
            headers={"X-LANDOS-SECRET": self.secret},
            json={
                "tenant_id": self.tenant_id,
                "parcel_apn": parcel_apn,
                "owner_hash": owner_hash,
                "county": county_slug,
                "state": state,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise EngineError(
                f"/evaluate {resp.status_code} for {parcel_apn}: {resp.text[:300]}"
            )
        return resp.json()
