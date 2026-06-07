"""Decide whether a listing matches our criteria (price, beds, location)."""

import config
from db import Listing


def _in_box(listing: Listing) -> bool | None:
    """True/False if we have coordinates; None if we can't tell from geo."""
    if listing.lat is None or listing.lon is None:
        return None
    return (config.LAT_MIN <= listing.lat <= config.LAT_MAX
            and config.LON_MIN <= listing.lon <= config.LON_MAX)


def _matches_area_text(listing: Listing) -> bool:
    text = listing.haystack()
    # Strong signal: a specific in-area avenue/street.
    if any(p.search(text) for p in config.AREA_TEXT_PATTERNS):
        return True
    # Weaker: zip code in the text.
    if any(z in text for z in config.ZIP_CODES):
        return True
    return False


def location_matches(listing: Listing) -> bool:
    """Geo box is authoritative when present; otherwise fall back to text.
    Pre-filtered listings (e.g. saved-search email alerts with no extractable
    address) are trusted."""
    box = _in_box(listing)
    if box is not None:
        return box
    if listing.prefiltered:
        return True
    return _matches_area_text(listing)


def matches(listing: Listing) -> tuple[bool, str]:
    """Return (is_match, reason). reason explains a rejection for logging."""
    if listing.price is not None and listing.price > config.MAX_PRICE:
        return False, f"price ${listing.price} > ${config.MAX_PRICE}"
    if listing.bedrooms is not None and listing.bedrooms < config.MIN_BEDROOMS:
        return False, f"{listing.bedrooms}br < {config.MIN_BEDROOMS}br"
    if not location_matches(listing):
        return False, "outside target area"
    return True, "match"
