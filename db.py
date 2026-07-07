"""SQLite-backed dedupe store. Tracks listings we've already alerted on so we
never notify twice, across restarts."""

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional

import config


@dataclass
class Listing:
    """A normalized rental listing from any source."""
    uid: str                      # stable unique id (source + native id/url)
    source: str                   # "craigslist", "pm:lingsch", "email:zillow"...
    title: str
    url: str
    price: Optional[int] = None
    bedrooms: Optional[float] = None
    sqft: Optional[float] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    address: str = ""
    body: str = ""
    posted_at: str = ""
    # Set when a source already constrained the area (e.g. a saved-search email
    # alert) and we couldn't extract an address to geo-verify — trust it.
    prefiltered: bool = False
    extra: dict = field(default_factory=dict)

    def haystack(self) -> str:
        """All text we can pattern-match against."""
        return " ".join([self.title, self.address, self.body])


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS seen (
               uid        TEXT PRIMARY KEY,
               source     TEXT,
               title      TEXT,
               url        TEXT,
               price      INTEGER,
               bedrooms   REAL,
               first_seen REAL,
               notified   INTEGER DEFAULT 0,
               dedup_key  TEXT
           )"""
    )
    # Migration for DBs created before dedup_key existed.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(seen)")}
    if "dedup_key" not in cols:
        conn.execute("ALTER TABLE seen ADD COLUMN dedup_key TEXT")
    # Alerts whose Telegram delivery failed, awaiting retry. Email listings
    # can't be re-parsed (IMAP UID watermark), so they must persist here.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pending (
               uid       TEXT PRIMARY KEY,
               payload   TEXT,
               dedup_key TEXT,
               attempts  INTEGER DEFAULT 0
           )"""
    )
    conn.commit()
    return conn


def pending_add(conn: sqlite3.Connection, listing: "Listing",
                dedup_key: str | None) -> None:
    import dataclasses
    import json
    conn.execute(
        "INSERT OR IGNORE INTO pending VALUES (?,?,?,0)",
        (listing.uid, json.dumps(dataclasses.asdict(listing)), dedup_key))
    conn.commit()


def pending_list(conn: sqlite3.Connection) -> list:
    return conn.execute(
        "SELECT uid, payload, dedup_key, attempts FROM pending").fetchall()


def pending_has(conn: sqlite3.Connection, uid: str) -> bool:
    return conn.execute("SELECT 1 FROM pending WHERE uid = ?",
                        (uid,)).fetchone() is not None


def pending_remove(conn: sqlite3.Connection, uid: str) -> None:
    conn.execute("DELETE FROM pending WHERE uid = ?", (uid,))
    conn.commit()


def pending_bump(conn: sqlite3.Connection, uid: str) -> None:
    conn.execute("UPDATE pending SET attempts = attempts + 1 WHERE uid = ?",
                 (uid,))
    conn.commit()


def get_kv(key: str) -> str | None:
    """Small key/value store (own connection — usable from any source)."""
    conn = _connect()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        row = conn.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_kv(key: str, value: str) -> None:
    conn = _connect()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        conn.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def is_new(conn: sqlite3.Connection, uid: str) -> bool:
    cur = conn.execute("SELECT 1 FROM seen WHERE uid = ?", (uid,))
    return cur.fetchone() is None


def key_seen(conn: sqlite3.Connection, dedup_key: str | None) -> bool:
    """True if a *different* listing with this cross-source key was already
    recorded (i.e. we've handled this physical home from some source)."""
    if not dedup_key:
        return False
    cur = conn.execute(
        "SELECT 1 FROM seen WHERE dedup_key = ? LIMIT 1", (dedup_key,))
    return cur.fetchone() is not None


def record(conn: sqlite3.Connection, listing: Listing, notified: bool,
           dedup_key: str | None = None) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO seen
           (uid, source, title, url, price, bedrooms, first_seen, notified,
            dedup_key)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (listing.uid, listing.source, listing.title, listing.url,
         listing.price, listing.bedrooms, time.time(), int(notified),
         dedup_key),
    )
    conn.commit()
