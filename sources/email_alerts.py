"""Email-ingestion detector. Reads aggregator saved-search alert emails
(Zillow, Apartments.com, Redfin, HotPads, ...) over IMAP and turns each listing
link into a Listing. This is the legal, unblockable way to cover sites that
hard-block scraping — the saved search does the filtering; we read it instantly.

Setup (one-time, on the throwaway Gmail account):
  1. Turn on 2-Step Verification, then create an App Password.
  2. Put RENTAL_EMAIL_ADDR / RENTAL_EMAIL_APP_PW in .env.
  3. Create saved-search alerts on each site (Outer Sunset, 2BR+, <$6k) sent to
     that address.

Per-provider field extraction is best-effort and will be tuned against real
emails; until then, links are always forwarded (the saved search guarantees
relevance) with whatever price/beds/address we can parse.
"""

import email
import imaplib
import re
import sys
import urllib.parse
from collections import Counter
from datetime import date, timedelta
from email.header import decode_header, make_header

from selectolax.parser import HTMLParser

import config
import geo
from db import Listing

# (provider, id-regex, canonical-url-builder) — matched against each link.
_PROVIDERS = [
    ("zillow",
     re.compile(r"zillow\.com/(?:homedetails|homes)/[^\"'\s]*?(\d{5,})_zpid", re.I),
     lambda i: f"https://www.zillow.com/homedetails/{i}_zpid/"),
    ("redfin",
     re.compile(r"redfin\.com/[A-Z]{2}/[^\"'\s]+/home/(\d+)", re.I), None),
    ("apartments",
     re.compile(r"apartments\.com/([a-z0-9\-]+/[a-z0-9]+)/", re.I),
     lambda i: f"https://www.apartments.com/{i}/"),
    ("hotpads",
     re.compile(r"hotpads\.com/([a-z0-9\-]+)/pad", re.I), None),
    ("trulia",
     re.compile(r"trulia\.com/(?:home|p|building)/[^\"'\s]*?-(\d{4,})", re.I), None),
    ("realtor",
     re.compile(r"realtor\.com/realestateandhomes-detail/[^\"'\s]*?(M?\d{5,})", re.I), None),
]

_PRICE_RE = re.compile(r"\$\s*([\d,]{3,7})")
_BEDS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:bd|beds?|bedrooms?)\b", re.I)
_SQFT_RE = re.compile(r"([\d,]{3,5})\s*(?:sq\s?\.?\s?ft|sqft|square\s*feet|ft2|ft²)", re.I)
_ADDR_RE = re.compile(
    r"\d+\s+[A-Za-z0-9 .#\-]+,\s*(?:San\s*Francisco|SF),\s*CA\s*9\d{4}", re.I)
# Suffix-anchored street address (avoids grabbing "1,018 Sq. Ft." etc.).
_ADDR_SUFFIX = re.compile(
    r"(?<![\d,.])\b\d{1,5}\s+"
    r"(?!sq\b|sq\.|square|ft\b|ft\.)"          # not "688 Sq. Ft. ..."
    r"[A-Za-z0-9'.\- ]{1,30}?"
    r"(?:St|Street|Ave|Avenue|Blvd|Way|Dr|Drive|Ct|Court|Ln|Lane|Pl|Place|"
    r"Ter|Terrace|Rd|Road)\b\.?,?\s*San\s*Francisco(?:,?\s*CA)?(?:\s*9\d{4})?",
    re.I)


def _candidate_urls(href: str):
    """Yield the href plus any URL embedded in a tracking/redirect wrapper."""
    if not href:
        return
    yield href
    unq = urllib.parse.unquote(href)
    if unq != href:
        yield unq
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        for key in ("url", "u", "redirect", "target", "link"):
            for v in qs.get(key, []):
                yield urllib.parse.unquote(v)
    except Exception:  # noqa: BLE001
        pass


def _match_provider(href: str):
    for cand in _candidate_urls(href):
        for name, rx, build in _PROVIDERS:
            m = rx.search(cand)
            if m:
                lid = m.group(1)
                url = build(lid) if build else cand.split("?")[0]
                return name, lid, url
    return None


def _email_text(msg) -> tuple[str, str]:
    """Return (html, plain) bodies, concatenated across parts."""
    html_parts, text_parts = [], []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain"):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
        except Exception:  # noqa: BLE001
            continue
        (html_parts if ctype == "text/html" else text_parts).append(decoded)
    return "\n".join(html_parts), "\n".join(text_parts)


def _subject_address(subject: str) -> str:
    """Pull the property address out of a Zillow alert subject, e.g.
    'Fwd: 1295 47th Ave #1095R just listed in ...' -> '1295 47th Ave #1095R'."""
    s = re.sub(r"^\s*(fwd:|re:)\s*", "", subject, flags=re.I).strip()
    m = re.match(r"(.+?)\s+(?:just listed|just rented|price|is now|in '|\()",
                 s, re.I)
    addr = (m.group(1) if m else "").strip()
    if addr and not re.search(r"\bCA\b|San\s*Francisco", addr, re.I):
        addr += ", San Francisco, CA"
    return addr


def _parse_zillow(subject: str, html: str, visible: str) -> list[Listing]:
    """Zillow wraps every link in opaque click-tracking, but the zpid is in the
    HTML. The newly-listed property is named in the subject and its zpid is the
    most-referenced one. We surface that primary listing (the time-sensitive
    one) with full data; digest 'more results' cards are skipped for now."""
    zpids = re.findall(r"(\d+)_zpid", html)
    if not zpids:
        return []
    primary = Counter(zpids).most_common(1)[0][0]
    address = _subject_address(subject)
    geo_addr = re.sub(r"#\S+", "", address).strip(" ,") if address else ""

    # Price/beds: the featured (just-listed) card renders first, so the first
    # tokens are the primary's. sqft is NOT extracted — in a digest the first
    # sqft token may belong to another card, and wrong size skews Value.
    price = beds = None
    mp = re.search(r"\$([\d,]{3,7})\s*/mo", visible) or \
        re.search(r"\$([\d,]{3,7})", visible)
    if mp:
        price = int(mp.group(1).replace(",", ""))
    mb = re.search(r"(\d+(?:\.\d+)?)\s*bd", visible, re.I)
    if mb:
        beds = float(mb.group(1))

    lat, lon = geo.geocode(geo_addr) if geo_addr else (None, None)
    return [Listing(
        uid=f"email:zillow:{primary}",
        source="email:zillow",
        title=address or "Zillow new listing",
        url=f"https://www.zillow.com/homedetails/{primary}_zpid/",
        price=price, bedrooms=beds, lat=lat, lon=lon,
        address=address,
        body=f"{address} {visible[:400]}",
        prefiltered=lat is None,
    )]


def _redfin_link(html: str) -> str:
    best = ""
    for a in HTMLParser(html).css("a"):
        h = a.attributes.get("href", "")
        if not h.lower().startswith("http") or "redfin.com" not in h:
            continue
        if re.search(r"unsubscrib|preferenc|manage|privacy|help|/about",
                     h, re.I):
            continue
        if "redmail" in h or "/click" in h:   # tracking link -> the listing
            return h
        best = best or h
    return best


def _parse_redfin(subject: str, html: str, visible: str) -> list[Listing]:
    """Redfin 'New rental' alerts are single-listing: address + rich amenity
    text live in the body; links are opaque tracking. We pull the address
    (suffix-anchored), price (subject), beds, and keep the description for
    scoring. 'Recommended' subjects are dropped here too (defense in depth)."""
    if "recommended" in subject.lower():
        return []
    m = _ADDR_SUFFIX.search(visible)
    address = m.group(0).strip(" ,") if m else ""
    geo_addr = re.sub(r"#\S+", "", address).strip(" ,")
    if geo_addr and not re.search(r"\bCA\b", geo_addr, re.I):
        geo_addr += ", CA"

    price = beds = None
    mp = _PRICE_RE.search(subject) or _PRICE_RE.search(visible)
    if mp:
        price = int(mp.group(1).replace(",", ""))
    mb = re.search(r"(\d+(?:\.\d+)?)\s*(?:bd|beds?|bedroom)", visible, re.I)
    if mb:
        beds = float(mb.group(1))

    # Not a listing alert (e.g. account/welcome email) if we found neither.
    if not address and price is None:
        return []

    lat, lon = geo.geocode(geo_addr) if geo_addr else (None, None)
    slug = re.sub(r"[^a-z0-9]", "", (address or subject).lower())[:40]
    return [Listing(
        uid=f"email:redfin:{slug}",
        source="email:redfin",
        title=address or "Redfin new listing",
        url=_redfin_link(html) or "https://www.redfin.com",
        price=price, bedrooms=beds, lat=lat, lon=lon,
        address=address,
        body=f"{address} {visible[:600]}",
        prefiltered=lat is None,
    )]


_ZUMPER_ADDR = re.compile(
    r"\d{1,5}\s+(?!(?:bed|bath|sq|studio)s?\b)"   # not "1 Bath 1380 ..."
    r"[A-Za-z0-9 .#'\-]+?,\s*San\s*Francisco", re.I)


def _first_link(html: str, host: str) -> str:
    for a in HTMLParser(html).css("a"):
        h = a.attributes.get("href", "")
        if host in h:
            return h
    return ""


def _parse_zumper(html: str, visible: str) -> list[Listing]:
    """Zumper emails are multi-listing digests with opaque click-tracking, but
    the visible text clusters cleanly per unit: '$3,600 1 Bed 1 Bath <addr>,
    San Francisco'. Split on '$' and pull price/beds/address from each chunk."""
    link = _first_link(html, "clicks.zumper.com") or "https://www.zumper.com"
    out, seen = [], set()
    for chunk in visible.split("$")[1:]:
        mp = re.match(r"\s*([\d,]{3,6})", chunk)
        ma = _ZUMPER_ADDR.search(chunk)
        if not (mp and ma):
            continue
        address = re.sub(r"\s+", " ", ma.group(0)).strip()
        if address in seen:
            continue
        seen.add(address)
        price = int(mp.group(1).replace(",", ""))
        mb = re.search(r"(\d+(?:\.\d+)?)\s*Bed", chunk, re.I)
        beds = float(mb.group(1)) if mb else (
            0.0 if re.search(r"studio", chunk, re.I) else None)
        geo_addr = re.sub(r"#\S+", "", address).strip(" ,")
        if not re.search(r"\bCA\b", geo_addr, re.I):
            geo_addr += ", CA"
        lat, lon = geo.geocode(geo_addr)
        slug = re.sub(r"[^a-z0-9]", "", address.lower())[:40]
        out.append(Listing(
            uid=f"email:zumper:{slug}", source="email:zumper",
            title=address, url=link, price=price, bedrooms=beds,
            lat=lat, lon=lon, address=address, body=address,
            prefiltered=lat is None,
        ))
    return out


def _parse_message(msg, sender: str) -> list[Listing]:
    html_body, plain = _email_text(msg)
    tree = HTMLParser(html_body) if html_body else None
    visible = (tree.body.text(separator=" ") if tree and tree.body else "") \
        + " " + plain
    subject = str(make_header(decode_header(msg.get("Subject", ""))))

    # Zillow wraps links in click-tracking; handle it specially via zpid.
    if "_zpid" in html_body or "zillow.com" in (sender + visible).lower():
        zres = _parse_zillow(subject, html_body, " ".join(visible.split()))
        if zres:
            return zres
    # Zumper: multi-listing digest, parse every unit from the text clusters.
    if "zumper" in (sender + visible[:300]).lower():
        zres = _parse_zumper(html_body, " ".join(visible.split()))
        if zres:
            return zres
    # Redfin also wraps links opaquely; parse address + amenities from body.
    if "redfin" in (sender + subject + visible[:300]).lower():
        rres = _parse_redfin(subject, html_body, " ".join(visible.split()))
        if rres:
            return rres

    # Collect unique listing links.
    found = {}
    if tree:
        for a in tree.css("a"):
            hit = _match_provider(a.attributes.get("href", ""))
            if hit:
                found[(hit[0], hit[1])] = hit[2]
    for m in re.finditer(r"https?://[^\s\"'<>]+", plain):  # plain-text links
        hit = _match_provider(m.group(0))
        if hit:
            found.setdefault((hit[0], hit[1]), hit[2])
    if not found:
        return []

    # Best-effort fields (reliable only when the email is a single listing).
    single = len(found) == 1
    price = beds = sqft = None
    address = ""
    if single:
        mp = _PRICE_RE.search(visible)
        if mp:
            price = int(mp.group(1).replace(",", ""))
        mb = _BEDS_RE.search(visible)
        if mb:
            beds = float(mb.group(1))
        ms = _SQFT_RE.search(visible)
        if ms:
            sqft = float(ms.group(1).replace(",", ""))
        ma = _ADDR_RE.search(visible)
        if ma:
            address = ma.group(0).strip()

    out = []
    for (prov, lid), url in found.items():
        lat = lon = None
        prefiltered = True
        if address:
            lat, lon = geo.geocode(address)
            if lat is not None:
                prefiltered = False  # we can geo-verify; don't blindly trust
        out.append(Listing(
            uid=f"email:{prov}:{lid}",
            source=f"email:{prov}",
            title=address or f"{prov.title()} listing (from saved-search alert)",
            url=url,
            price=price,
            bedrooms=beds,
            sqft=sqft,
            lat=lat,
            lon=lon,
            address=address,
            body=f"{address} {visible[:500]}" if single else address,
            prefiltered=prefiltered,
        ))
    return out


def fetch(is_seen=None) -> list[Listing]:
    if not (config.EMAIL_ADDRESS and config.EMAIL_APP_PASSWORD):
        return []
    out = []
    try:
        M = imaplib.IMAP4_SSL(config.EMAIL_IMAP_HOST)
        M.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
        M.select(config.EMAIL_MAILBOX, readonly=True)
        since = (date.today() - timedelta(days=config.EMAIL_LOOKBACK_DAYS)) \
            .strftime("%d-%b-%Y")
        typ, data = M.search(None, "SINCE", since)
        for num in data[0].split():
            typ, msgdata = M.fetch(num, "(RFC822)")
            if not msgdata or not msgdata[0]:
                continue
            msg = email.message_from_bytes(msgdata[0][1])
            sender = str(make_header(decode_header(msg.get("From", "")))).lower()
            # We don't gate on sender: forwarded alerts (e.g. auto-forwarded
            # from a personal inbox) may carry a different From. _parse_message
            # returns nothing unless it finds real listing links, so non-alert
            # mail is naturally ignored.
            for listing in _parse_message(msg, sender):
                if is_seen and is_seen(listing.uid):
                    continue
                out.append(listing)
        M.logout()
    except Exception as e:  # noqa: BLE001
        print(f"[email] error: {e}", file=sys.stderr)
    return out
