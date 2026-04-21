"""Monthly partition lifecycle for ``logs.events``.

Two operations:

- :func:`ensure_log_partitions` — make sure the partitions covering the
  current month *and* the next month exist. Run on a daily schedule so the
  first write of the new month never blocks waiting for partition DDL.
- :func:`prune_old_log_partitions` — drop partitions whose **entire** range
  falls outside the retention window. We never drop a partition that still
  holds rows we'd want to query.

Both are sync (DDL) and intended to be wrapped by APScheduler in
:mod:`pct_manager.scheduler`.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from .db import engine

logger = logging.getLogger(__name__)


_PARTITION_NAME_RE = re.compile(r"^events_(\d{4})_(\d{2})$")


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start, end


def _partition_name(d: date) -> str:
    return f"events_{d.year:04d}_{d.month:02d}"


def ensure_log_partitions(months_ahead: int = 1) -> list[str]:
    """Create partitions for the current month and the next ``months_ahead``.

    Returns the list of partitions that were *newly* created (existing ones
    are skipped via ``IF NOT EXISTS``-style detection).
    """
    today = datetime.now(timezone.utc).date()
    targets: list[date] = [date(today.year, today.month, 1)]
    cursor = targets[0]
    for _ in range(months_ahead):
        cursor = _next_month(cursor)
        targets.append(cursor)

    created: list[str] = []
    with engine.begin() as conn:
        existing = _list_partitions(conn)
        for first_of_month in targets:
            name = _partition_name(first_of_month)
            if name in existing:
                continue
            start, end = _month_bounds(first_of_month.year, first_of_month.month)
            conn.execute(
                text(
                    f"CREATE TABLE logs.{name} PARTITION OF logs.events "
                    f"FOR VALUES FROM ('{start.isoformat()}') "
                    f"TO ('{end.isoformat()}')"
                )
            )
            created.append(name)
            logger.info("Created log partition %s", name)
    return created


def prune_old_log_partitions(retention_days: int) -> list[str]:
    """Drop any monthly partition whose end-of-range is older than the
    retention threshold.

    A partition is only dropped when the *whole* month it covers falls
    outside the keep-window — never half-pruned.
    """
    if retention_days <= 0:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).date()
    cutoff_first_of_month = date(cutoff.year, cutoff.month, 1)

    dropped: list[str] = []
    with engine.begin() as conn:
        for name in _list_partitions(conn):
            match = _PARTITION_NAME_RE.match(name)
            if not match:
                continue
            year, month = int(match.group(1)), int(match.group(2))
            _, end = _month_bounds(year, month)
            if end <= cutoff_first_of_month:
                conn.execute(text(f"DROP TABLE IF EXISTS logs.{name}"))
                dropped.append(name)
                logger.info("Dropped expired log partition %s", name)
    return dropped


def _list_partitions(conn) -> set[str]:  # type: ignore[no-untyped-def]
    rows = conn.execute(
        text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'logs' AND tablename LIKE 'events_%'"
        )
    ).all()
    return {row[0] for row in rows}


def _next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
