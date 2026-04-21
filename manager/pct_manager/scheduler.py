"""APScheduler bootstrapping.

Runs:
- daily partition maintenance for ``logs.events``
- daily retention purge for ``logs.events`` partitions
- periodic alert rule evaluation (P7)
- periodic storage runway forecast refresh (P7)
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .alerter import evaluate_rules, refresh_storage_forecasts
from .config import settings
from .partitions import ensure_log_partitions, prune_old_log_partitions

logger = logging.getLogger(__name__)


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        _safe(ensure_log_partitions),
        CronTrigger(hour=0, minute=10, timezone="UTC"),
        id="ensure_log_partitions",
        replace_existing=True,
    )
    scheduler.add_job(
        _safe(_prune_logs),
        CronTrigger(hour=0, minute=20, timezone="UTC"),
        id="prune_old_log_partitions",
        replace_existing=True,
    )

    # P7 — Alerting + storage runway. Both jobs are sync; APScheduler
    # runs them on its worker thread, which is fine because none of the
    # rule code touches the async event loop.
    scheduler.add_job(
        _safe(evaluate_rules),
        IntervalTrigger(seconds=settings.alert_eval_interval),
        id="evaluate_alert_rules",
        replace_existing=True,
        # If the manager is paused (e.g., debugger), don't fire the
        # backlog all at once on resume.
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _safe(refresh_storage_forecasts),
        IntervalTrigger(seconds=settings.forecast_interval_seconds),
        id="refresh_storage_forecasts",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return scheduler


def run_startup_jobs() -> None:
    """Execute the maintenance jobs once on boot, before the scheduler kicks
    in, so a fresh manager doesn't have to wait until midnight UTC for the
    first partition to be created (and so the Alerts page isn't empty for
    a whole minute after startup)."""
    for fn in (ensure_log_partitions, _prune_logs, evaluate_rules, refresh_storage_forecasts):
        try:
            fn()
        except Exception:  # noqa: BLE001
            logger.exception("Initial %s failed", fn.__name__)


def _prune_logs() -> list[str]:
    return prune_old_log_partitions(settings.log_retention_days)


def _safe(fn):
    """APScheduler swallows exceptions silently by default; log them."""

    def _wrapped(*args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            logger.exception("Scheduled job %s failed", fn.__name__)

    _wrapped.__name__ = f"safe_{fn.__name__}"
    return _wrapped
