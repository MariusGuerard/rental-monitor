"""AppFolio detector. Most west-side SF property managers publish vacancies at
`<slug>.appfolio.com/listings` as static HTML with a uniform card structure,
so one parser covers all of them (subdomains in config.APPFOLIO_SUBDOMAINS).

Cards carry the address but no coordinates, so we geocode SF addresses (only
new, plausibly-SF ones) and let the bounding-box filter decide the rest.
"""

import re
import sys

import httpx
from selectolax.parser import HTMLParser

import config
import geo
from db import Listing

_PRICE_RE = re.compile(r"\$\s*([\d,]+)")
_BEDS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*bd", re.I)


def _text(node, sel):
    el = node.css_first(sel)
    return el.text().strip() if el else ""


def _parse_card(card, subdomain: str) -> Listing | None:
    a = card.css_first('a[href*="/listings/detail/"]')
    if not a:
        return None
    href = a.attributes.get("href", "")
    url = f"https://{subdomain}.appfolio.com{href}"

    img = card.css_first("img.js-listing-image") or card.css_first("img")
    address = (img.attributes.get("alt", "").strip() if img else "")

    rent = _text(card, ".js-listing-blurb-rent")
    price = None
    m = _PRICE_RE.search(rent)
    if m:
        price = int(m.group(1).replace(",", ""))

    bb = _text(card, ".js-listing-blurb-bed-bath")
    bedrooms = None
    m = _BEDS_RE.search(bb)
    if m:
        bedrooms = float(m.group(1))
    elif "studio" in bb.lower():
        bedrooms = 0.0

    title = address or _text(card, ".listing-item__title") or "AppFolio listing"
    return Listing(
        uid=f"appfolio:{subdomain}:{href}",
        source=f"pm:{subdomain}",
        title=title,
        url=url,
        price=price,
        bedrooms=bedrooms,
        address=address,
        body=f"{title} {address} {bb}",
    )


def _fetch_subdomain(client: httpx.Client, subdomain: str) -> list[Listing]:
    url = f"https://{subdomain}.appfolio.com/listings"
    try:
        r = client.get(url)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"[appfolio:{subdomain}] fetch failed: {e}", file=sys.stderr)
        return []
    cards = HTMLParser(r.text).css(".listing-item")
    out = []
    for c in cards:
        listing = _parse_card(c, subdomain)
        if listing:
            out.append(listing)
    return out


def fetch(is_seen=None) -> list[Listing]:
    """Return new SF AppFolio listings with coordinates filled in.

    Pre-filters to plausibly-SF, in-budget, new listings before geocoding so
    we don't hammer Nominatim with East Bay inventory.
    """
    out = []
    with httpx.Client(headers=config.HTTP_HEADERS, timeout=25,
                      follow_redirects=True) as client:
        for sub in config.APPFOLIO_SUBDOMAINS:
            for listing in _fetch_subdomain(client, sub):
                if is_seen and is_seen(listing.uid):
                    continue
                if listing.price is not None and listing.price > config.MAX_PRICE:
                    continue
                if not config.SF_ADDRESS_HINT.search(listing.address):
                    continue
                listing.lat, listing.lon = geo.geocode(listing.address)
                out.append(listing)
    return out
