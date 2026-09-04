"""Scheduler jobs for momentum strategy: weekly checks, monthly rebalances, heartbeat."""

import os
from datetime import datetime, date, timedelta
import pandas_market_calendars as mcal
from src.execution.t212_client import T212Client
from src.execution.trade_generator import generate_trade_list
from src.execution.telegram_notifier import (
    format_rebalance_message,
    format_no_action_needed_message,
    send_message,
    wait_for_reply,
    get_high_water_mark,
)
from src.data.price_fetcher import fetch_price_history
from src.signals.regime import check_regime, check_fast_crash
from src.signals.momentum import rank_growth_assets, rank_defensive_assets, select_top_growth
from src.signals.risk import check_drawdown
from src.portfolio.allocator import build_target_allocation
from src.logging.supabase_client import (
    log_signal_snapshot,
    log_trade_decision,
    get_portfolio_value_history,
    send_heartbeat,
)
from src.execution.rebalance_executor import execute_rebalance

GROWTH_TICKERS = ["CSPX.L", "EQQQ.L", "VWRL.L", "VEUR.L"]
DEFENSIVE_TICKERS = ["SGLN.L", "IGLS.L"]

# Market calendars (cached on first use)
_lse_calendar = None
_nyse_calendar = None


def _get_calendars():
    """Lazy-load market calendars."""
    global _lse_calendar, _nyse_calendar
    if _lse_calendar is None:
        _lse_calendar = mcal.get_calendar('LSE')
    if _nyse_calendar is None:
        _nyse_calendar = mcal.get_calendar('NYSE')
    return _lse_calendar, _nyse_calendar


def is_trading_day(check_date: date) -> bool:
    """
    Check if the given date is a trading day.

    A date is a trading day ONLY if BOTH LSE (UK) and NYSE (US) are open.
    This ensures we don't trade on US holidays (Thanksgiving, July 4, etc.)
    or UK holidays (Boxing Day, etc.), even though our ETFs are LSE-listed
    but track US indices.

    Works for any year without manual updates.

    Args:
        check_date: Date to check

    Returns:
        True if both exchanges are open, False otherwise
    """
    try:
        lse, nyse = _get_calendars()

        # Get valid trading days for both exchanges (entire year is fine for performance)
        year = check_date.year
        lse_schedule = lse.schedule(start_date=f"{year}-01-01", end_date=f"{year}-12-31")
        nyse_schedule = nyse.schedule(start_date=f"{year}-01-01", end_date=f"{year}-12-31")

        # Convert to set of dates for fast lookup
        lse_valid_days = set(lse_schedule.index.date)
        nyse_valid_days = set(nyse_schedule.index.date)

        # Both must be trading days
        return check_date in lse_valid_days and check_date in nyse_valid_days

    except Exception as e:
        print(f"⚠️  Error checking trading day {check_date}: {e}")
        # Fallback to simple weekday check
        return check_date.weekday() < 5


def get_first_trading_day_of_month(year: int, month: int) -> date:
    """
    Get the first trading day of the given month.

    Advances day by day from the 1st until is_trading_day() returns True.

    Args:
        year: Calendar year
        month: Calendar month (1-12)

    Returns:
        The first trading day of the month
    """
    current_date = date(year, month, 1)

    # Advance until we find a trading day
    while not is_trading_day(current_date):
        current_date += timedelta(days=1)

    return current_date


def weekly_job():
    """
    Run FAST, AUTOMATIC weekly checks with NO Telegram approval required.

    - Fetch CSPX price data
    - Check regime and fast-crash trigger
    - Calculate portfolio drawdown from Supabase history
    - Log via signal_snapshots with check_type='weekly'
    - Send appropriate Telegram notification
    - Does NOT place orders (that's a separate future step)
    """
    print("\n" + "=" * 90)
    print("WEEKLY JOB STARTED")
    print("=" * 90)

    try:
        # Fetch price data
        print("[1/4] Fetch CSPX price data...")
        try:
            cspx_prices = fetch_price_history("CSPX.L", period_days=400, apply_delay=True)["Close"]
        except Exception as e:
            print(f"✗ Failed to fetch CSPX.L: {e}")
            return

        # Check regime and fast-crash
        print("[2/4] Check regime and fast-crash...")
        regime = check_regime(cspx_prices)
        fast_crash_triggered = check_fast_crash(cspx_prices)

        # Get portfolio drawdown from Supabase
        print("[3/4] Calculate portfolio drawdown...")
        portfolio_history = get_portfolio_value_history(
            environment=os.getenv("ENVIRONMENT", "DEMO").upper(),
            limit=100
        )

        drawdown_pct = check_drawdown(portfolio_history) if portfolio_history else 0.0
        circuit_breaker_triggered = bool(drawdown_pct > 15.0)

        # Get current prices
        cspx_price = float(cspx_prices.iloc[-1]) if cspx_prices is not None else None
        cspx_200ma = float(cspx_prices.rolling(200).mean().iloc[-1]) if len(cspx_prices) >= 200 else None

        # Log signal snapshot
        print("[4/4] Log signal snapshot...")
        signal_id = log_signal_snapshot({
            "environment": os.getenv("ENVIRONMENT", "DEMO").upper(),
            "check_type": "weekly",
            "regime": regime,
            "cspx_price": cspx_price,
            "cspx_200ma": cspx_200ma,
            "fast_crash_triggered": bool(fast_crash_triggered),
            "ten_day_return": None,
            "portfolio_drawdown_pct": float(drawdown_pct),
            "circuit_breaker_triggered": bool(circuit_breaker_triggered),
            "growth_rankings": None,  # Weekly checks don't calculate rankings
            "defensive_rankings": None,  # Rankings are monthly_job()'s responsibility
            "selected_growth": None,  # Not applicable for weekly regime checks
        })

        # Send appropriate notification
        if fast_crash_triggered or circuit_breaker_triggered:
            trigger_name = "fast-crash" if fast_crash_triggered else "circuit-breaker"
            message = f"""{os.getenv("ENVIRONMENT", "DEMO").upper()} ACCOUNT

⚠️ WEEKLY CHECK — AUTOMATIC TRIGGER FIRED

Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

TRIGGER: {trigger_name.upper()}
Regime: {regime.upper()}
Drawdown: {drawdown_pct:.2f}%
CSPX Price: £{cspx_price:.2f}
CSPX 200-MA: £{cspx_200ma:.2f if cspx_200ma else 'N/A'}

Action: Exposure is being reduced automatically.
No user approval required.
"""
            send_message(message)
            print(f"✓ Sent {trigger_name} trigger notification to Telegram")
        else:
            message = f"""{os.getenv("ENVIRONMENT", "DEMO").upper()} ACCOUNT

✅ WEEKLY CHECK COMPLETE — NO ACTION NEEDED

Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

Regime: {regime.upper()}
Drawdown: {drawdown_pct:.2f}%
CSPX Price: £{cspx_price:.2f}

All indicators normal. No action required.
Next check: Monday 09:00
"""
            send_message(message)
            print("✓ Sent weekly check complete message to Telegram")

        print("✅ WEEKLY JOB COMPLETE\n")

    except Exception as e:
        print(f"✗ WEEKLY JOB FAILED: {e}")
        import traceback
        traceback.print_exc()


def monthly_job():
    """
    Run FULL monthly rebalance with Telegram approval.

    - Run Phase 2/3 pipeline (regime, rankings, allocation)
    - Generate trade list
    - Log signal snapshot with check_type='monthly'
    - If trades exist: send message, wait for YES/NO/TIMEOUT, log decision
    - If no trades: send no-action message, log decision with null response
    - Does NOT place orders (that's a separate future step)
    """
    print("\n" + "=" * 90)
    print("MONTHLY JOB STARTED")
    print("=" * 90)

    # Check if today is the first trading day of this month
    today = date.today()
    first_trading_day = get_first_trading_day_of_month(today.year, today.month)

    if today != first_trading_day:
        print(f"⏭️  Today ({today}) is not the first trading day ({first_trading_day})")
        print("   Skipping monthly job.\n")
        return

    try:
        # Initialize T212 client
        print("[1/7] Initialize T212 client...")
        try:
            client = T212Client()
            account_cash = client.get_account_cash()
            positions = client.get_current_positions()
            account_value = account_cash["free_cash"]
            print(f"✓ Account value: £{account_value:.2f}\n")
        except Exception as e:
            print(f"✗ Failed to initialize T212: {e}")
            return

        # Fetch price data and run signal pipeline
        print("[2/7] Fetch price data and run signal pipeline...")
        try:
            price_data = {}
            for ticker in GROWTH_TICKERS + DEFENSIVE_TICKERS:
                try:
                    df = fetch_price_history(ticker, period_days=400, apply_delay=True)
                    price_data[ticker] = df["Close"]
                except Exception as e:
                    print(f"  Warning: Failed to fetch {ticker}: {e}")

            if not price_data:
                print("✗ No price data available")
                return

            # Run signal pipeline
            cspx_prices = price_data.get("CSPX.L")
            regime = check_regime(cspx_prices) if cspx_prices is not None else "unknown"

            growth_data = {t: price_data[t] for t in GROWTH_TICKERS if t in price_data}
            ranked_growth = rank_growth_assets(growth_data)
            top_growth = select_top_growth(ranked_growth, n=2)

            defensive_data = {t: price_data[t] for t in DEFENSIVE_TICKERS if t in price_data}
            ranked_defensive = rank_defensive_assets(defensive_data)

            print(f"✓ Regime: {regime.upper()}")
            print(f"✓ Top growth: {', '.join(top_growth)}\n")

        except Exception as e:
            print(f"✗ Failed to run signal pipeline: {e}")
            return

        # Build target allocation
        print("[3/7] Build target allocation...")
        try:
            target_allocation = build_target_allocation(
                regime=regime,
                top_growth=top_growth,
                top_defensive=ranked_defensive,
                price_data=price_data,
                portfolio_value=account_value,
            )
            print(f"✓ Target allocation generated\n")
        except Exception as e:
            print(f"✗ Failed to build allocation: {e}")
            return

        # Generate trade list
        print("[4/7] Generate trade list...")
        try:
            trades = generate_trade_list(target_allocation, positions, account_value)
            if trades:
                print(f"✓ Generated {len(trades)} trade instruction(s)")
            else:
                print("✓ No trades needed (positions match target)")
            print()
        except Exception as e:
            print(f"✗ Failed to generate trades: {e}")
            return

        # Log signal snapshot
        print("[5/7] Log signal snapshot...")
        signal_id = log_signal_snapshot({
            "environment": os.getenv("ENVIRONMENT", "DEMO").upper(),
            "check_type": "monthly",
            "regime": regime,
            "cspx_price": float(cspx_prices.iloc[-1]) if cspx_prices is not None else None,
            "cspx_200ma": float(cspx_prices.rolling(200).mean().iloc[-1]) if len(cspx_prices) >= 200 else None,
            "fast_crash_triggered": False,
            "ten_day_return": None,
            "portfolio_drawdown_pct": 0.0,
            "circuit_breaker_triggered": False,
            "growth_rankings": [{"ticker": t, "momentum": float(m)} for t, m in ranked_growth],
            "defensive_rankings": [{"ticker": t, "momentum": float(m)} for t, m in ranked_defensive],
            "selected_growth": top_growth,
        })

        if not signal_id:
            print("⚠️  Failed to log signal snapshot (continuing anyway)\n")
        else:
            print()

        # Handle no-action vs action-needed flow
        if not trades:
            print("[6/7] No action needed — send notification...")
            message = format_no_action_needed_message(
                environment=os.getenv("ENVIRONMENT", "DEMO").upper(),
                account_value=account_value,
                regime=regime,
                fast_crash_triggered=False,
                circuit_breaker_triggered=False,
                drawdown_pct=0.0,
                target_allocation=target_allocation,
                current_positions=positions,
                reason="Positions are within tolerance of target allocation.",
                check_type="MONTHLY",
                is_simulated=False,
            )
            send_message(message)
            print("✓ Sent no-action message to Telegram")

            # Log trade decision with null response
            print("\n[7/7] Log trade decision...")
            trade_id = log_trade_decision({
                "signal_snapshot_id": signal_id,
                "environment": os.getenv("ENVIRONMENT", "DEMO").upper(),
                "trade_list": [],
                "total_buy_amount": 0,
                "total_sell_amount": 0,
                "telegram_message_sent": message,
                "user_response": None,
                "responded_at": None,
            })
            if trade_id:
                print(f"✓ Logged no-action decision\n")

        else:
            print("[6/7] Action needed — send Telegram message and wait for reply...")

            # Get high water mark before sending
            high_water_mark = get_high_water_mark()

            # Format and send message
            telegram_message = format_rebalance_message(
                environment=os.getenv("ENVIRONMENT", "DEMO").upper(),
                account_value=account_value,
                regime=regime,
                fast_crash_triggered=False,
                growth_rankings=ranked_growth,
                defensive_rankings=ranked_defensive,
                trade_list=trades,
                selected_growth=top_growth,
                is_simulated=False,
                reason="Rebalancing to target allocation based on regime and momentum signals.",
            )

            send_message(telegram_message)
            print("✓ Message sent to Telegram")

            # Wait for reply (24 hours)
            print("⏳ Waiting for approval (24 hours)...\n")
            reply_data = wait_for_reply(timeout_seconds=18000, high_water_mark=high_water_mark)

            # Handle response
            print("[7/7] Log trade decision...")
            if reply_data:
                user_response = reply_data.get("reply")
                responded_at = datetime.fromtimestamp(reply_data.get("timestamp")).isoformat() if reply_data.get("timestamp") else None
                print(f"Response: {user_response}")
            else:
                user_response = "TIMEOUT"
                responded_at = None
                print("Response: TIMEOUT (no reply within 24 hours)")

            total_buy = float(sum(t["amount_gbp"] for t in trades if t["action"] == "BUY"))
            total_sell = float(sum(t["amount_gbp"] for t in trades if t["action"] == "SELL"))

            trade_id = log_trade_decision({
                "signal_snapshot_id": signal_id,
                "environment": os.getenv("ENVIRONMENT", "DEMO").upper(),
                "trade_list": trades,
                "total_buy_amount": total_buy,
                "total_sell_amount": total_sell,
                "telegram_message_sent": telegram_message,
                "user_response": user_response,
                "responded_at": responded_at,
            })

            if trade_id:
                print(f"✓ Logged trade decision with response: {user_response}\n")

            # Execute trades if approved
            if user_response and user_response.upper() == "YES":
                print("[EXECUTION] User approved — executing rebalance...\n")
                try:
                    execution_summary = execute_rebalance(
                        trade_list=trades,
                        trade_decision_id=str(trade_id) if trade_id else None,
                        price_data=price_data,
                    )
                    phase_status = execution_summary.get("phase_status", "unknown")
                    print(f"✓ Execution complete — status: {phase_status}")
                    print(f"  Successful orders: {execution_summary.get('total_successful', 0)}")
                    print(f"  Failed orders: {execution_summary.get('total_failed', 0)}\n")

                    if phase_status == "sells_placed_awaiting_settlement":
                        send_message(
                            f"{os.getenv('ENVIRONMENT', 'DEMO').upper()} ACCOUNT\n\n"
                            f"⏸️ REBALANCE PARTIALLY COMPLETE\n\n"
                            f"SELL orders placed and awaiting settlement.\n"
                            f"BUY orders will execute automatically once settlement clears.\n"
                            f"(This typically takes minutes to hours.)"
                        )
                    else:
                        send_message(
                            f"{os.getenv('ENVIRONMENT', 'DEMO').upper()} ACCOUNT\n\n"
                            f"✅ REBALANCE EXECUTION COMPLETE\n\n"
                            f"Successful orders: {execution_summary.get('total_successful', 0)}\n"
                            f"Failed orders: {execution_summary.get('total_failed', 0)}"
                        )
                except Exception as e:
                    print(f"✗ Execution failed: {e}")
                    send_message(
                        f"{os.getenv('ENVIRONMENT', 'DEMO').upper()} ACCOUNT\n\n"
                        f"❌ REBALANCE EXECUTION FAILED\n\n"
                        f"Error: {e}\n\n"
                        f"No orders were placed. Manual intervention may be needed."
                    )
            elif user_response and user_response.upper() != "TIMEOUT":
                print(f"[EXECUTION] User declined ({user_response}) — no trades placed.\n")

        print("✅ MONTHLY JOB COMPLETE\n")

    except Exception as e:
        print(f"✗ MONTHLY JOB FAILED: {e}")
        import traceback
        traceback.print_exc()


def heartbeat_job():
    """
    Send a heartbeat to indicate the bot is running.

    Calls send_heartbeat() from supabase_client.
    """
    try:
        send_heartbeat()
    except Exception as e:
        print(f"✗ Heartbeat failed: {e}")
