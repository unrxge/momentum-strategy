#!/usr/bin/env python3
"""
Verify every instrument in src/config.py against Trading 212 metadata (read-only):
ISIN match, exchange id 42 (London Stock Exchange, ISA-eligible), GBP/GBX currency.

    python tools/verify_t212_instruments.py

The metadata endpoints are heavily rate-limited (one call per ~50 s) and can be slow.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from src.broker import T212  # noqa: E402
from src.config import INSTRUMENTS  # noqa: E402

LSE_EXCHANGE_ID = 42


def main() -> int:
    b = T212()
    exchanges = b._request("GET", "equity/metadata/exchanges")
    sched = {s["id"]: (e["id"], e["name"]) for e in exchanges for s in e.get("workingSchedules", [])}
    time.sleep(5)
    instruments = b._request("GET", "equity/metadata/instruments")
    by_ticker = {i.get("ticker"): i for i in instruments}
    ok = True
    for inst in INSTRUMENTS:
        i = by_ticker.get(inst.t212)
        if i is None:
            print(f"✗ {inst.key}: T212 ticker {inst.t212} not found"); ok = False; continue
        ex = sched.get(i.get("workingScheduleId"), (None, "?"))
        problems = []
        if i.get("isin") != inst.isin:
            problems.append(f"ISIN {i.get('isin')} != {inst.isin}")
        if ex[0] != LSE_EXCHANGE_ID:
            problems.append(f"exchange {ex} != LSE(42)")
        if i.get("currencyCode") != inst.quote:
            problems.append(f"currency {i.get('currencyCode')} != {inst.quote}")
        status = "✓" if not problems else "✗"
        ok &= not problems
        print(f"{status} {inst.key:7s} {inst.t212:10s} {i.get('currencyCode'):3s} {ex[1]:24s} {i.get('name')}  {' | '.join(problems)}")
    print("ALL OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
