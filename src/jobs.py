"""
Scheduled jobs.  Run with:

    python -m src.jobs rebalance [--dry-run] [--force]
    python -m src.jobs weekly
    python -m src.jobs heartbeat

rebalance : first trading day of the month (self-gated unless --force).  Computes signals,
            trades to target, reports to Telegram.  --dry-run does everything except place orders.
weekly    : Monday status — what the rules see, what next month's rebalance would do, drawdown.
heartbeat : daily liveness row in Supabase.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import date, datetime, timezone

from dotenv import load_dotenv

from src import notify, store
from src.broker import T212, BrokerError
from src.config import INSTRUMENTS, BY_KEY, REBALANCE_TRADING_DAY_OF_MONTH, DRAWDOWN_ALERT, ENV_KEYS
from src.data import load_prices
from src.executor import execute
from src.strategy import StrategyParams, compute_signals, target_weights, generate_trades, describe, CASH
from src.trading_calendar import nth_trading_day, next_rebalance_date

PARAMS = StrategyParams()


def _env() -> str:
    return os.getenv("ENVIRONMENT", "demo").upper()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _check_env() -> list[str]:
    return [k for k in ENV_KEYS if not os.getenv(k)]


def _alert(title: str, body: str) -> None:
    notify.send(notify.header(_env(), title) + f"{_now()}\n\n{body}")


def _signals_and_targets():
    """Fetch data, compute signals and targets.  Raises RuntimeError with a readable message."""
    trade_px, signal_px, problems = load_prices(INSTRUMENTS)
    if problems:
        raise RuntimeError("Price data failed the quality gate:\n" + "\n".join(f"• {p}" for p in problems))
    sig = compute_signals(signal_px, PARAMS, date=str(max(s.index[-1].date() for s in signal_px.values())))
    target = target_weights(sig, PARAMS)
    last_px = {k: float(s.iloc[-1]) for k, s in trade_px.items()}
    return sig, target, last_px


def _snapshot(broker: T212) -> dict:
    snap = broker.snapshot()
    store.log_portfolio_value(snap["total"], snap["cash"]["free"], snap["invested"])
    return snap


# ---------------------------------------------------------------- rebalance
def rebalance(dry_run: bool = False, force: bool = False) -> int:
    print(f"=== REBALANCE [{_env()}] {_now()} dry_run={dry_run} force={force}")
    missing = _check_env()
    if missing:
        _alert("❌ REBALANCE ABORTED", "Missing environment variables: " + ", ".join(missing))
        return 2
    today = date.today()
    due = nth_trading_day(today.year, today.month, REBALANCE_TRADING_DAY_OF_MONTH)
    if today != due and not force:
        print(f"not the rebalance day (due {due}); nothing to do")
        return 0
    try:
        broker = T212()
        snap = _snapshot(broker)
        pending = broker.pending_orders()
        if pending:
            _alert("❌ REBALANCE ABORTED", f"{len(pending)} order(s) already pending on the account. "
                   "Not trading on top of an unknown state — check the T212 app.")
            return 3
        other = {k: v for k, v in snap["positions"].items() if k.startswith("OTHER:")}
        positions = {k: v for k, v in snap["positions"].items() if not k.startswith("OTHER:")}
        universe_value = snap["cash"]["free"] + sum(v["value"] for v in positions.values())
        if other:
            print(f"ignoring non-universe holdings: {list(other)}")

        sig, target, last_px = _signals_and_targets()
        trades = generate_trades(target, positions, universe_value, PARAMS)
        history = store.portfolio_history()
        dd = store.drawdown(history)
        snapshot_id = store.log_snapshot("monthly", sig, target, dd, {"dry_run": dry_run, "forced": force})

        plan = (f"Account: {notify.fmt_gbp(snap['total'])} (cash {notify.fmt_gbp(snap['cash']['free'])})\n"
                f"Drawdown from peak: {dd:.1%}" + ("  ⚠️ above alert level" if dd > DRAWDOWN_ALERT else "") + "\n\n"
                + describe(sig, target, PARAMS) + "\n\nTrades:\n" + notify.trades_block(trades))
        if dry_run:
            msg = notify.header(_env(), "🧪 DRY RUN — no orders placed") + f"{_now()}\n\n" + plan
            notify.send(msg)
            store.log_decision(snapshot_id, trades, msg, "DRY_RUN")
            print(plan)
            return 0
        if not trades:
            msg = notify.header(_env(), "✅ MONTHLY REBALANCE — nothing to trade") + f"{_now()}\n\n" + plan
            notify.send(msg)
            store.log_decision(snapshot_id, [], msg, "NO_TRADES")
            return 0

        decision_id = store.log_decision(snapshot_id, trades, plan, "AUTO")
        result = execute(broker, trades, last_px, decision_id)
        after = broker.snapshot()
        lines = [f"Placed: {len(result['placed'])}   Failed: {len(result['failed'])}"]
        if result["buys_skipped"]:
            lines.append("⚠️ SELLS STILL PENDING AFTER 15 MIN — BUYS NOT PLACED. Check the T212 app; "
                         "the next weekly status will show the gap and next month's run will complete it.")
        for f in result["failed"]:
            lines.append(f"  ✗ {f['action']} {f['key']}: {f.get('error')}")
        lines.append("\nPositions after:\n" + notify.positions_block(
            {k: v for k, v in after["positions"].items() if not k.startswith("OTHER:")}, after["total"]))
        lines.append(f"Cash: {notify.fmt_gbp(after['cash']['free'])}   Total: {notify.fmt_gbp(after['total'])}")
        title = "✅ MONTHLY REBALANCE EXECUTED" if not result["failed"] and not result["buys_skipped"] else "⚠️ MONTHLY REBALANCE — ATTENTION"
        notify.send(notify.header(_env(), title) + f"{_now()}\n\n" + plan + "\n\n" + "\n".join(lines))
        store.log_portfolio_value(after["total"], after["cash"]["free"], after["invested"])
        return 0 if not result["failed"] else 1
    except (BrokerError, RuntimeError, ValueError) as exc:
        _alert("❌ REBALANCE ABORTED", str(exc))
        return 4
    except Exception as exc:                       # anything else: never silent
        _alert("❌ REBALANCE CRASHED", f"{exc.__class__.__name__}: {exc}\n\n{traceback.format_exc()[-1200:]}")
        return 5


# ---------------------------------------------------------------- weekly status
def weekly() -> int:
    print(f"=== WEEKLY STATUS [{_env()}] {_now()}")
    try:
        broker = T212()
        snap = _snapshot(broker)
        positions = {k: v for k, v in snap["positions"].items() if not k.startswith("OTHER:")}
        history = store.portfolio_history()
        dd = store.drawdown(history)
        sig, target, last_px = _signals_and_targets()
        trades = generate_trades(target, positions, snap["cash"]["free"] + sum(v["value"] for v in positions.values()), PARAMS)
        store.log_snapshot("weekly", sig, target, dd)
        nxt = next_rebalance_date(date.today(), REBALANCE_TRADING_DAY_OF_MONTH)
        body = (f"Account: {notify.fmt_gbp(snap['total'])} (cash {notify.fmt_gbp(snap['cash']['free'])})\n"
                f"Drawdown from peak: {dd:.1%}" + ("  ⚠️ above alert level" if dd > DRAWDOWN_ALERT else "") + "\n"
                f"Positions:\n{notify.positions_block(positions, snap['total'])}\n\n"
                + describe(sig, target, PARAMS)
                + f"\n\nIf the rebalance ran today it would:\n{notify.trades_block(trades)}\n"
                f"Next rebalance: {nxt}")
        notify.send(notify.header(_env(), "📊 WEEKLY STATUS") + f"{_now()}\n\n" + body)
        return 0
    except Exception as exc:
        _alert("❌ WEEKLY STATUS FAILED", f"{exc}")
        return 1


def heartbeat() -> int:
    rid = store.heartbeat()
    print("heartbeat", rid)
    return 0 if rid else 1


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="momentum-strategy jobs")
    ap.add_argument("job", choices=["rebalance", "weekly", "heartbeat"])
    ap.add_argument("--dry-run", action="store_true", help="compute and report, place no orders")
    ap.add_argument("--force", action="store_true", help="ignore the first-trading-day gate")
    a = ap.parse_args(argv)
    if a.job == "rebalance":
        return rebalance(dry_run=a.dry_run, force=a.force)
    if a.job == "weekly":
        return weekly()
    return heartbeat()


if __name__ == "__main__":
    sys.exit(main())
