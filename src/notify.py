"""Telegram notifications (one-way; nothing waits for a reply)."""
from __future__ import annotations

import os
import time

import requests


def send(text: str, retries: int = 3) -> bool:
    """Send a message; returns True on success.  Never raises."""
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("⚠️  Telegram not configured; message was:\n" + text)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(retries):
        try:
            r = requests.post(url, json={"chat_id": chat, "text": text[:4000]}, timeout=15)
            if r.status_code == 200:
                return True
            print(f"✗ Telegram {r.status_code}: {r.text[:200]}")
        except requests.RequestException as exc:
            print(f"✗ Telegram error: {exc}")
        time.sleep(2 * (attempt + 1))
    print("✗ TELEGRAM SEND FAILED — message was:\n" + text)
    return False


def header(env: str, title: str) -> str:
    return f"[{env.upper()}] {title}\n"


def fmt_gbp(x: float) -> str:
    return f"£{x:,.2f}"


def positions_block(positions: dict[str, dict], total: float) -> str:
    if not positions:
        return "  (no positions)"
    lines = []
    for k, v in sorted(positions.items(), key=lambda kv: -kv[1]["value"]):
        lines.append(f"  {k:8s} {fmt_gbp(v['value']):>12s}  ({v['value'] / total:5.1%})" if total else f"  {k:8s} {fmt_gbp(v['value'])}")
    return "\n".join(lines)


def trades_block(trades: list[dict]) -> str:
    if not trades:
        return "  none (within tolerance bands)"
    return "\n".join(f"  {t['action']:4s} {t['key']:8s} {fmt_gbp(t['amount_gbp']):>12s}  [{t['reason']}]" for t in trades)
