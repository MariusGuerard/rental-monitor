"""Cross-source de-duplication. The same physical listing can show up from
several sources (Zillow email, Redfin email, a PM scraper, Craigslist), each
with its own uid. We collapse them to one alert via a stable key derived from
the street address (preferred) or rounded coordinates.

Key choice:
- "addr:<number> <street> <suffix>" when a street number is present. Unit
  numbers are stripped, so the same home from different sources matches (at the
  small risk of merging two simultaneous units at one address — rare).
- "geo:<lat>,<lon>" rounded to ~11m when there's no street number but we have
  coordinates. (Note: Craigslist's own coordinates differ from our geocoder's,
  so a CL listing may not dedupe against an email listing of the same home.)
- None when we have neither — falls back to per-uid behavior (no dedupe).
"""

import re

from db import Listing

_SUFFIX = {
    "street": "st", "st": "st", "avenue": "ave", "ave": "ave", "av": "ave",
    "boulevard": "blvd", "blvd": "blvd", "drive": "dr", "dr": "dr",
    "court": "ct", "ct": "ct", "lane": "ln", "ln": "ln", "place": "pl",
    "pl": "pl", "road": "rd", "rd": "rd", "terrace": "ter", "ter": "ter",
    "way": "way", "highway": "hwy", "hwy": "hwy",
}


def _normalize_address(addr: str) -> str:
    a = addr.lower()
    # Drop unit markers WITHOUT consuming the comma (so the address still
    # truncates correctly at the first comma below).
    a = re.sub(r"#[^\s,]+", " ", a)                   # drop "#1095R"
    a = re.sub(r"\b(?:apt|unit|suite|ste)\b\.?\s*[^\s,]+", " ", a)  # "apt 2"
    a = a.split(",")[0]                               # keep up to first comma
    a = re.sub(r"[^a-z0-9 ]", " ", a)
    tokens = a.split()
    if tokens and tokens[-1] in _SUFFIX:
        tokens[-1] = _SUFFIX[tokens[-1]]
    return " ".join(tokens).strip()


def key(listing: Listing) -> str | None:
    if listing.address:
        norm = _normalize_address(listing.address)
        if re.match(r"^\d+\s+\S", norm):             # has a street number
            return f"addr:{norm}"
    if listing.lat is not None and listing.lon is not None:
        return f"geo:{round(listing.lat, 4)},{round(listing.lon, 4)}"
    return None
