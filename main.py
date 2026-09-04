#!/usr/bin/env python3
"""Entry point for the momentum strategy scheduler.

Persistent process that stays alive and runs jobs on a schedule:
- Weekly checks (Mondays 09:00)
- Monthly rebalance (first trading day of month, 09:00)
- Heartbeat (daily, 08:00)
"""

import os
import sys
import time
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from src.scheduler.jobs import weekly_job, monthly_job, heartbeat_job


def _start_health_server():
    """Run a minimal HTTP server so Railway keeps the container alive."""
    port = int(os.getenv("PORT", 8080))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):
            pass  # silence access logs

    server = HTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Health endpoint running on port {port}")

    # Self-ping every 45s to prevent Railway idle sleep between external pings
    def _self_ping():
        while True:
            time.sleep(45)
            try:
                urllib.request.urlopen(f"http://localhost:{port}", timeout=5)
            except Exception:
                pass

    threading.Thread(target=_self_ping, daemon=True).start()


def main():
    """Start the momentum strategy scheduler."""
    environment = os.getenv("ENVIRONMENT", "DEMO").upper()
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("\n" + "=" * 90)
    print("MOMENTUM STRATEGY SCHEDULER STARTED")
    print("=" * 90)
    print(f"Environment: {environment}")
    print(f"Started at: {start_time}")
    print("\nScheduled jobs:")
    print("  • Weekly check: Every Monday at 09:00")
    print("  • Monthly rebalance: First trading day of month at 09:00")
    print("  • Heartbeat: Daily at 08:00")
    print("\nScheduler is now running. Press Ctrl+C to exit.\n")

    _start_health_server()

    scheduler = BackgroundScheduler()

    # Weekly job: Monday 09:00
    scheduler.add_job(
        weekly_job,
        trigger=CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="weekly_check",
        name="Weekly check (regime, crash, drawdown)",
        replace_existing=True,
    )

    # Monthly job: runs every day at 09:00, but checks internally if it's the first trading day
    scheduler.add_job(
        monthly_job,
        trigger=CronTrigger(hour=9, minute=0),
        id="monthly_rebalance",
        name="Monthly rebalance (runs on first trading day only)",
        replace_existing=True,
    )

    # Heartbeat: daily at 08:00
    scheduler.add_job(
        heartbeat_job,
        trigger=CronTrigger(hour=8, minute=0),
        id="heartbeat",
        name="Heartbeat (daily monitoring)",
        replace_existing=True,
    )

    try:
        scheduler.start()
        print("Scheduler started — waiting for jobs.\n")
        # Keep main thread alive; BackgroundScheduler runs jobs on its own threads
        while True:
            time.sleep(60)
            if not scheduler.running:
                print("Scheduler stopped unexpectedly — exiting.")
                sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nScheduler stopped by user.")
        scheduler.shutdown()
    except Exception as e:
        print(f"\n\nScheduler crashed: {e}")
        import traceback
        traceback.print_exc()
        scheduler.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()
