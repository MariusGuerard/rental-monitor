"""One-shot Telegram setup. After you (and your partner) message the bot and
tap Start, run this: it captures the chat IDs, writes them into .env, and sends
each a confirmation message.

    ./.venv/bin/python setup_telegram.py
"""

import pathlib
import re
import sys

import httpx

import config

ENV = pathlib.Path(__file__).resolve().parent / ".env"


def main() -> int:
    token = config.TELEGRAM_BOT_TOKEN
    if not token and ENV.exists():
        m = re.search(r'RENTAL_TELEGRAM_TOKEN="([^"]+)"', ENV.read_text())
        token = m.group(1) if m else ""
    if not token:
        print("No bot token found (env RENTAL_TELEGRAM_TOKEN or .env).")
        return 1

    up = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates",
                   timeout=15).json()
    chats = {}
    for u in up.get("result", []):
        ch = (u.get("message") or u.get("channel_post") or {}).get("chat", {})
        if ch.get("id"):
            chats[ch["id"]] = ch.get("first_name") or ch.get("username") or "?"
    if not chats:
        print("No chats yet. Open Telegram, message the bot, tap Start, retry.")
        return 1

    ids = ",".join(str(c) for c in chats)
    print("Found chats:", chats)

    # Write the chat IDs into .env (replace or append the line).
    text = ENV.read_text() if ENV.exists() else ""
    line = f'export RENTAL_TELEGRAM_CHATS="{ids}"'
    if "RENTAL_TELEGRAM_CHATS" in text:
        text = re.sub(r'export RENTAL_TELEGRAM_CHATS=.*', line, text)
    else:
        text += ("\n" if text and not text.endswith("\n") else "") + line + "\n"
    ENV.write_text(text)
    print(f"Wrote chat IDs to {ENV}")

    for cid, name in chats.items():
        httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   json={"chat_id": cid,
                         "text": "✅ Rental monitor connected. You'll get "
                                 "Outer Sunset matches here the moment they "
                                 "post."}, timeout=15)
        print(f"Sent confirmation to {name} ({cid})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
