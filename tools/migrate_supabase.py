"""Copy all rows from one Supabase project to another (same schema, original ids kept).

All credentials come from .env: the old project is SUPABASE_URL / SUPABASE_SERVICE_KEY, the
new project is NEW_SUPABASE_URL / NEW_SUPABASE_SERVICE_KEY.

Usage:
    venv/bin/python tools/migrate_supabase.py [--dry-run]

Run sql/schema.sql then sql/migration_001_dashboard.sql in the NEW project first.
Idempotent: rows are upserted by id, so it is safe to re-run.
"""
import os
import sys

from dotenv import load_dotenv
from supabase import create_client

# Parents before children (foreign keys).
TABLES = [
    "signal_snapshots",
    "trade_decisions",
    "executed_orders",
    "portfolio_value_history",
    "heartbeat",
    "benchmark_history",
    "cashflows",
]
PAGE = 1000
BATCH = 200


def _env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        sys.exit(f"{name} not set")
    return v


def _fetch_all(client, table: str) -> list[dict]:
    rows, start = [], 0
    while True:
        r = client.table(table).select("*").order("id").range(start, start + PAGE - 1).execute()
        rows.extend(r.data)
        if len(r.data) < PAGE:
            return rows
        start += PAGE


def main() -> None:
    dry = "--dry-run" in sys.argv
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(root, ".env"))
    old = create_client(_env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY"))
    new = create_client(_env("NEW_SUPABASE_URL"), _env("NEW_SUPABASE_SERVICE_KEY"))

    for table in TABLES:
        rows = _fetch_all(old, table)
        print(f"{table}: {len(rows)} rows", end="")
        if dry or not rows:
            print(" (skipped)" if dry else "")
            continue
        for i in range(0, len(rows), BATCH):
            new.table(table).upsert(rows[i:i + BATCH], on_conflict="id").execute()
        copied = len(_fetch_all(new, table))
        print(f" -> new has {copied}")
        if copied < len(rows):
            sys.exit(f"✗ {table}: row count mismatch")
    print("Done.")


if __name__ == "__main__":
    main()
