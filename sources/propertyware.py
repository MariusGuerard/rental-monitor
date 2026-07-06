"""Propertyware detector. PMs using Propertyware embed a JS listing widget that
calls a JSON API on connect.propertyware.com. We replicate that: authenticate
with the site's customer-id (an API key) to get a Bearer token, then fetch the
marketing listings. The API returns coordinates directly, so no geocoding.

Site IDs live in config.PROPERTYWARE_SITES (read from each site's
`#pw-listing-widget` div: data-website-id / data-widget-id / data-customer-id).
"""

import sys

import httpx

import config
import health
from db import Listing

API_BASE = "https://connect.propertyware.com"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _auth(client: httpx.Client, customer_id: str) -> str | None:
    r = client.post(f"{API_BASE}/auth/apikey",
                    headers={"Authorization": f"Apikey {customer_id}"})
    r.raise_for_status()
    key = r.json().get("access-key", "")
    # The API returns the key already prefixed with "Bearer "; normalize so we
    # always send a single, well-formed Bearer header.
    if key.startswith("Bearer "):
        key = key[len("Bearer "):]
    return f"Bearer {key}" if key else None


def _parse(item: dict, site: dict) -> Listing:
    parts = [item.get("address"), item.get("city"), item.get("state"),
             item.get("zip")]
    address = ", ".join(str(p) for p in parts if p)
    beds = _num(item.get("no_bedrooms"))
    title = item.get("marketing_name") or item.get("name") or address \
        or "Propertyware listing"
    pid = item.get("id", "")
    return Listing(
        uid=f"propertyware:{site['name']}:{pid}",
        source=f"pm:{site['name']}",
        title=title,
        url=f"{site['site_url']}?propertyId={pid}",
        price=_num(item.get("target_rent")) and int(_num(item.get("target_rent"))),
        bedrooms=beds,
        sqft=_num(item.get("total_area")),
        lat=_num(item.get("lattitude") or item.get("latitude")),
        lon=_num(item.get("longitude")),
        address=address,
        body=f"{title} {address}",
    )


def _fetch_site(client: httpx.Client, site: dict) -> list[Listing]:
    try:
        token = _auth(client, site["customer_id"])
        if not token:
            return []
        r = client.get(
            f"{API_BASE}/api/marketing/listings",
            params={"website_id": site["website_id"],
                    "widget_id": site["widget_id"]},
            headers={"Authorization": token},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"[propertyware:{site['name']}] fetch failed: {e}",
              file=sys.stderr)
        health.error(f"propertyware:{site['name']}", e)
        return []
    items = data if isinstance(data, list) else data.get("data", [])
    return [_parse(it, site) for it in items if isinstance(it, dict)]


def fetch(is_seen=None) -> list[Listing]:
    """Return new, in-budget Propertyware listings (coordinates included)."""
    out = []
    headers = {"User-Agent": config.HTTP_HEADERS["User-Agent"],
               "Content-Type": "application/json"}
    with httpx.Client(timeout=25, follow_redirects=True,
                      headers=headers) as client:
        for site in config.PROPERTYWARE_SITES:
            for listing in _fetch_site(client, site):
                if is_seen and is_seen(listing.uid):
                    continue
                if listing.price is not None and listing.price > config.MAX_PRICE:
                    continue
                out.append(listing)
    return out
