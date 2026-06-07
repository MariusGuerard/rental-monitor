# Outer Sunset Rental Monitor

Watches for new rental listings in **Outer Sunset, SF** and alerts you fast so
you can apply before everyone else.

**Target criteria** (edit in `config.py`):
- West of 40th Ave, between Lincoln Way (N) and Noriega St (S), zip 94122
- Under $6,000/mo
- 2+ bedrooms

## How it works

Each sweep: fetch every source → filter (price / beds / geo box) → dedupe
against `data/seen.sqlite3` → notify on anything new. State is on disk, so
restarts never re-alert.

```
sources/*.py  →  filters.py  →  db.py (dedupe)  →  notify.py
                     ▲
                 config.py (criteria, box, creds)
```

### Sources
- **Craigslist** ✅ live. RSS is blocked, so we scan the static HTML results
  list, then fetch detail pages only for *new* Sunset candidates to get exact
  coordinates + bedrooms (two-stage, polite).
- **AppFolio PM companies** ✅ live. One parser over 9 subdomains
  (`<slug>.appfolio.com/listings`, static HTML): spsf (Structure),
  adventproperties (Advent), abacus, gordon, progressivesf (Progressive),
  amore, rmc, anchorrlty (Anchor), utopiamanagement. Cards have addresses but
  no coords, so `geo.py` geocodes new SF addresses (cached) and the box filter
  decides. Some have no current vacancies at any given time — that's fine.
- **Buildium PM companies** ✅ live. One parser over
  `<slug>.managebuilding.com/Resident/public/rentals`; listing cards expose
  rent/beds/location as data-attributes. Currently: rajproperties (Raj).
- **Propertyware PM companies** ✅ live. Replicates the listing widget's JSON
  API (auth with customer-id → Bearer → `/api/marketing/listings`). Returns
  coordinates directly (no geocoding). Currently: leading (Leading Properties).
  Site IDs in `config.PROPERTYWARE_SITES`.
- **Aggregator email alerts** ✅ built (idle until credentials). Reads saved-
  search alert emails (Zillow/Apartments.com/Redfin/HotPads/Trulia/Realtor) over
  IMAP, unwraps tracking links, extracts price/beds/sqft/address, geocodes when
  possible (else trusts the saved-search prefilter). Setup in
  `sources/email_alerts.py` docstring. Needs `RENTAL_EMAIL_APP_PW` in `.env`.
- **Still to add:** Lingsch (JS/Intellirent + RentCafe), Belong (React SPA) —
  both expected to be covered by the email layer (they syndicate to Zillow).
- **FB / Nextdoor** ⏳ later (resists automation).

Adding more AppFolio/Buildium PMs is trivial: append the subdomain to
`APPFOLIO_SUBDOMAINS` / `BUILDIUM_SUBDOMAINS` in `config.py`.

`geo.py` — Nominatim geocoder with a permanent SQLite cache; respects the
~1 req/sec policy. Add `setup_telegram.py` run once to wire phone alerts.

## Scoring
Each alert carries two 0-100 scores (`scoring.py`, all weights in `config.py`):
- **Value** — quality vs. asking rent. We estimate a "fair" rent from beds +
  detected amenities and compare to the ask (>50 = priced below fair).
- **Fit** — our personal preferences: backyard, garage, ocean view, being
  **north of Kirkham** (lat ≥ 37.7596) and **west of 45th** (lon ≤ −122.5048).
  Location bonuses work for every source (we have coords); amenity bonuses
  depend on listing text (richest on Craigslist, which includes the description).

## Notifications
Primary: **Telegram** (reaches your phone). Fallback: macOS notification +
console/log. To enable Telegram, set these in the LaunchAgent's
`EnvironmentVariables` (or your shell):
- `RENTAL_TELEGRAM_TOKEN` — bot token from @BotFather
- `RENTAL_TELEGRAM_CHATS` — comma-separated chat IDs (you + partner)
- `RENTAL_PACKET_URL` — link to your pre-staged application docs (Drive folder)

## Running

```bash
./.venv/bin/python main.py          # one sweep, notifies on new listings
./.venv/bin/python main.py --seed   # record current listings WITHOUT alerting
```

### Scheduling (launchd, every 5 min)
LaunchAgent: `~/Library/LaunchAgents/com.rentalmonitor.plist`
```bash
launchctl load   ~/Library/LaunchAgents/com.rentalmonitor.plist   # start
launchctl unload ~/Library/LaunchAgents/com.rentalmonitor.plist   # stop
launchctl list | grep rentalmonitor                               # status
```

## Logs
- `data/monitor.log` — per-sweep summary + matches
- `data/launchd.{out,err}.log` — scheduler stdout/stderr
- `data/seen.sqlite3` — dedupe store (delete to reset; then re-`--seed`)

## Tuning
All knobs live in `config.py`: price, beds, the lat/lon bounding box, the
Craigslist candidate hints, and request politeness delays.
```
