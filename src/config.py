"""
Static configuration: the instrument universe, strategy parameters, schedule.

Every instrument is a GBP/GBX line on the London Stock Exchange (Trading 212 exchange id 42,
ISA-eligible, no FX conversion fee).  Verified against /equity/metadata/instruments on
2026-09-04 (see tools/verify_t212_instruments.py).

Two Yahoo tickers per instrument:
  trade_ticker  — the LSE line actually bought/sold; used for valuation and order sizing.
  signal_ticker — the series the trend/momentum rules are computed on.  For the equity ETFs
                  this is the US-listed total-return twin of the same index (SPY, QQQ, VT, VGK)
                  in its own currency; for gold and gilts it is the LSE line itself (GBP).
                  No currency conversion happens anywhere: the US twin is watched, the London
                  tracker is traded.  (Backtested against the raw indices ^GSPC/^NDX: the
                  dividend-adjusted twins are +0.03 Sharpe on both data sets.)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    key: str
    name: str
    role: str            # equity | gold | bonds
    trade_ticker: str    # Yahoo, LSE line
    quote: str           # GBP | GBX (pence)
    signal_ticker: str   # Yahoo, series used for signals
    t212: str            # Trading 212 order ticker
    isin: str


INSTRUMENTS: tuple[Instrument, ...] = (
    Instrument("SP500",  "iShares Core S&P 500 (Acc)",          "equity", "CSP1.L", "GBX", "SPY",    "CSP1_EQ",  "IE00B5BMR087"),
    Instrument("NDX",    "Invesco EQQQ Nasdaq-100",             "equity", "EQQQ.L", "GBX", "QQQ",    "EQQQl_EQ", "IE0032077012"),
    Instrument("WORLD",  "Vanguard FTSE All-World",             "equity", "VWRL.L", "GBP", "VT",     "VWRLl_EQ", "IE00B3RBWM25"),
    Instrument("EUROPE", "Vanguard FTSE Developed Europe",      "equity", "VEUR.L", "GBP", "VGK",    "VEURl_EQ", "IE00B945VV12"),
    Instrument("GOLD",   "iShares Physical Gold",               "gold",   "SGLN.L", "GBX", "SGLN.L", "SGLNl_EQ", "IE00B4ND3602"),
    Instrument("GILTS",  "iShares Core UK Gilts",               "bonds",  "IGLT.L", "GBP", "IGLT.L", "IGLTl_EQ", "IE00B1FZSB30"),
)
BY_KEY = {i.key: i for i in INSTRUMENTS}
BY_T212 = {i.t212: i for i in INSTRUMENTS}
EQUITY_KEYS = tuple(i.key for i in INSTRUMENTS if i.role == "equity")
GOLD_KEY = next(i.key for i in INSTRUMENTS if i.role == "gold")
BONDS_KEY = next(i.key for i in INSTRUMENTS if i.role == "bonds")

# Order quantity precision (decimal places).  T212 rejected 4 dp on SGLN in testing and the
# metadata endpoint reports no minTradeQuantity, so every order is rounded to 2 dp
# (worst-case rounding £6 on a £618 share).
QUANTITY_DP = 2

# Schedule (UTC).  LSE opens 07:00 UTC (BST) / 08:00 UTC (GMT); the US index signal is
# yesterday's close, available either way.  09:30 is after the opening auction on both.
REBALANCE_TRADING_DAY_OF_MONTH = 1     # first trading day where LSE and NYSE are both open
SETTLEMENT_WAIT_SECONDS = 900          # poll pending orders up to 15 min between sells and buys
DRAWDOWN_ALERT = 0.15                  # informational alert only; nothing trades on it
PRICE_HISTORY_DAYS = 400               # yfinance request; ~395 trading rows

ENV_KEYS = ("ENVIRONMENT", "T212_DEMO_API_KEY", "T212_DEMO_API_SECRET", "T212_DEMO_BASE_URL",
            "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
