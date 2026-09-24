from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, timedelta

from gridcast_public.durable_store import connect_postgres


def prune(
    dsn: str,
    *,
    schema: str = "public",
    retention_days: int = 30,
    max_terminal_runs: int = 1000,
) -> int:
    if retention_days < 1 or max_terminal_runs < 1:
        raise ValueError("retention_days and max_terminal_runs must be positive")
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    with connect_postgres(dsn, schema) as db:
        old = db.execute(
            "SELECT request_id FROM runs WHERE status IN ('succeeded','failed') "
            "AND updated_at<%s",
            (cutoff,),
        ).fetchall()
        excess = db.execute(
            "SELECT request_id FROM runs WHERE status IN ('succeeded','failed') "
            "ORDER BY updated_at DESC, request_id DESC OFFSET %s",
            (max_terminal_runs,),
        ).fetchall()
        ids = list({row["request_id"] for row in old + excess})
        if not ids:
            return 0
        removed = db.execute(
            "DELETE FROM runs WHERE request_id = ANY(%s) "
            "AND status IN ('succeeded','failed')",
            (ids,),
        ).rowcount
        return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune old terminal public-preview runs.")
    parser.add_argument(
        "--days", type=int, default=30, help="retain terminal tasks for at least this many days"
    )
    parser.add_argument(
        "--max-terminal-runs", type=int, default=1000, help="maximum terminal tasks after pruning"
    )
    args = parser.parse_args()
    dsn = os.getenv("GRIDCAST_DATABASE_URL")
    if not dsn:
        parser.error("GRIDCAST_DATABASE_URL is required")
    removed = prune(
        dsn,
        schema=os.getenv("GRIDCAST_DB_SCHEMA", "public"),
        retention_days=args.days,
        max_terminal_runs=args.max_terminal_runs,
    )
    print(f"Pruned {removed} terminal task(s). Active and recoverable tasks were retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
