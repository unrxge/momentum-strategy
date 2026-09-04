"""
T212 Ticker Mapping — ISA-Eligible, GBP/GBX-Denominated Listings

Each ticker has been verified against T212's /equity/metadata/exchanges endpoint to confirm:
1. Exchange ID 42 (London Stock Exchange, ISA-eligible)
2. NOT exchange ID 68 (London Stock Exchange NON-ISA) or 64 (AIM)
3. GBP/GBX currency (matching Phase 1 currency-handling logic)
4. workingScheduleId maps to LSE trading hours (07:00:31 - 15:30:00 UTC)

Mapping process:
- ISINs matched to T212 instruments via get_instruments()
- Candidates resolved to exchanges via workingScheduleId → exchange lookup
- Multiple candidates filtered by:
  1. Currency preference (GBP/GBX > others)
  2. Ticker name matching (e.g., CSPX → CSP1_EQ preferred over CSPX_EQ)
  3. "l" suffix indicator (London Stock Exchange convention)

All 6 ETFs confirmed to single, unambiguous LSE (ID 42) listings.
"""

# Mapping from yfinance tickers (.L = London) to T212 order API tickers (_EQ format)
T212_TICKER_MAP = {
    "CSPX.L": "CSP1_EQ",   # iShares Core S&P 500 UCITS ETF (GBX)
    "EQQQ.L": "EQQQl_EQ",  # Invesco NASDAQ-100 UCITS ETF (GBP)
    "VWRL.L": "VWRLl_EQ",  # Vanguard FTSE All-World UCITS ETF (GBP)
    "VEUR.L": "VEURl_EQ",  # Vanguard FTSE Dev Europe UCITS ETF (GBP)
    "SGLN.L": "SGLNl_EQ",  # iShares Physical Gold ETC (GBX)
    "IGLS.L": "IGLSl_EQ",  # iShares UK Gilts 0-5yr UCITS ETF (GBP)
}


def get_t212_ticker(yfinance_ticker: str) -> str:
    """
    Convert yfinance ticker to T212 order API ticker.

    Args:
        yfinance_ticker: Ticker in yfinance format (e.g., "CSPX.L")

    Returns:
        T212 order API ticker (e.g., "CSP1_EQ")

    Raises:
        ValueError: If ticker not found in mapping
    """
    if yfinance_ticker not in T212_TICKER_MAP:
        raise ValueError(
            f"Ticker {yfinance_ticker} not in T212 mapping. "
            f"Valid tickers: {list(T212_TICKER_MAP.keys())}"
        )

    return T212_TICKER_MAP[yfinance_ticker]
