"""Address -> (lat, lon) via OpenStreetMap Nominatim, cached in SQLite so each
address is geocoded at most once. Used by sources that give addresses but no
coordinates (e.g. AppFolio). Respects Nominatim's ~1 req/sec policy."""

import sqlite3
import sys
import time

import httpx

import config

_last_call = [0.0]   # mutable holder for simple inter-call throttling


def _cache_conn() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS geocache (
               address TEXT PRIMARY KEY,
               lat     REAL,
               lon     REAL,
               ts      REAL
           )"""
    )
    conn.commit()
    return conn


def geocode(address: str) -> tuple[float | None, float | None]:
    """Return (lat, lon) for an address, or (None, None) if not found.
    Cached results (including misses) are reused forever."""
    address = (address or "").strip()
    if not address:
        return None, None

    conn = _cache_conn()
    try:
        row = conn.execute(
            "SELECT lat, lon FROM geocache WHERE address = ?", (address,)
        ).fetchone()
        if row is not None:
            return row[0], row[1]

        # Throttle live calls.
        wait = config.GEOCODE_DELAY - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        lat = lon = None
        try:
            r = httpx.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": address, "format": "json", "limit": 1,
                        "countrycodes": "us"},
                headers={"User-Agent": config.NOMINATIM_UA},
                timeout=20,
            )
            r.raise_for_status()
            j = r.json()
            if j:
                lat, lon = float(j[0]["lat"]), float(j[0]["lon"])
        except Exception as e:  # noqa: BLE001
            print(f"[geo] geocode failed for {address!r}: {e}", file=sys.stderr)
            return None, None
        finally:
            _last_call[0] = time.time()

        conn.execute(
            "INSERT OR REPLACE INTO geocache VALUES (?,?,?,?)",
            (address, lat, lon, time.time()),
        )
        conn.commit()
        return lat, lon
    finally:
        conn.close()
