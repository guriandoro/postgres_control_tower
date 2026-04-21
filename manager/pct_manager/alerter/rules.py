"""Rule definitions for the alert engine.

Each rule produces zero or more :class:`RuleHit`s; the dispatcher folds
hits into open/resolved alert rows and decides which notifiers to call.

Thresholds are intentionally hardcoded — operators tune them by sending
a PR (and remembering to update ``docs/safety-and-rbac.md``). The only
runtime knob is whether each rule fires at all, governed by data
availability (no WAL samples → no WAL alerts).

Rules:
- ``wal_lag``       : latest ``wal_health.archive_lag_seconds`` > 900s
- ``backup_failed`` : the most recent ``pct.jobs`` row of any backup_*
                     kind for an agent finished as ``failed``
- ``clock_drift``   : ``|agent.clock_skew_ms| > 2000`` and seen in 5m
- ``role_flapping`` : >= 3 role transitions in last 10m for one agent
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Agent, Job, RoleTransition, WalHealth

log = logging.getLogger("pct_manager.alerter.rules")

# Thresholds — see module docstring.
WAL_LAG_THRESHOLD_SECONDS = 15 * 60
CLOCK_DRIFT_THRESHOLD_MS = 2_000
CLOCK_DRIFT_FRESH_SECONDS = 5 * 60
FLAPPING_WINDOW_MINUTES = 10
FLAPPING_TRANSITION_THRESHOLD = 3


@dataclass(frozen=True)
class RuleHit:
    """One firing rule for a particular target.

    The dispatcher uses ``(kind, cluster_id, dedup_key)`` as the dedup
    primary key. ``payload`` is JSON-merged onto the existing alert row
    on every evaluation pass.
    """

    kind: str
    severity: str  # 'info' | 'warning' | 'critical'
    cluster_id: int | None
    dedup_key: str
    payload: dict[str, Any] = field(default_factory=dict)


# ---------- individual rules ----------


def rule_wal_lag(db: Session) -> list[RuleHit]:
    """Latest WAL sample per agent; alert when archive_lag > 15m."""
    # Latest captured_at per agent via correlated subquery.
    latest_subq = (
        select(
            WalHealth.agent_id.label("agent_id"),
            func.max(WalHealth.captured_at).label("captured_at"),
        )
        .group_by(WalHealth.agent_id)
        .subquery()
    )
    rows = db.execute(
        select(WalHealth, Agent)
        .join(
            latest_subq,
            (WalHealth.agent_id == latest_subq.c.agent_id)
            & (WalHealth.captured_at == latest_subq.c.captured_at),
        )
        .join(Agent, Agent.id == WalHealth.agent_id)
    ).all()

    hits: list[RuleHit] = []
    for wh, agent in rows:
        lag = wh.archive_lag_seconds
        if lag is None or lag <= WAL_LAG_THRESHOLD_SECONDS:
            continue
        hits.append(
            RuleHit(
                kind="wal_lag",
                severity="critical" if lag > 3600 else "warning",
                cluster_id=agent.cluster_id,
                dedup_key=f"agent:{agent.id}",
                payload={
                    "agent_id": agent.id,
                    "hostname": agent.hostname,
                    "archive_lag_seconds": int(lag),
                    "captured_at": wh.captured_at.isoformat(),
                    "threshold_seconds": WAL_LAG_THRESHOLD_SECONDS,
                },
            )
        )
    return hits


def rule_backup_failed(db: Session) -> list[RuleHit]:
    """Alert if the most recent backup_* job for any agent is ``failed``.

    Only the *latest* job matters: a successful run after a failure
    closes the alert. We don't alert on ``check`` or ``stanza_create``
    failures (operators usually run those interactively).
    """
    backup_kinds = ("backup_full", "backup_diff", "backup_incr")

    latest_subq = (
        select(
            Job.agent_id.label("agent_id"),
            func.max(Job.id).label("max_id"),
        )
        .where(Job.kind.in_(backup_kinds))
        .group_by(Job.agent_id)
        .subquery()
    )
    rows = db.execute(
        select(Job, Agent)
        .join(latest_subq, Job.id == latest_subq.c.max_id)
        .join(Agent, Agent.id == Job.agent_id)
    ).all()

    hits: list[RuleHit] = []
    for job, agent in rows:
        if job.status != "failed":
            continue
        hits.append(
            RuleHit(
                kind="backup_failed",
                severity="critical",
                cluster_id=agent.cluster_id,
                dedup_key=f"agent:{agent.id}",
                payload={
                    "agent_id": agent.id,
                    "hostname": agent.hostname,
                    "job_id": job.id,
                    "kind": job.kind,
                    "exit_code": job.exit_code,
                    "finished_at": (
                        job.finished_at.isoformat() if job.finished_at else None
                    ),
                    "stdout_tail": (job.stdout_tail or "")[-1_500:],
                },
            )
        )
    return hits


def rule_clock_drift(db: Session) -> list[RuleHit]:
    """Alert when |clock_skew_ms| > 2000 on a recently-seen agent."""
    fresh_after = datetime.now(timezone.utc) - timedelta(
        seconds=CLOCK_DRIFT_FRESH_SECONDS
    )
    rows = db.execute(
        select(Agent).where(
            Agent.last_seen_at != None,  # noqa: E711
            Agent.last_seen_at >= fresh_after,
            Agent.clock_skew_ms != None,  # noqa: E711
            func.abs(Agent.clock_skew_ms) > CLOCK_DRIFT_THRESHOLD_MS,
        )
    ).scalars().all()

    hits: list[RuleHit] = []
    for agent in rows:
        skew = int(agent.clock_skew_ms or 0)
        hits.append(
            RuleHit(
                kind="clock_drift",
                severity="warning" if abs(skew) < 30_000 else "critical",
                cluster_id=agent.cluster_id,
                dedup_key=f"agent:{agent.id}",
                payload={
                    "agent_id": agent.id,
                    "hostname": agent.hostname,
                    "clock_skew_ms": skew,
                    "threshold_ms": CLOCK_DRIFT_THRESHOLD_MS,
                    "last_seen_at": (
                        agent.last_seen_at.isoformat() if agent.last_seen_at else None
                    ),
                },
            )
        )
    return hits


def rule_role_flapping(db: Session) -> list[RuleHit]:
    """Alert when an agent has >= N role transitions in the last 10m."""
    since = datetime.now(timezone.utc) - timedelta(minutes=FLAPPING_WINDOW_MINUTES)
    rows = db.execute(
        select(
            RoleTransition.agent_id,
            func.count(RoleTransition.id).label("n"),
        )
        .where(RoleTransition.ts_utc >= since)
        .group_by(RoleTransition.agent_id)
        .having(func.count(RoleTransition.id) >= FLAPPING_TRANSITION_THRESHOLD)
    ).all()

    hits: list[RuleHit] = []
    for agent_id, n in rows:
        agent = db.get(Agent, agent_id)
        if agent is None:
            continue
        hits.append(
            RuleHit(
                kind="role_flapping",
                severity="critical",
                cluster_id=agent.cluster_id,
                dedup_key=f"agent:{agent.id}",
                payload={
                    "agent_id": agent.id,
                    "hostname": agent.hostname,
                    "transitions_last_window": int(n),
                    "window_minutes": FLAPPING_WINDOW_MINUTES,
                    "threshold": FLAPPING_TRANSITION_THRESHOLD,
                },
            )
        )
    return hits


ALL_RULES = (rule_wal_lag, rule_backup_failed, rule_clock_drift, rule_role_flapping)
