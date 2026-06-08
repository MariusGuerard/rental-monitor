"""Match criteria and runtime config for the Outer Sunset rental monitor.

Edit the values here to tune what counts as a match. Everything the
detectors and filters need lives in this one file.
"""

import os

# ---------------------------------------------------------------------------
# WHAT WE'RE LOOKING FOR
# ---------------------------------------------------------------------------
MAX_PRICE = 6000          # dollars/month, inclusive upper bound
MIN_BEDROOMS = 2          # at least this many bedrooms

# Geographic target: Outer Sunset, WEST of 40th Ave, between Lincoln Way (N)
# and Noriega St (S). zip 94122.
#
# Bounding box (approximate — avenues/streets are roughly evenly spaced).
# A listing matches the box if it has coordinates inside it. Coordinates are
# the *reliable* filter; the text patterns below are a backup for listings
# that don't carry lat/lon.
LAT_MIN = 37.7530         # ~Noriega St (south edge)
LAT_MAX = 37.7660         # ~Lincoln Way (north edge)
LON_MIN = -122.5120       # ~Great Highway / ocean (west edge)
LON_MAX = -122.4990       # ~40th Ave (east edge); west of 40th => lon < this

ZIP_CODES = ["94122"]

# Text backup matcher: streets/avenues that fall inside the target area.
# Used when a listing has no coordinates. Matched case-insensitively against
# the listing's title + body + address.
import re  # noqa: E402

# Avenues 41–48 (west of 40th), plus La Playa & Great Highway.
_AVES = r"(4[1-8]\s*th\s*ave|la\s*playa|great\s*h(igh)?w(a)?y)"
# Cross streets between Lincoln and Noriega (the named blocks in range).
_CROSS = r"(lincoln|irving|judah|kirkham|lawton|moraga|noriega)"
AREA_TEXT_PATTERNS = [re.compile(_AVES, re.I)]
# A neighborhood mention alone is a weak positive (we still prefer geo).
NEIGHBORHOOD_HINTS = [re.compile(p, re.I) for p in (
    r"outer\s*sunset", r"\bsunset\b", _CROSS,
)]

# ---------------------------------------------------------------------------
# SCORING  (shown in each alert; weights are rough heuristics, tune freely)
# ---------------------------------------------------------------------------
# VALUE = quality-for-the-price. We estimate a "fair" rent from beds + visible
# amenities, compare to the asking rent, and map the gap to 0-100 (>50 means
# priced below fair = a good deal).
VALUE_BASE_RENT = 2400         # $ baseline (0-br) for the area
VALUE_RENT_PER_BR = 1000       # $ per bedroom -> a 2br is ~ $4,400 fair
VALUE_RENT_PER_SQFT = 4.4      # $/sqft/mo, blended 50/50 with the per-br est.
                               # when square footage is known (the big driver)
VALUE_FEATURE_PREMIUMS = {     # $ each amenity adds to the fair estimate
    "garage": 300, "parking": 150, "backyard": 250,
    "ocean_view": 400, "remodeled": 200, "laundry": 100,
}
VALUE_SENSITIVITY = 150        # how sharply the price gap moves the score

# Property type strongly affects fair rent: a house justifies more than an
# apartment of the same beds; an in-law/ADU much less. Multiplies fair rent.
TYPE_MULTIPLIERS = {
    "house": 1.12, "townhouse": 1.08, "flat": 1.05, "duplex": 1.05,
    "condo": 1.0, "apartment": 1.0,
    "adu": 0.82, "inlaw": 0.82, "room": 0.55,
}
DEFAULT_TYPE_MULT = 1.0
# Detected from the listing text; ordered, first match wins (specific first so
# an "in-law unit in a house" is classified in-law, not house).
TYPE_PATTERNS = [
    ("room", re.compile(r"room\s*for\s*rent|private\s*room|\bshared\b", re.I)),
    ("inlaw", re.compile(r"in[\s-]?law", re.I)),
    ("adu", re.compile(r"\badu\b|accessory\s*dwelling|cottage|granny", re.I)),
    ("townhouse", re.compile(r"town\s*house|townhome", re.I)),
    ("house", re.compile(r"single\s*family|\bsfh\b|\bhouse\b|detached|"
                         r"private\s*home|entire\s*(?:home|house|place)|"
                         r"whole\s*(?:home|house)", re.I)),
    ("duplex", re.compile(r"duplex|multi[\s-]?family|\bflat\b|\d\s*unit", re.I)),
    ("condo", re.compile(r"\bcondo", re.I)),
    ("apartment", re.compile(r"apartment|\bapt\b", re.I)),
]

# FIT = how well it matches OUR preferences. Each matched item adds its weight;
# score = points / max_points * 100.
FIT_WEIGHTS = {
    "house": 3,                # prefer a house (incl. townhouse) over a flat/apt
    "backyard": 3,
    "garage": 3,
    "ocean_view": 1,           # nice-to-have, least important
    "north_of_kirkham": 2,     # closer to Lincoln
    "west_of_45th": 2,         # closer to the ocean
}
FIT_NORTH_OF_LAT = 37.7596     # ~Kirkham St; lat >= this == north of Kirkham
FIT_WEST_OF_LON = -122.5048    # ~45th Ave; lon <= this == west of 45th

# Amenity detection (matched against title + address + body/description text).
FEATURE_PATTERNS = {
    "garage": re.compile(r"\bgarage\b", re.I),
    # Qualified off-street parking only — bare "street parking" doesn't count.
    "parking": re.compile(r"\bcarport\b|\bdriveway\b|covered\s*parking|"
                          r"off[\s-]?street\s*parking|deeded\s*parking", re.I),
    "backyard": re.compile(r"back\s*yard|backyard|\byard\b|patio|garden|deck", re.I),
    "ocean_view": re.compile(r"ocean\s*view|sea\s*view|water\s*view|"
                             r"view\s*of\s*the\s*ocean|beach\s*view", re.I),
    "remodeled": re.compile(r"remodel|renovat|updated|newly\s*(remodel|renovat|built)", re.I),
    "laundry": re.compile(r"laundry|washer|\bw/?d\b", re.I),
}

# ---------------------------------------------------------------------------
# SOURCES
# ---------------------------------------------------------------------------
# Craigslist blocks the RSS endpoint (403) but serves a static HTML results
# page. We scan that list, then fetch detail pages for Sunset candidates to
# get exact coordinates + bedrooms. Query params pre-scope price & beds.
CRAIGSLIST_SEARCH_URL = (
    "https://sfbay.craigslist.org/search/sfc/apa"
    f"?max_price={MAX_PRICE}&min_bedrooms={MIN_BEDROOMS}"
)
# Only fetch a detail page when the list-stage location/title hints the
# Sunset, to keep request volume low and polite.
CRAIGSLIST_DETAIL_HINTS = [re.compile(p, re.I) for p in (
    r"sunset", r"parkside", _AVES,
)]

# AppFolio: most west-side PMs publish vacancies at <slug>.appfolio.com/listings
# as static HTML. One parser covers all; the box filter drops non-Sunset units.
# (abacus & gordon are real but often have zero current vacancies.)
APPFOLIO_SUBDOMAINS = [
    "spsf",              # Structure Properties
    "adventproperties",  # Advent Properties
    "abacus",            # Abacus Property Management
    "gordon",            # Gordon Property Management
    "progressivesf",     # Progressive Property Group
    "amore",             # Amore Real Estate
    "rmc",               # Real Management Company
    "anchorrlty",        # Anchor Realty
    "utopiamanagement",  # Utopia Management (national; SF filter gates it)
    "gaetanirealestate", # Gaetani Real Estate (Richmond; live Outer Sunset units)
    "chandlerproperties",  # Chandler Properties (100% SF)
    "amsires",           # AMSI / American Marketing Systems
    "wcpm",              # West Coast Property Management (SF; some commercial)
    "bancalsf",          # BanCal Properties (SF + Peninsula)
    "relisto",           # ReLISTO (SF apartment specialist)
]

# Buildium: same idea, at <slug>.managebuilding.com/Resident/public/rentals.
# Each listing card exposes data-rent/data-bedrooms/data-location attributes.
BUILDIUM_SUBDOMAINS = [
    "rajproperties",     # Raj Properties (Berkeley/Oakland/SF; Outer Sunset page)
    "keyopp",            # KeyOpp Property Management (SF; Richmond activity)
    "thesfpropertymanagement",  # TheSFPropertyManagement (SF, Bay-Area-wide)
]

# Propertyware: the public listing widget hits a JSON API on
# connect.propertyware.com. Each site embeds its IDs in a #pw-listing-widget
# div (data-website-id / data-widget-id / data-customer-id). The API returns
# coordinates (`lattitude`[sic]/`longitude`), so no geocoding needed.
PROPERTYWARE_SITES = [
    {
        "name": "leading",          # Leading Properties (SF, Sunset district)
        "site_url": "https://leading-sf.com/rental-search/",
        "website_id": "183042054",
        "widget_id": "5688",
        "customer_id": "3GKStWdPXKDRYqIXNkSHcKAydtHPjPo",
    },
]

# Email ingestion: read aggregator saved-search alert emails (Zillow,
# Apartments.com, Redfin, HotPads, ...) over IMAP. This is the legal,
# unblockable way to cover sites that hard-block scraping. The headless cron
# needs a Gmail App Password (the account must have 2-Step Verification on).
EMAIL_IMAP_HOST = "imap.gmail.com"
EMAIL_ADDRESS = os.environ.get("RENTAL_EMAIL_ADDR", "")
EMAIL_APP_PASSWORD = os.environ.get("RENTAL_EMAIL_APP_PW", "")
EMAIL_MAILBOX = os.environ.get("RENTAL_EMAIL_MAILBOX", "INBOX")
EMAIL_LOOKBACK_DAYS = 3        # how far back to scan each sweep
# Senders (domain substrings) whose mail we treat as listing alerts.
EMAIL_ALERT_SENDERS = [
    "zillow.com", "apartments.com", "redfin.com", "hotpads.com",
    "trulia.com", "realtor.com", "padmapper.com",
]
# The saved searches already constrain area/price/beds, so when we can't
# extract an address to geo-verify, trust the prefilter and alert anyway.
EMAIL_TRUST_SAVED_SEARCH = True

# AppFolio listings carry an address but no coordinates, so we geocode. Only
# addresses that look like SF are geocoded (keeps Nominatim calls minimal).
SF_ADDRESS_HINT = re.compile(r"san\s*francisco|\b9411[0-9]\b|\b9412[0-9]\b", re.I)
GEOCODE_DELAY = 1.1   # seconds between live Nominatim calls (their rate limit)
NOMINATIM_UA = "rental-monitor/1.0 (personal hobby project)"

# Browser-like headers; Craigslist 403s bare clients.
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
# Politeness: seconds to sleep between detail-page fetches.
DETAIL_FETCH_DELAY = 1.0

# ---------------------------------------------------------------------------
# NOTIFICATIONS
# ---------------------------------------------------------------------------
# Telegram is the primary channel (reaches your phone instantly). Leave the
# token unset to fall back to a native macOS notification + console log.
TELEGRAM_BOT_TOKEN = os.environ.get("RENTAL_TELEGRAM_TOKEN", "")
# Comma-separated chat IDs (you + partner). e.g. "12345,67890"
TELEGRAM_CHAT_IDS = [
    c.strip() for c in os.environ.get("RENTAL_TELEGRAM_CHATS", "").split(",")
    if c.strip()
]

# Link to your pre-staged application packet (Drive folder, etc.). Included in
# every alert so you can apply in minutes. Fill this in once it's ready.
APPLICATION_PACKET_URL = os.environ.get("RENTAL_PACKET_URL", "")

# ---------------------------------------------------------------------------
# RUNTIME
# ---------------------------------------------------------------------------
import pathlib  # noqa: E402
BASE_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "seen.sqlite3"
LOG_PATH = BASE_DIR / "data" / "monitor.log"
