"""Recurring backup schedules — cron parser + APScheduler tick.

A schedule is a cron expression (UTC) attached to a cluster. Once a
minute the manager scans enabled rows, fires any whose ``next_run_at``
is past, and recomputes ``next_run_at`` from the cron expression.

Firing a schedule means inserting a ``pct.jobs`` row exactly like the
UI does — the agent runner can't tell the two apart, and the job
allowlist is enforced at the same layer (defense in depth, see
``docs/safety-and-rbac.md``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Agent, BackupSchedule, Job
from .schemas import BACKUP_SCHEDULE_KINDS

log = logging.getLogger("pct_manager.schedules")


class InvalidCronExpression(ValueError):
    """Raised when a user-supplied cron string fails to parse."""


def parse_cron(expression: str) -> CronTrigger:
    """Validate ``expression`` and return a UTC ``CronTrigger``.

    Translates APScheduler's ``ValueError`` (which carries detail like
    "Wrong number of fields") into a domain-specific exception so the
    route can map it to a 400 with a clear message.
    """
    try:
        return CronTrigger.from_crontab(expression, timezone="UTC")
    except (ValueError, TypeError) as exc:
        raise InvalidCronExpression(str(exc)) from exc


def compute_next_run(expression: str, after: datetime | None = None) -> datetime | None:
    """Next fire time after ``after`` (defaults to "now"). May be None
    if the cron will never fire again (APScheduler returns None for
    e.g. impossible date combinations)."""
    trigger = parse_cron(expression)
    base = after or datetime.now(timezone.utc)
    return trigger.get_next_fire_time(None, base)


def evaluate_backup_schedules() -> dict[str, int]:
    """Tick: fire every schedule whose ``next_run_at`` is past.

    Returns a small stat dict mainly so the scheduler logs are useful
    when debugging missed runs. Idempotent w.r.t. transient DB failures
    — the next tick will pick up anything we missed because we only
    advance ``next_run_at`` after the job row is committed.
    """
    fired = 0
    skipped_no_agent = 0
    skipped_invalid = 0
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        due = list(
            db.scalars(
                select(BackupSchedule)
                .where(
                    BackupSchedule.enabled.is_(True),
                    BackupSchedule.next_run_at.is_not(None),
                    BackupSchedule.next_run_at <= now,
                )
                .order_by(BackupSchedule.next_run_at.asc())
            ).all()
        )
        for schedule in due:
            # Defense in depth: a future migration could leave a stray
            # row with a kind no longer in the allowlist. Skip it
            # rather than insert a job the agent will refuse.
            if schedule.kind not in BACKUP_SCHEDULE_KINDS:
                log.warning(
                    "Schedule %s has disallowed kind %r; skipping",
                    schedule.id,
                    schedule.kind,
                )
                skipped_invalid += 1
                schedule.enabled = False
                db.commit()
                continue

            agent = _route_to_agent(db, schedule.cluster_id)
            if agent is None:
                log.warning(
                    "Schedule %s for cluster %s has no agents; will retry next tick",
                    schedule.id,
                    schedule.cluster_id,
                )
                skipped_no_agent += 1
                # Don't advance next_run_at — we want to retry.
                continue

            job = Job(
                agent_id=agent.id,
                kind=schedule.kind,
                params=dict(schedule.params),
                status="pending",
                requested_by=None,  # scheduler-issued, not a UI user
            )
            db.add(job)
            db.flush()  # populate job.id

            schedule.last_run_at = now
            schedule.last_job_id = job.id
            try:
                schedule.next_run_at = compute_next_run(
                    schedule.cron_expression, after=now
                )
            except InvalidCronExpression:
                log.exception(
                    "Schedule %s has invalid cron %r; disabling",
                    schedule.id,
                    schedule.cron_expression,
                )
                schedule.enabled = False
                schedule.next_run_at = None
            db.commit()
            fired += 1
            log.info(
                "Schedule %s fired job %s (cluster=%s kind=%s)",
                schedule.id,
                job.id,
                schedule.cluster_id,
                schedule.kind,
            )

    return {
        "fired": fired,
        "skipped_no_agent": skipped_no_agent,
        "skipped_invalid": skipped_invalid,
    }


def _route_to_agent(db: Session, cluster_id: int) -> Agent | None:
    """Pick the agent that should run a cluster's backup.

    Mirrors the routing used by ``POST /api/v1/jobs`` so the UI
    submission and scheduler dispatch land on the same node: prefer
    ``primary``, otherwise the lowest-id agent. We don't ship a
    cluster-wide failover policy in v1 — if the primary is offline the
    job will simply queue until it comes back.
    """
    return db.scalar(
        select(Agent)
        .where(Agent.cluster_id == cluster_id)
        .order_by(
            (Agent.role == "primary").desc(),
            Agent.id.asc(),
        )
        .limit(1)
    )
