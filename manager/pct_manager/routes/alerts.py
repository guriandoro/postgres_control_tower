"""Alerts API (P7).

- ``GET    /api/v1/alerts``                list with filters (status, kind, cluster)
- ``GET    /api/v1/alerts/summary``        small counts payload for the dashboard
- ``POST   /api/v1/alerts/{id}/ack``       acknowledge an open alert (admin only)

Alerts are *opened/resolved* by the rule engine (``alerter/dispatcher.py``).
The UI cannot create alerts directly; ``ack`` only silences notifications,
it does not mark the alert resolved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_admin
from ..db import get_db
from ..models import Alert, User
from ..schemas import AlertAckResponse, AlertKind, AlertOut

router = APIRouter()

AlertStatusFilter = Literal["open", "resolved", "acknowledged", "all"]


@router.get("", response_model=list[AlertOut])
def list_alerts(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    status_filter: AlertStatusFilter = Query(default="open", alias="status"),
    kind: AlertKind | None = None,
    cluster_id: int | None = None,
    limit: int = Query(default=200, ge=1, le=1_000),
) -> list[Alert]:
    stmt = select(Alert).order_by(Alert.opened_at.desc()).limit(limit)
    if status_filter == "open":
        stmt = stmt.where(Alert.resolved_at.is_(None))
    elif status_filter == "resolved":
        stmt = stmt.where(Alert.resolved_at.is_not(None))
    elif status_filter == "acknowledged":
        stmt = stmt.where(
            Alert.resolved_at.is_(None),
            Alert.acknowledged_at.is_not(None),
        )
    # "all" => no filter
    if kind is not None:
        stmt = stmt.where(Alert.kind == kind)
    if cluster_id is not None:
        stmt = stmt.where(Alert.cluster_id == cluster_id)
    return list(db.scalars(stmt).all())


@router.get("/summary")
def alerts_summary(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> dict[str, int | dict[str, int]]:
    """Cheap counts payload used by the Dashboard hero card."""
    open_total = db.scalar(
        select(func.count(Alert.id)).where(Alert.resolved_at.is_(None))
    ) or 0
    by_severity_rows = db.execute(
        select(Alert.severity, func.count(Alert.id))
        .where(Alert.resolved_at.is_(None))
        .group_by(Alert.severity)
    ).all()
    acked = db.scalar(
        select(func.count(Alert.id)).where(
            Alert.resolved_at.is_(None),
            Alert.acknowledged_at.is_not(None),
        )
    ) or 0
    return {
        "open_total": int(open_total),
        "open_acknowledged": int(acked),
        "by_severity": {sev: int(n) for sev, n in by_severity_rows},
    }


@router.post("/{alert_id}/ack", response_model=AlertAckResponse)
def acknowledge_alert(
    alert_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin)],
) -> AlertAckResponse:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    if alert.resolved_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Alert is already resolved; nothing to acknowledge",
        )
    if alert.acknowledged_at is not None:
        # Idempotent ack — return the existing timestamp.
        return AlertAckResponse(id=alert.id, acknowledged_at=alert.acknowledged_at)
    now = datetime.now(timezone.utc)
    alert.acknowledged_at = now
    alert.acknowledged_by = user.id
    db.commit()
    return AlertAckResponse(id=alert.id, acknowledged_at=now)
