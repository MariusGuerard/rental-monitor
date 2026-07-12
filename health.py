"""Heartbeat: per-sweep health stats, immediate failure alerts (with a 24h
per-source cooldown), and a once-a-day digest to the Telegram group.

Why: every real failure so far (email account suspended, Zillow forwarding
silently broken, scheduler not firing, IMAP hang) was SILENT — indistinguishable
from "no new listings". The digest turns silence into signal; failure alerts
surface breakage in minutes instead of days.

Each sweep is its own process (the cloud loop re-runs main.py), so counters
accumulate in the DB kv store and reset when the daily digest is sent.
"""

import datetime
import json
import sys
import time
from zoneinfo import ZoneInfo

import config
import db
import notify

# In-process collectors for the current sweep (merged into the DB by flush()).
_sweep = {"by_source": {}, "errors": {}, "emails_fetched": 0}


def record_source(name: str, count: int) -> None:
    _sweep["by_source"][name] = _sweep["by_source"].get(name, 0) + count


def error(name: str, msg) -> None:
    _sweep["errors"][name] = str(msg)[:200]


def emails_fetched(n: int) -> None:
    _sweep["emails_fetched"] += n


def _now_local() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo(config.HEALTH_TZ))


def _digest_text(stats: dict) -> str:
    lines = ["🩺 rental-monitor daily check"]
    lines.append(
        f"sweeps: {stats.get('sweeps', 0)} · new listings: "
        f"{stats.get('new', 0)} · matched: {stats.get('matched', 0)} · "
        f"pushed: {stats.get('pushed', 0)} · emails fetched: "
        f"{stats.get('emails', 0)}")
    bs = stats.get("by_source", {})
    if bs:
        lines.append("seen by source: " + " · ".join(
            f"{k} {v}" for k, v in sorted(bs.items()) if v))
    errs = stats.get("errors", {})
    if errs:
        lines.append("⚠️ errors: " + " · ".join(
            f"{k} ×{v['count']} ({v.get('last', '')[:60]})"
            for k, v in sorted(errs.items())))
    else:
        lines.append("✅ no source errors")
    return "\n".join(lines)


def flush(new: int, matched: int, pushed: int) -> None:
    """Called once at the end of every sweep."""
    try:
        stats = json.loads(db.get_kv("health_stats") or "{}")
        stats["sweeps"] = stats.get("sweeps", 0) + 1
        stats["new"] = stats.get("new", 0) + new
        stats["matched"] = stats.get("matched", 0) + matched
        stats["pushed"] = stats.get("pushed", 0) + pushed
        stats["emails"] = stats.get("emails", 0) + _sweep["emails_fetched"]
        bs = stats.setdefault("by_source", {})
        for k, v in _sweep["by_source"].items():
            bs[k] = bs.get(k, 0) + v
        errs = stats.setdefault("errors", {})
        for k, msg in _sweep["errors"].items():
            e = errs.setdefault(k, {"count": 0})
            e["count"] += 1
            e["last"] = msg
        db.set_kv("health_stats", json.dumps(stats))

        # Immediate failure alert — only for PERSISTENT failures (>= STREAK
        # errors in quick succession), not one-off network blips: a single
        # transient timeout in hundreds of sweeps self-heals and shouldn't
        # page anyone (it still shows in the daily digest). Throttled to one
        # alert per source per 24h.
        STREAK, WINDOW = 3, 20 * 60
        now = time.time()
        for k, msg in _sweep["errors"].items():
            raw = db.get_kv(f"health_errstreak:{k}")
            try:
                streak = json.loads(raw) if raw else {"n": 0, "ts": 0}
            except ValueError:
                streak = {"n": 0, "ts": 0}
            # Consecutive-ish: within WINDOW of the previous error.
            streak["n"] = streak["n"] + 1 if now - streak["ts"] <= WINDOW else 1
            streak["ts"] = now
            db.set_kv(f"health_errstreak:{k}", json.dumps(streak))
            if streak["n"] < STREAK:
                continue
            last = float(db.get_kv(f"health_alerted:{k}") or 0)
            if now - last > 24 * 3600:
                notify.send_text(
                    f"⚠️ rental-monitor: source '{k}' failing "
                    f"{streak['n']} sweeps in a row: {msg}")
                db.set_kv(f"health_alerted:{k}", str(now))

        # Daily digest once we're past the digest hour (local time).
        local = _now_local()
        today = local.strftime("%Y-%m-%d")
        if (local.hour >= config.HEALTH_DIGEST_HOUR
                and db.get_kv("health_digest_date") != today):
            notify.send_text(_digest_text(stats))
            db.set_kv("health_digest_date", today)
            db.set_kv("health_stats", "{}")
    except Exception as e:  # noqa: BLE001 — health must never break a sweep
        print(f"[health] flush failed: {e}", file=sys.stderr)
