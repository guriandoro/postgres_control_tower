"""Cluster read views (UI-facing).

Two endpoints:
- ``GET /api/v1/clusters`` — fleet summary, one row per cluster.
- ``GET /api/v1/clusters/{id}`` — per-cluster detail, with embedded agents
  and their latest pgBackRest snapshot + WAL health sample.

Both require a UI session (JWT). Agent ingest endpoints live under
``/api/v1/agents/*`` and use bearer tokens.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import Agent, Cluster, PgbackrestInfo, StorageForecast, User, WalHealth
from ..schemas import (
    AgentDetail,
    ClusterDetail,
    ClusterSummary,
    PgbackrestInfoOut,
    StorageForecastOut,
    WalHealthOut,
)

router = APIRouter()


@router.get("", response_model=list[ClusterSummary])
def list_clusters(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> list[ClusterSummary]:
    """Fleet view. Adds agent_count and last_seen_at across the cluster."""
    stmt = (
        select(
            Cluster,
            func.count(Agent.id).label("agent_count"),
            func.max(Agent.last_seen_at).label("last_seen_at"),
        )
        .outerjoin(Agent, Agent.cluster_id == Cluster.id)
        .group_by(Cluster.id)
        .order_by(Cluster.id)
    )
    rows = db.execute(stmt).all()
    return [
        ClusterSummary(
            id=cluster.id,
            name=cluster.name,
            kind=cluster.kind,  # type: ignore[arg-type]
            created_at=cluster.created_at,
            agent_count=agent_count,
            last_seen_at=last_seen_at,
        )
        for cluster, agent_count, last_seen_at in rows
    ]


@router.get("/{cluster_id}", response_model=ClusterDetail)
def get_cluster(
    cluster_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> ClusterDetail:
    """Per-cluster detail with the latest pgBackRest snapshot and WAL sample
    per agent. We fetch the latest rows with two ``DISTINCT ON``-style
    correlated subqueries (kept ORM-side for portability)."""
    cluster = db.get(Cluster, cluster_id)
    if cluster is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cluster not found")

    agents = list(
        db.scalars(
            select(Agent).where(Agent.cluster_id == cluster_id).order_by(Agent.id)
        ).all()
    )

    agent_details: list[AgentDetail] = []
    for agent in agents:
        latest_wal = db.scalar(
            select(WalHealth)
            .where(WalHealth.agent_id == agent.id)
            .order_by(WalHealth.captured_at.desc())
            .limit(1)
        )
        latest_pgbr = db.scalar(
            select(PgbackrestInfo)
            .where(PgbackrestInfo.agent_id == agent.id)
            .order_by(PgbackrestInfo.captured_at.desc())
            .limit(1)
        )
        agent_details.append(
            AgentDetail(
                id=agent.id,
                cluster_id=agent.cluster_id,
                hostname=agent.hostname,
                role=agent.role,  # type: ignore[arg-type]
                last_seen_at=agent.last_seen_at,
                version=agent.version,
                clock_skew_ms=agent.clock_skew_ms,
                created_at=agent.created_at,
                latest_wal_health=(
                    WalHealthOut.model_validate(latest_wal) if latest_wal else None
                ),
                latest_pgbackrest_info=(
                    PgbackrestInfoOut.model_validate(latest_pgbr)
                    if latest_pgbr
                    else None
                ),
            )
        )

    return ClusterDetail(
        id=cluster.id,
        name=cluster.name,
        kind=cluster.kind,  # type: ignore[arg-type]
        created_at=cluster.created_at,
        agents=agent_details,
    )


@router.get(
    "/{cluster_id}/storage_forecast",
    response_model=StorageForecastOut | None,
)
def get_storage_forecast(
    cluster_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> StorageForecast | None:
    """Latest "Storage Runway" forecast for the cluster.

    Returns ``null`` if the scheduler hasn't computed one yet (typically
    because there are fewer than two pgBackRest snapshots within the
    forecast window). The cluster itself must exist or we 404.
    """
    cluster = db.get(Cluster, cluster_id)
    if cluster is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cluster not found")
    return db.scalar(
        select(StorageForecast).where(StorageForecast.cluster_id == cluster_id)
    )
