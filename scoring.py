"""Score a listing on two axes shown in each alert:

- VALUE  (0-100): quality-for-the-price. >50 == priced below our fair estimate.
- FIT    (0-100): how well it matches our personal preferences (amenities +
                  being north of Kirkham / west of 45th).

All knobs live in config.py. Returns plain data so notify.py can format it.
"""

from dataclasses import dataclass, field

import config
from db import Listing


@dataclass
class Score:
    value: int | None = None          # 0-100, None if no price to judge
    value_reason: str = ""
    fit: int = 0                      # 0-100
    fit_hits: list[str] = field(default_factory=list)
    fit_misses: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)


def _features(listing: Listing) -> dict[str, bool]:
    text = listing.haystack()
    return {name: bool(p.search(text))
            for name, p in config.FEATURE_PATTERNS.items()}


def _prop_type(listing: Listing) -> str | None:
    text = listing.haystack()
    for name, pat in config.TYPE_PATTERNS:
        if pat.search(text):
            return name
    return None


def _value(listing: Listing, feats: dict[str, bool],
           ptype: str | None) -> tuple[int | None, str]:
    beds = listing.bedrooms if listing.bedrooms is not None else 1
    bedroom_fair = config.VALUE_BASE_RENT + config.VALUE_RENT_PER_BR * beds
    # Blend the per-bedroom estimate with a per-sqft one when size is known —
    # square footage is the dominant value driver (a 600sqft in-law and a
    # 1000sqft house are not the same "2BR").
    if listing.sqft:
        sqft_fair = config.VALUE_RENT_PER_SQFT * listing.sqft
        fair = (bedroom_fair + sqft_fair) / 2
    else:
        fair = bedroom_fair
    for name, premium in config.VALUE_FEATURE_PREMIUMS.items():
        if feats.get(name):
            fair += premium
    # Property type: a house justifies more rent than an apartment; an
    # in-law/ADU much less. So the same price is better/worse value accordingly.
    fair *= config.TYPE_MULTIPLIERS.get(ptype, config.DEFAULT_TYPE_MULT)

    extra = []
    if listing.sqft:
        extra.append(f"{listing.sqft:g}sqft")
    if ptype:
        extra.append(ptype)
    suffix = f" ({', '.join(extra)})" if extra else ""

    if not listing.price:
        return None, f"no price shown (fair est. ~${fair:,.0f}){suffix}"
    deal = (fair - listing.price) / fair
    score = max(0, min(100, round(50 + deal * config.VALUE_SENSITIVITY)))
    return score, f"${listing.price:,}/mo vs ~${fair:,.0f} fair{suffix}"


def _fit(listing: Listing, feats: dict[str, bool],
         ptype: str | None) -> tuple[int, list, list]:
    north = listing.lat is not None and listing.lat >= config.FIT_NORTH_OF_LAT
    west = listing.lon is not None and listing.lon <= config.FIT_WEST_OF_LON
    is_house = ptype in ("house", "townhouse")
    checks = [
        ("house", "house", is_house),
        ("backyard", "backyard", feats.get("backyard", False)),
        ("garage", "garage", feats.get("garage", False)),
        ("ocean_view", "ocean view", feats.get("ocean_view", False)),
        ("north_of_kirkham", "N of Kirkham", north),
        ("west_of_45th", "W of 45th", west),
    ]
    pts = 0
    hits, misses = [], []
    for key, label, ok in checks:
        if ok:
            pts += config.FIT_WEIGHTS.get(key, 0)
            hits.append(label)
        else:
            misses.append(label)
    max_pts = sum(config.FIT_WEIGHTS.values()) or 1
    return round(pts / max_pts * 100), hits, misses


def score(listing: Listing) -> Score:
    feats = _features(listing)
    ptype = _prop_type(listing)
    value, value_reason = _value(listing, feats, ptype)
    fit, hits, misses = _fit(listing, feats, ptype)
    present = [n.replace("_", " ") for n, ok in feats.items() if ok]
    return Score(value=value, value_reason=value_reason, fit=fit,
                 fit_hits=hits, fit_misses=misses, features=present)
