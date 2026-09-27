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
    return f"[Momentum bot · {env.upper()}] {title}\n"


def fmt_gbp(x: float) -> str:
    return f"£{x:,.2f}"


def _name(key: str) -> str:
    from src.config import BY_KEY
    i = BY_KEY.get(key)
    return f"{i.name} ({key})" if i else key


_REASONS = {
    "entry": "new holding — it's now one of the strongest and rising",
    "rebalance": "top-up/trim back to its target share",
    "exit": "no longer qualifies (weaker or falling), so it's sold",
}


def positions_block(positions: dict[str, dict], total: float) -> str:
    if not positions:
        return "  (nothing held — all cash)"
    lines = []
    for k, v in sorted(positions.items(), key=lambda kv: -kv[1]["value"]):
        share = f" — {v['value'] / total:.0%} of the account" if total else ""
        lines.append(f"  • {_name(k)}: {fmt_gbp(v['value'])}{share}")
    return "\n".join(lines)


def trades_block(trades: list[dict]) -> str:
    if not trades:
        return "  Nothing — everything is close enough to its target, so no trades (saves fees)."
    return "\n".join(f"  • {'Buy' if t['action'] == 'BUY' else 'Sell'} {fmt_gbp(t['amount_gbp'])} of {_name(t['key'])}\n"
                     f"    why: {_REASONS.get(t['reason'], t['reason'])}" for t in trades)


def drawdown_line(dd: float, alert: float) -> str:
    """Drawdown explained: how far below the account's best-ever value it is now."""
    if dd < 0.005:
        mood = "🟢 at or near its best-ever value"
    elif dd < alert:
        mood = "🟢 a normal dip — this strategy expects dips of 10-20%"
    else:
        mood = "🟠 a big dip — expected occasionally, but worth watching (no action is taken automatically)"
    return f"Down from its peak: {dd:.1%} — {mood}"


def slippage_line(avg_bps: float, n: int) -> str:
    """bps = hundredths of a percent.  Positive = paid a bit more than the reference price."""
    pct = avg_bps / 100
    verdict = "fine" if abs(avg_bps) < 30 else "higher than usual"
    return (f"Price paid vs expected: {pct:+.2f}% on average over {n} trade(s) ({verdict}; "
            "small differences are normal market friction)")
