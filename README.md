# Momentum Strategy System

An automated ETF investment system for a UK S&S ISA that combines regime detection, momentum ranking, and rule-based portfolio rebalancing.

## Features

- **Regime Detection**: Identifies market conditions to adjust trading aggressiveness
- **Momentum Ranking**: Scores assets based on recent performance
- **Portfolio Rebalancing**: Automated rule-based execution via Trading 212 API
- **Event Logging**: All trades and decisions logged to Supabase for analysis

## Tech Stack

- **Language**: Python 3.11+
- **Data**: yfinance, pandas, numpy
- **Scheduling**: APScheduler
- **Broker**: Trading 212 API
- **Logging**: Supabase
- **Deployment**: Railway

## Setup

1. Clone and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. Configure environment:
   ```bash
   cp .env.example .env
   # Edit .env with your Trading 212 and Supabase credentials
   ```

3. Run locally:
   ```bash
   python main.py
   ```

## Architecture

- `src/data/` — Market data retrieval from yfinance
- `src/signals/` — Regime detection and momentum calculation
- `src/portfolio/` — Rebalancing logic and position sizing
- `src/execution/` — Trading 212 API integration
- `src/logging/` — Supabase event tracking

## Deployment

Deployed on Railway. Push to trigger auto-deploy via `railway.json`.

## License

Personal use only.
