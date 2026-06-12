"""
parse.py — JSON-LD SaleEvent extraction (the validated approach: pages are
server-rendered, no JS execution needed).

listing_id = trailing numeric path segment of the SaleEvent url
(https://www.estatesales.net/IN/Hammond/46324/4937331 -> 4937331).
That id is the idempotency anchor for everything downstream:
  recipe.sale_events PK, parcel_apn = estate-{listing_id},
  owner_hash = sha256(listing_id).
"""
import hashlib
import json
import re

LD_JSON_RE = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.S,
)
LISTING_ID_RE = re.compile(r"/(\d+)/?$")


def parse_sale_events(html: str):
    """Yield normalized event dicts from every JSON-LD SaleEvent on a page."""
    for block in LD_JSON_RE.findall(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "SaleEvent":
                continue
            url = item.get("url") or ""
            m = LISTING_ID_RE.search(url.strip())
            if not m:
                continue  # no listing id -> no stable key -> skip, never guess
            listing_id = m.group(1)

            loc = item.get("location") or {}
            addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
            org = item.get("organizer") or {}

            yield {
                "listing_id": listing_id,
                "url": url.strip(),
                "title": (item.get("name") or "").strip() or None,
                "description": (item.get("description") or "").strip() or None,
                "organizer_name": (org.get("name") or "").strip() or None,
                "organizer_phone": (org.get("telephone") or "").strip() or None,
                "start_at": item.get("startDate") or None,
                "end_at": item.get("endDate") or None,
                "street_address": (addr.get("streetAddress") or "").strip() or None,
                "city": (addr.get("addressLocality") or "").strip() or None,
                "state": (addr.get("addressRegion") or "").strip() or None,
                "postal_code": (addr.get("postalCode") or "").strip() or None,
                "payload": item,  # full raw JSON-LD, kept verbatim
            }


def canon_keys(listing_id: str):
    """The canon keys — single definition, used by persist and engine push."""
    return {
        "parcel_apn": f"estate-{listing_id}",
        "owner_hash": hashlib.sha256(listing_id.encode("utf-8")).hexdigest(),
    }
