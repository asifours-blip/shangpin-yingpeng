"""Explicit once or polling scheduler entry point, never started by API startup."""

import argparse
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.services.operation_plans import tick as tick_plans


def tick(db: Session, *, now: datetime | None = None, limit: int = 10) -> list[int]:
    return tick_plans(db, now=now, limit=limit)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run operations scheduling passes")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Run one pass and exit")
    mode.add_argument("--loop", action="store_true", help="Poll until stopped")
    parser.add_argument("--poll-seconds", type=int, default=60, help="Loop interval in seconds (1-3600)")
    args = parser.parse_args()
    if not 1 <= args.poll_seconds <= 3600:
        parser.error("--poll-seconds must be between 1 and 3600")
    while True:
        with SessionLocal() as db:
            ids = tick(db, now=datetime.now(timezone.utc))
        print(f"operation scheduler tick complete: {len(ids)} round(s)")
        if args.once:
            break
        try:
            time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
