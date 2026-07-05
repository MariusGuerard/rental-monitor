"""Haoshiyou (好室友) detector — the Bay Area's largest Chinese-language rental
platform, aggregating ~100 WeChat rental groups. Many Outer Sunset owner units
rent here FIRST, before Craigslist/Zillow. The site is server-rendered
(Next.js), so we scrape the homepage's recent-listing cards directly.

Card text looks like:
  "招租 旧金山 Sunset 独立两房一卫出租 $3,800/月 2天前 San Francisco, 94122"
We keep only SF-Sunset WHOLE-UNIT listings (dropping East Bay, other SF zips,
and room/shared rentals like 单间/主卧/合租), and parse price + beds from the
mixed Chinese/English title. No exact address is given, so these are trusted as
pre-filtered (the Sunset signal is the gate) and beds/price still apply.
"""

import re
import sys

import httpx
from selectolax.parser import HTMLParser

import config
from db import Listing

BASE = "https://haoshiyou.org"

_WANTED = re.compile(r"求租|找房|想租|求&|wanted", re.I)          # seeking, not offering
_ROOM = re.compile(r"单间|单房|主卧|次卧|雅房|床位|合租|室友|分租|"
                   r"一间|房间出租|招室友|限女|限男|only\s*(fe)?male", re.I)
_EASTBAY_ZIP = re.compile(r"\b94[5-9]\d{2}\b")                    # 945xx-949xx East Bay
_SUNSET_ZIP = re.compile(r"\b9411[68]\b|\b94122\b")              # Sunset/Parkside
_SUNSET_KW = re.compile(r"日落|外日落|sunset|parkside", re.I)
_SF_KW = re.compile(r"旧金山|三藩市|san\s*francisco|\bSF\b", re.I)
_PRICE = re.compile(r"\$\s*([\d,]{3,5})")
_ZIP = re.compile(r"\b(9\d{4})\b")

_BEDS_NUM = {"一": 1, "两": 2, "兩": 2, "二": 2, "三": 3, "四": 4}
_BEDS_RE = re.compile(r"(\d|[一两兩二三四])\s*(?:房|室|[Bb]\b|bed)", re.I)


def _sunset_gate(text: str) -> bool:
    """SF Outer Sunset (or adjacent Parkside), excluding East Bay."""
    if _EASTBAY_ZIP.search(text):
        return False
    if _SUNSET_ZIP.search(text):
        return True
    return bool(_SUNSET_KW.search(text) and _SF_KW.search(text))


def _beds(text: str):
    m = _BEDS_RE.search(text)
    if not m:
        return None
    tok = m.group(1)
    return float(_BEDS_NUM.get(tok, tok)) if tok.isdigit() or tok in _BEDS_NUM \
        else None


def fetch(is_seen=None) -> list[Listing]:
    try:
        r = httpx.get(BASE, headers=config.HTTP_HEADERS, timeout=20,
                      follow_redirects=True)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"[haoshiyou] fetch failed: {e}", file=sys.stderr)
        return []

    out = []
    for a in HTMLParser(r.text).css("a"):
        href = a.attributes.get("href", "")
        if not href.startswith("/l/"):
            continue
        text = " ".join(a.text(separator=" ").split())
        if _WANTED.search(text) or _ROOM.search(text):
            continue
        if not _sunset_gate(text):
            continue
        uid = f"haoshiyou:{href.rsplit('/', 1)[-1]}"
        if is_seen and is_seen(uid):
            continue
        price = None
        m = _PRICE.search(text)
        if m:
            price = int(m.group(1).replace(",", ""))
        mz = _ZIP.search(text)
        addr = f"San Francisco, CA {mz.group(1)}" if mz else "San Francisco, CA"
        out.append(Listing(
            uid=uid, source="haoshiyou", title=text[:80],
            url=f"{BASE}{href}", price=price, bedrooms=_beds(text),
            address=addr, body=text, prefiltered=True,
        ))
    return out
