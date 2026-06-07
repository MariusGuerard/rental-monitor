"""Send alerts. Primary: Telegram (reaches your phone). Fallback: native macOS
notification + console/log line so nothing is silent during local testing."""

import subprocess
import sys

import httpx

import config
import scoring
from db import Listing


def _format(listing: Listing) -> str:
    bits = [f"🏠 {listing.title}"]
    line2 = []
    if listing.price is not None:
        line2.append(f"${listing.price:,}/mo")
    if listing.bedrooms is not None:
        line2.append(f"{listing.bedrooms:g}br")
    if listing.address:
        line2.append(listing.address)
    if line2:
        bits.append(" · ".join(line2))

    # Scores
    s = scoring.score(listing)
    value = f"{s.value}/100" if s.value is not None else "n/a"
    bits.append(f"⭐ Value {value} · Fit {s.fit}/100")
    bits.append(f"💵 {s.value_reason}"
                + (f" · {', '.join(s.features)}" if s.features else ""))
    fit_line = "✅ fit: " + (", ".join(s.fit_hits) if s.fit_hits else "—")
    if s.fit_misses:
        fit_line += f"   ❌ no: {', '.join(s.fit_misses)}"
    bits.append(fit_line)

    bits.append(f"📍 source: {listing.source}")
    bits.append(listing.url)
    if config.APPLICATION_PACKET_URL:
        bits.append(f"📄 Apply now → {config.APPLICATION_PACKET_URL}")
    return "\n".join(bits)


def _telegram(text: str) -> bool:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_IDS):
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    ok = True
    for chat_id in config.TELEGRAM_CHAT_IDS:
        try:
            r = httpx.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": False,
            }, timeout=10)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            print(f"[notify] telegram send failed for {chat_id}: {e}",
                  file=sys.stderr)
            ok = False
    return ok


def _macos(title: str, text: str) -> None:
    # Best-effort desktop notification while testing on the Mac.
    try:
        snippet = text.replace('"', "'").split("\n")[0]
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{snippet}" with title "{title}"'],
            check=False, capture_output=True,
        )
    except Exception:  # noqa: BLE001
        pass


def send(listing: Listing) -> bool:
    """Notify all channels. Returns True if at least one channel delivered."""
    text = _format(listing)
    delivered = _telegram(text)
    _macos("New rental match", text)
    print("\n" + "=" * 60 + f"\n{text}\n" + "=" * 60)
    return delivered
