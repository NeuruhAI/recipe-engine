"""
fetch.py — the polite fetcher. Three hard rules, enforced here and nowhere
else so they cannot be bypassed by accident:

  1. robots.txt is checked at runtime (urllib.robotparser) before any page
     fetch; a disallowed path is a hard error, not a skip.
  2. >= 2 seconds between NETWORK fetches (cache hits don't count).
  3. Honest User-Agent. We are not pretending to be a browser.

Page cache: data/cache/{anchor_slug}_{YYYYMMDD}.html — a rerun on the same
day never refetches (the gate test runs the pipeline twice; the second run
must be cache-only on the fetch side).
"""
import time
import urllib.robotparser
from pathlib import Path

import requests

BASE = "https://www.estatesales.net"
USER_AGENT = "recipe-engine/0.1 (Neuruh LLC; contact: jeramieshicks@gmail.com)"
MIN_SPACING_SECONDS = 2.0
TIMEOUT = 30

_robots = None
_last_fetch_at = 0.0


def _robots_ok(url: str) -> bool:
    global _robots
    if _robots is None:
        _robots = urllib.robotparser.RobotFileParser(f"{BASE}/robots.txt")
        _robots.read()
    return _robots.can_fetch(USER_AGENT, url)


def fetch_anchor(slug: str, city_path: str, cache_dir: Path, today: str):
    """
    Fetch one anchor city page, cache-first.
    Returns (html, from_cache: bool, final_url: str|None).
    """
    cache_file = cache_dir / f"{slug}_{today}.html"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8"), True, None

    url = f"{BASE}/{city_path}"
    if not _robots_ok(url):
        raise RuntimeError(f"robots.txt disallows {url} — refusing to fetch")

    global _last_fetch_at
    wait = MIN_SPACING_SECONDS - (time.monotonic() - _last_fetch_at)
    if wait > 0:
        time.sleep(wait)

    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        timeout=TIMEOUT,
        allow_redirects=True,
    )
    _last_fetch_at = time.monotonic()
    resp.raise_for_status()

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(resp.text, encoding="utf-8")
    return resp.text, False, resp.url
