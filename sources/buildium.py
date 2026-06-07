"""Buildium detector. Some west-side PMs publish vacancies at
`<slug>.managebuilding.com/Resident/public/rentals` as static HTML. Every
listing is an `<a class="featured-listing">` whose data-* attributes carry
rent, bedrooms, and location — so no detail fetch is needed. Addresses are
geocoded (SF only) and the bounding-box filter decides.
"""

import re
import sys

import httpx
from selectolax.parser import HTMLParser

import config
import geo
from db import Listing


def _text(node, sel):
    el = node.css_first(sel)
    return el.text().strip() if el else ""


def _parse_card(card, subdomain: str) -> Listing | None:
    href = card.attributes.get("href", "")
    if not href:
        return None
    attrs = card.attributes
    price = None
    if attrs.get("data-rent"):
        try:
            price = int(float(attrs["data-rent"]))
        except ValueError:
            pass
    bedrooms = None
    if attrs.get("data-bedrooms"):
        try:
            bedrooms = float(attrs["data-bedrooms"])
        except ValueError:
            pass
    sqft = None
    if attrs.get("data-square-feet"):
        try:
            sqft = float(attrs["data-square-feet"]) or None
        except ValueError:
            pass

    street = _text(card, ".featured-listing__title")
    city = _text(card, ".featured-listing__address")
    # data-location looks like "Berkeley,CA|94704"
    loc = attrs.get("data-location", "")
    address = ", ".join(p for p in (street, city) if p) or loc.replace("|", " ")

    # data-type (e.g. "SingleFamily", "MultiFamily") helps classify the unit.
    dtype = attrs.get("data-type", "")
    return Listing(
        uid=f"buildium:{subdomain}:{href}",
        source=f"pm:{subdomain}",
        title=street or "Buildium listing",
        url=f"https://{subdomain}.managebuilding.com{href}",
        price=price,
        bedrooms=bedrooms,
        sqft=sqft,
        address=address,
        body=f"{street} {city} {loc} {dtype} "
             f"{_text(card, '.featured-listing__features')}",
    )


def _fetch_subdomain(client: httpx.Client, subdomain: str) -> list[Listing]:
    url = f"https://{subdomain}.managebuilding.com/Resident/public/rentals"
    try:
        r = client.get(url)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"[buildium:{subdomain}] fetch failed: {e}", file=sys.stderr)
        return []
    out = []
    for card in HTMLParser(r.text).css("a.featured-listing"):
        listing = _parse_card(card, subdomain)
        if listing:
            out.append(listing)
    return out


def fetch(is_seen=None) -> list[Listing]:
    """Return new, in-budget, SF Buildium listings with coordinates filled in."""
    out = []
    with httpx.Client(headers=config.HTTP_HEADERS, timeout=25,
                      follow_redirects=True) as client:
        for sub in config.BUILDIUM_SUBDOMAINS:
            for listing in _fetch_subdomain(client, sub):
                if is_seen and is_seen(listing.uid):
                    continue
                if listing.price is not None and listing.price > config.MAX_PRICE:
                    continue
                if not config.SF_ADDRESS_HINT.search(listing.body):
                    continue
                listing.lat, listing.lon = geo.geocode(listing.address)
                out.append(listing)
    return out
