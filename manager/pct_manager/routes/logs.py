"""Log ingestion (agent push) and query (UI pull).

Two endpoints:

- ``POST /api/v1/logs/ingest``       agent → manager, batched, bearer auth.
- ``GET  /api/v1/logs/events``       UI → manager, filtered, JWT auth.

The ingest path also extracts role transitions: any record whose
``parsed["role_transition"]`` is set ``{"from": ..., "to": ...}`` is
duplicated into ``logs.role_transitions`` so the Cluster page can render
the leader-history Gantt without scanning the full event stream.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..auth import get_current_agent, get_current_user
from ..db import get_db
from ..models import Agent, LogEvent, RoleTransition, User
from ..schemas import (
    LogBatchIngest,
    LogEventOut,
    LogIngestAck,
    LogSeverity,
    LogSource,
    RoleTransitionOut,
)

router = APIRouter()

_MAX_QUERY_LIMIT = 1_000


@router.post(
    "/ingest",
    response_model=LogIngestAck,
    status_code=status.HTTP_201_CREATED,
)
def ingest_logs(
    batch: LogBatchIngest,
    db: Annotated[Session, Depends(get_db)],
    agent: Annotated[Agent, Depends(get_current_agent)],
) -> LogIngestAck:
    if not batch.records:
        return LogIngestAck(accepted=0, role_transitions=0)

    events: list[LogEvent] = []
    transitions: list[RoleTransition] = []
    for rec in batch.records:
        events.append(
            LogEvent(
                ts_utc=rec.ts_utc,
                agent_id=agent.id,
                source=rec.source,
                severity=rec.severity,
                raw=rec.raw,
                parsed=rec.parsed,
            )
        )
        # Optional, parser-driven role-transition signal — see PLAN §6.
        if rec.parsed and rec.source in ("patroni", "etcd"):
            rt = rec.parsed.get("role_transition")
            if isinstance(rt, dict) and "to" in rt:
                transitions.append(
                    RoleTransition(
                        ts_utc=rec.ts_utc,
                        agent_id=agent.id,
                        from_role=rt.get("from"),
                        to_role=str(rt["to"]),
                        source=rec.source,
                    )
                )

    db.add_all(events)
    db.add_all(transitions)
    db.commit()
    return LogIngestAck(accepted=len(events), role_transitions=len(transitions))


@router.get("/events", response_model=list[LogEventOut])
def query_events(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    cluster_id: int | None = None,
    agent_id: int | None = None,
    source: LogSource | None = None,
    severity: LogSeverity | None = None,
    since: datetime | None = Query(default=None, description="Inclusive lower bound on ts_utc"),
    until: datetime | None = Query(default=None, description="Exclusive upper bound on ts_utc"),
    q: str | None = Query(default=None, description="Substring match on parsed->>'message'"),
    limit: int = Query(default=200, ge=1, le=_MAX_QUERY_LIMIT),
) -> list[LogEvent]:
    """Filtered tail of recent events. Newest-first."""
    stmt = select(LogEvent).order_by(LogEvent.ts_utc.desc()).limit(limit)

    if cluster_id is not None:
        stmt = stmt.where(
            LogEvent.agent_id.in_(
                select(Agent.id).where(Agent.cluster_id == cluster_id)
            )
        )
    if agent_id is not None:
        stmt = stmt.where(LogEvent.agent_id == agent_id)
    if source is not None:
        stmt = stmt.where(LogEvent.source == source)
    if severity is not None:
        stmt = stmt.where(LogEvent.severity == severity)
    if since is not None:
        stmt = stmt.where(LogEvent.ts_utc >= since)
    if until is not None:
        stmt = stmt.where(LogEvent.ts_utc < until)
    if q:
        # Free-text search across the raw line and the parsed `message`
        # field. JSONB ->> on a missing key returns NULL, which fails
        # ILIKE safely, so the OR collapses to the raw match.
        needle = f"%{q}%"
        stmt = stmt.where(
            or_(
                LogEvent.raw.ilike(needle),
                LogEvent.parsed["message"].astext.ilike(needle),
            )
        )

    return list(db.scalars(stmt).all())


@router.get("/role_transitions", response_model=list[RoleTransitionOut])
def query_role_transitions(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    cluster_id: int | None = None,
    agent_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=200, ge=1, le=_MAX_QUERY_LIMIT),
) -> list[RoleTransition]:
    stmt = (
        select(RoleTransition)
        .order_by(RoleTransition.ts_utc.desc())
        .limit(limit)
    )
    if cluster_id is not None:
        stmt = stmt.where(
            RoleTransition.agent_id.in_(
                select(Agent.id).where(Agent.cluster_id == cluster_id)
            )
        )
    if agent_id is not None:
        stmt = stmt.where(RoleTransition.agent_id == agent_id)
    if since is not None:
        stmt = stmt.where(RoleTransition.ts_utc >= since)
    if until is not None:
        stmt = stmt.where(RoleTransition.ts_utc < until)
    return list(db.scalars(stmt).all())


# Reserved for future per-event detail view (P5 UI work)
@router.get("/events/{event_id}", response_model=LogEventOut)
def get_event(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> LogEvent:
    event = db.scalar(select(LogEvent).where(LogEvent.id == event_id))
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return event
