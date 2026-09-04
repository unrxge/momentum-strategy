#!/usr/bin/env python3
"""Entry point for the momentum strategy scheduler.

Persistent process that stays alive and runs jobs on a schedule:
- Weekly checks (Mondays 09:00)
- Monthly rebalance (first trading day of month, 09:00)
- Heartbeat (daily, 08:00)
"""

import os
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from src.scheduler.jobs import weekly_job, monthly_job, heartbeat_job


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

    scheduler = BlockingScheduler()

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

    # Start the scheduler (blocks indefinitely)
    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\n\nScheduler stopped by user.")
        scheduler.shutdown()
    except Exception as e:
        print(f"\n\nScheduler crashed: {e}")
        import traceback
        traceback.print_exc()
        scheduler.shutdown()
        raise  # Re-raise so Railway sees a non-zero exit and restarts


if __name__ == "__main__":
    main()
