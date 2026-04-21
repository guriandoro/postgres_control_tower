"""Storage runway forecast.

For each cluster we fit a least-squares line to the trailing
``forecast_window_days`` of pgBackRest repo size and persist:

- ``daily_growth_bytes``: slope, in bytes/day (may be negative).
- ``current_bytes``: most recent observed total repo size.
- ``days_to_target``: when the trend would hit ``target_bytes`` (or
  null when growth is non-positive or target is unset).

We deliberately do NOT keep historical forecasts; one row per cluster,
overwritten in-place. Operators who need history can query
``pct.pgbackrest_info`` directly — the raw input is preserved.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import Agent, Cluster, PgbackrestInfo, StorageForecast

log = logging.getLogger("pct_manager.alerter.forecast")


def refresh_storage_forecasts() -> dict[str, int]:
    """Recompute the forecast row for every cluster. Returns counters."""
    counters = {"clusters": 0, "computed": 0, "skipped": 0}
    window_days = settings.forecast_window_days
    target_bytes = settings.forecast_target_bytes or None
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    with SessionLocal() as db:
        clusters = db.scalars(select(Cluster)).all()
        for cluster in clusters:
            counters["clusters"] += 1
            sample = _build_cluster_series(db, cluster.id, cutoff)
            if sample is None:
                # Not enough data points (need >= 2) — skip silently.
                counters["skipped"] += 1
                continue
            samples, current_bytes = sample
            slope = _linear_slope(samples)  # bytes per second
            daily = int(slope * 86_400)
            days_to_target = _days_to_target(
                current_bytes, daily, target_bytes
            )
            _upsert_forecast(
                db,
                cluster_id=cluster.id,
                sample_count=len(samples),
                daily_growth_bytes=daily,
                current_bytes=current_bytes,
                target_bytes=target_bytes,
                days_to_target=days_to_target,
            )
            counters["computed"] += 1
        db.commit()

    log.info(
        "Forecast pass: clusters=%d computed=%d skipped=%d",
        counters["clusters"],
        counters["computed"],
        counters["skipped"],
    )
    return counters


def _build_cluster_series(
    db: Session, cluster_id: int, cutoff: datetime
) -> tuple[list[tuple[float, int]], int] | None:
    """Aggregate per-snapshot total repo size across the cluster.

    Returns (series, latest_total) where series is a list of
    ``(epoch_seconds, total_bytes)`` tuples, latest first. Returns None
    if there isn't enough data to fit a line.
    """
    rows = db.execute(
        select(PgbackrestInfo.captured_at, PgbackrestInfo.payload)
        .join(Agent, Agent.id == PgbackrestInfo.agent_id)
        .where(
            Agent.cluster_id == cluster_id,
            PgbackrestInfo.captured_at >= cutoff,
        )
        .order_by(PgbackrestInfo.captured_at.asc())
    ).all()

    # Bucket by captured_at minute so two agents whose snapshots arrive
    # within the same minute count as one observation.
    buckets: dict[datetime, int] = defaultdict(int)
    for ts, payload in rows:
        size = _extract_total_size(payload)
        if size is None:
            continue
        bucket = ts.replace(second=0, microsecond=0)
        buckets[bucket] += size

    if len(buckets) < 2:
        return None

    series = sorted((ts.timestamp(), bytes_) for ts, bytes_ in buckets.items())
    latest_total = series[-1][1]
    return series, latest_total


def _extract_total_size(payload: Any) -> int | None:
    """Total bytes the pgBackRest repo currently holds for backups.

    pgBackRest's ``info --output=json`` does NOT publish a top-level
    "size" on each ``repo[]`` entry — those entries only carry
    ``{key, cipher, status}``. The per-backup footprint lives at
    ``backup[i].info.repository.delta`` (new bytes that backup added to
    the repo: equal to ``repository.size`` for fulls, the marginal delta
    for diff/incr). Summing that across every retained backup in every
    stanza approximates "what's in the repo right now" minus the WAL
    archive (whose byte counts aren't in this payload at all).

    Returns None when nothing usable was found, so callers can skip
    rather than persist a misleading zero.
    """
    if not isinstance(payload, list):
        return None
    total = 0
    found = False
    for stanza in payload:
        if not isinstance(stanza, dict):
            continue
        for backup in stanza.get("backup") or []:
            if not isinstance(backup, dict):
                continue
            info = backup.get("info")
            if not isinstance(info, dict):
                continue
            repository = info.get("repository")
            if not isinstance(repository, dict):
                continue
            # Prefer per-backup delta (true marginal bytes); fall back to
            # repository.size which equals delta for fulls and is at least
            # an upper bound for diff/incr if delta is somehow missing.
            value = repository.get("delta")
            if not isinstance(value, (int, float)):
                value = repository.get("size")
            if isinstance(value, (int, float)):
                total += int(value)
                found = True
    return total if found else None


def _linear_slope(series: list[tuple[float, int]]) -> float:
    """Least-squares slope of bytes vs. seconds. NumPy not required."""
    n = len(series)
    if n < 2:
        return 0.0
    mean_x = sum(x for x, _ in series) / n
    mean_y = sum(y for _, y in series) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in series)
    den = sum((x - mean_x) ** 2 for x, _ in series)
    if den == 0:
        return 0.0
    return num / den


def _days_to_target(
    current: int, daily_growth: int, target: int | None
) -> float | None:
    if target is None or target <= 0:
        return None
    if daily_growth <= 0:
        return None
    if current >= target:
        return 0.0
    return (target - current) / daily_growth


def _upsert_forecast(
    db: Session,
    *,
    cluster_id: int,
    sample_count: int,
    daily_growth_bytes: int,
    current_bytes: int,
    target_bytes: int | None,
    days_to_target: float | None,
) -> None:
    """One-row-per-cluster upsert via PG ``ON CONFLICT``."""
    stmt = pg_insert(StorageForecast).values(
        cluster_id=cluster_id,
        captured_at=datetime.now(timezone.utc),
        sample_count=sample_count,
        daily_growth_bytes=daily_growth_bytes,
        current_bytes=current_bytes,
        target_bytes=target_bytes,
        days_to_target=days_to_target,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_storage_forecast_cluster",
        set_={
            "captured_at": stmt.excluded.captured_at,
            "sample_count": stmt.excluded.sample_count,
            "daily_growth_bytes": stmt.excluded.daily_growth_bytes,
            "current_bytes": stmt.excluded.current_bytes,
            "target_bytes": stmt.excluded.target_bytes,
            "days_to_target": stmt.excluded.days_to_target,
        },
    )
    db.execute(stmt)
