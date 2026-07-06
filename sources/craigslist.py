"""Craigslist SF apartments detector via the static HTML search page.

Craigslist 403s the RSS endpoint, so we parse the static results list
(`li.cl-static-search-result`) for title/url/price/location, then fetch a
detail page ONLY for new Sunset candidates to extract exact coordinates and
bedroom count. Detail fetches are gated by an `is_seen` callback so we never
re-fetch a posting we've already processed.
"""

import html
import re
import sys
import time

import httpx
from selectolax.parser import HTMLParser

import config
import health
from db import Listing

_PRICE_RE = re.compile(r"\$\s*([\d,]+)")
_BEDS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*BR", re.I)
_LAT_RE = re.compile(r'data-latitude="([\d.\-]+)"')
_LON_RE = re.compile(r'data-longitude="([\d.\-]+)"')
_SQFT_RE = re.compile(r"(\d{3,4})\s*ft2", re.I)


def _client() -> httpx.Client:
    return httpx.Client(headers=config.HTTP_HEADERS, timeout=20,
                        follow_redirects=True)


def _scan_list(client: httpx.Client) -> list[Listing]:
    """Cheap stage: parse the static results list (no geo/beds yet)."""
    try:
        r = client.get(config.CRAIGSLIST_SEARCH_URL)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"[craigslist] list fetch failed: {e}", file=sys.stderr)
        health.error("craigslist", e)
        return []

    out = []
    for li in HTMLParser(r.text).css("li.cl-static-search-result"):
        a = li.css_first("a")
        if not a:
            continue
        url = a.attributes.get("href", "")
        title = html.unescape(li.attributes.get("title", "")).strip()
        price = None
        pe = li.css_first(".price")
        if pe:
            m = _PRICE_RE.search(pe.text())
            if m:
                price = int(m.group(1).replace(",", ""))
        loc = li.css_first(".location")
        location = loc.text().strip() if loc else ""
        out.append(Listing(
            uid=f"craigslist:{url}", source="craigslist", title=title,
            url=url, price=price, address=location,
            body=f"{title} {location}",
        ))
    return out


def _enrich(client: httpx.Client, listing: Listing) -> Listing:
    """Detail stage: add coordinates, bedrooms, and the real map address."""
    try:
        r = client.get(listing.url)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"[craigslist] detail fetch failed {listing.url}: {e}",
              file=sys.stderr)
        return listing
    d = r.text
    m = _LAT_RE.search(d)
    if m:
        listing.lat = float(m.group(1))
    m = _LON_RE.search(d)
    if m:
        listing.lon = float(m.group(1))

    tree = HTMLParser(d)
    attr_text = " ".join(a.text() for a in tree.css(".attrgroup"))
    m = _BEDS_RE.search(attr_text)
    if m:
        listing.bedrooms = float(m.group(1))
    elif re.search(r"\bstudio\b", attr_text, re.I):
        listing.bedrooms = 0.0
    m = _SQFT_RE.search(attr_text) or _SQFT_RE.search(listing.title)
    if m:
        listing.sqft = float(m.group(1))
    addr = tree.css_first(".mapaddress")
    if addr:
        listing.address = addr.text().strip()
    # Posting description carries amenity language (backyard, ocean view, ...)
    # that the attrgroups miss — include it so scoring can see it.
    desc_el = tree.css_first("#postingbody")
    desc = desc_el.text().strip() if desc_el else ""
    listing.body = f"{listing.title} {listing.address} {attr_text} {desc}"
    return listing


def _is_candidate(listing: Listing) -> bool:
    if listing.price is not None and listing.price > config.MAX_PRICE:
        return False
    return any(p.search(listing.body) for p in config.CRAIGSLIST_DETAIL_HINTS)


def fetch(is_seen=None) -> list[Listing]:
    """Return enriched, newly-seen Sunset candidate listings.

    `is_seen(uid) -> bool` lets us skip detail fetches for postings we've
    already handled. Without it, every Sunset candidate is enriched.
    """
    with _client() as client:
        results = _scan_list(client)
        enriched = []
        for listing in results:
            if not _is_candidate(listing):
                continue
            if is_seen and is_seen(listing.uid):
                continue
            enriched.append(_enrich(client, listing))
            time.sleep(config.DETAIL_FETCH_DELAY)
    return enriched
