"""One poll cycle: fetch every source, filter, dedupe, notify.

Run on a schedule (launchd/cron). Each invocation does a single sweep and
exits — state lives in the SQLite dedupe DB, so restarts are safe.
"""

import datetime as dt
import sys

import config
import db
import dedupe
import filters
import notify
from sources import (appfolio, buildium, craigslist, email_alerts, haoshiyou,
                     propertyware)

# Register detectors here as you add them (FB, ...).
SOURCES = {
    "craigslist": craigslist.fetch,
    "appfolio": appfolio.fetch,
    "buildium": buildium.fetch,
    "propertyware": propertyware.fetch,
    "email": email_alerts.fetch,
    "haoshiyou": haoshiyou.fetch,
}


def log(msg: str) -> None:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} {msg}"
    print(line)
    try:
        config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(config.LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def run_once(seed: bool = False) -> None:
    """One sweep. With seed=True, record current listings as seen WITHOUT
    notifying — used once at setup so we only alert on future listings."""
    conn = db._connect()

    # Safety net for hosted runs (e.g. GitHub Actions) where the state DB lives
    # in a cache that can be evicted: if the DB comes up empty, treat this run
    # as a silent seed so a lost cache can never blast dozens of stale alerts.
    if not seed and conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0:
        log("WARNING: empty state DB — running this sweep as a silent seed")
        seed = True

    total, new, matched, alerted = 0, 0, 0, 0

    for name, fetch in SOURCES.items():
        try:
            listings = fetch(is_seen=lambda uid: not db.is_new(conn, uid))
        except Exception as e:  # noqa: BLE001
            log(f"[{name}] ERROR during fetch: {e}")
            continue
        total += len(listings)
        for listing in listings:
            if not db.is_new(conn, listing.uid):
                continue
            new += 1
            ok, reason = filters.matches(listing)
            if not ok:
                db.record(conn, listing, notified=False)
                continue
            matched += 1
            key = dedupe.key(listing)
            # Same physical home already handled from another source? Record it
            # (so we track the uid) but don't alert again.
            if db.key_seen(conn, key):
                db.record(conn, listing, notified=False, dedup_key=key)
                log(f"[{name}] DUP ({key}): {listing.title} -> {listing.url}")
                continue
            if seed:
                db.record(conn, listing, notified=False, dedup_key=key)
                log(f"[{name}] SEED (silent): {listing.title} -> {listing.url}")
                continue
            delivered = notify.send(listing)
            if not delivered:
                # Transient send failure: do NOT record, so it's retried next
                # sweep instead of being silently lost.
                log(f"[{name}] SEND FAILED (will retry): {listing.title}")
                continue
            db.record(conn, listing, notified=True, dedup_key=key)
            alerted += 1
            log(f"[{name}] MATCH: {listing.title} -> {listing.url}")

    verb = "seeded" if seed else "pushed"
    log(f"sweep done: {total} seen, {new} new, {matched} matched, "
        f"{alerted} {verb}")
    conn.close()


if __name__ == "__main__":
    seed = "--seed" in sys.argv
    try:
        run_once(seed=seed)
    except Exception as e:  # noqa: BLE001
        log(f"FATAL: {e}")
        sys.exit(1)
