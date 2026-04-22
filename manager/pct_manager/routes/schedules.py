"""Backup schedules — UI-facing CRUD.

Endpoints (all require a UI session JWT; writes require admin):

- ``GET    /api/v1/schedules``        list all schedules
- ``POST   /api/v1/schedules``        create a recurring backup
- ``PATCH  /api/v1/schedules/{id}``   toggle / edit cron / params
- ``DELETE /api/v1/schedules/{id}``   remove a schedule

The actual firing happens in ``pct_manager.schedules.evaluate_backup_schedules``,
called every minute from APScheduler.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_admin
from ..db import get_db
from ..models import BackupSchedule, Cluster, User
from ..schedules import InvalidCronExpression, compute_next_run
from ..schemas import (
    BackupScheduleCreateRequest,
    BackupScheduleOut,
    BackupScheduleUpdateRequest,
)

router = APIRouter()


@router.get("", response_model=list[BackupScheduleOut])
def list_schedules(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    cluster_id: int | None = None,
) -> list[BackupSchedule]:
    stmt = select(BackupSchedule).order_by(BackupSchedule.id.asc())
    if cluster_id is not None:
        stmt = stmt.where(BackupSchedule.cluster_id == cluster_id)
    return list(db.scalars(stmt).all())


@router.post(
    "",
    response_model=BackupScheduleOut,
    status_code=status.HTTP_201_CREATED,
)
def create_schedule(
    body: BackupScheduleCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin)],
) -> BackupSchedule:
    cluster = db.get(Cluster, body.cluster_id)
    if cluster is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cluster not found")

    try:
        next_run = compute_next_run(body.cron_expression) if body.enabled else None
    except InvalidCronExpression as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Invalid cron expression: {exc}",
        ) from exc

    schedule = BackupSchedule(
        cluster_id=body.cluster_id,
        kind=body.kind,
        cron_expression=body.cron_expression,
        params=body.params,
        enabled=body.enabled,
        created_by=user.id,
        next_run_at=next_run,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


@router.patch("/{schedule_id}", response_model=BackupScheduleOut)
def update_schedule(
    schedule_id: int,
    body: BackupScheduleUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> BackupSchedule:
    schedule = db.get(BackupSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")

    cron_changed = (
        body.cron_expression is not None
        and body.cron_expression != schedule.cron_expression
    )
    enabling = body.enabled is True and not schedule.enabled

    if body.cron_expression is not None:
        schedule.cron_expression = body.cron_expression
    if body.params is not None:
        schedule.params = body.params
    if body.kind is not None:
        schedule.kind = body.kind
    if body.enabled is not None:
        schedule.enabled = body.enabled

    if not schedule.enabled:
        schedule.next_run_at = None
    elif cron_changed or enabling or schedule.next_run_at is None:
        # Recompute from "now" so a schedule that was paused for a long
        # time doesn't fire its full backlog the moment it's re-enabled.
        try:
            schedule.next_run_at = compute_next_run(
                schedule.cron_expression,
                after=datetime.now(timezone.utc),
            )
        except InvalidCronExpression as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Invalid cron expression: {exc}",
            ) from exc

    db.commit()
    db.refresh(schedule)
    return schedule


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> None:
    schedule = db.get(BackupSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    db.delete(schedule)
    db.commit()
