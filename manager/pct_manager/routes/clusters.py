"""Cluster read views (UI-facing).

Endpoints:
- ``GET /api/v1/clusters`` — fleet summary, one row per cluster.
- ``GET /api/v1/clusters/{id}`` — per-cluster detail, with embedded agents
  and their latest pgBackRest snapshot + WAL health sample.
- ``GET /api/v1/clusters/{id}/storage_forecast`` — latest forecast row.
- ``GET /api/v1/clusters/{id}/wal_health`` — per-agent WAL lag history
  for the sparkline.

Both require a UI session (JWT). Agent ingest endpoints live under
``/api/v1/agents/*`` and use bearer tokens.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import (
    Agent,
    Cluster,
    PatroniState,
    PgbackrestInfo,
    StorageForecast,
    User,
    WalHealth,
)
from ..schemas import (
    AgentDetail,
    ClusterDetail,
    ClusterSummary,
    ClusterWalHealth,
    PatroniStateOut,
    PgbackrestInfoOut,
    StorageForecastOut,
    WalHealthOut,
    WalHealthSeries,
)

router = APIRouter()

# Freshness window used by the fleet view's "Agents online" counter. Matches
# the UI's 5-minute cluster freshness badge and ``CLOCK_DRIFT_FRESH_SECONDS``
# in the alerter — an agent that hasn't heartbeat within this window is not
# counted as online. Kept here (not in config.py) because this is a
# presentation concern for the dashboard, not a tuning knob.
ONLINE_FRESH_SECONDS = 5 * 60


@router.get("", response_model=list[ClusterSummary])
def list_clusters(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> list[ClusterSummary]:
    """Fleet view. Adds agent counts and last_seen_at across the cluster.

    ``agents_online`` counts agents whose last heartbeat lands within
    ``ONLINE_FRESH_SECONDS``; ``agent_count`` stays the *registered*
    total so the per-cluster card can still show e.g. "3 agents" even
    when some are stale.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ONLINE_FRESH_SECONDS)
    stmt = (
        select(
            Cluster,
            func.count(Agent.id).label("agent_count"),
            func.count(Agent.id)
            .filter(Agent.last_seen_at >= cutoff)
            .label("agents_online"),
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
            agents_online=agents_online,
            last_seen_at=last_seen_at,
        )
        for cluster, agent_count, agents_online, last_seen_at in rows
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
        latest_patroni = db.scalar(
            select(PatroniState)
            .where(PatroniState.agent_id == agent.id)
            .order_by(PatroniState.captured_at.desc())
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
                latest_patroni_state=(
                    PatroniStateOut.model_validate(latest_patroni)
                    if latest_patroni
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


@router.get(
    "/{cluster_id}/wal_health",
    response_model=ClusterWalHealth,
)
def get_cluster_wal_health(
    cluster_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    since_minutes: int = Query(
        default=60,
        ge=1,
        le=24 * 60,
        description="Look-back window for WAL samples, in minutes.",
    ),
    max_per_agent: int = Query(
        default=300,
        ge=1,
        le=2000,
        description=(
            "Hard cap on samples returned per agent. The collector ticks every "
            "30s, so the default keeps roughly 2.5h of resolution if the look-"
            "back window is widened."
        ),
    ),
) -> ClusterWalHealth:
    """Per-agent WAL archival history for the cluster's sparkline.

    Returns one ``WalHealthSeries`` per agent currently attached to the
    cluster (even if it has no samples in the window — empty
    ``samples`` keeps the UI's per-agent legend stable across renders).
    Samples are ordered oldest-first so the chart can plot them as-is.
    """
    cluster = db.get(Cluster, cluster_id)
    if cluster is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cluster not found")

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    agents = list(
        db.scalars(
            select(Agent).where(Agent.cluster_id == cluster_id).order_by(Agent.id)
        ).all()
    )

    series: list[WalHealthSeries] = []
    for agent in agents:
        # Pull the newest N rows in the window, then reverse for plotting.
        rows = list(
            db.scalars(
                select(WalHealth)
                .where(
                    WalHealth.agent_id == agent.id,
                    WalHealth.captured_at >= cutoff,
                )
                .order_by(WalHealth.captured_at.desc())
                .limit(max_per_agent)
            ).all()
        )
        rows.reverse()
        series.append(
            WalHealthSeries(
                agent_id=agent.id,
                hostname=agent.hostname,
                role=agent.role,  # type: ignore[arg-type]
                samples=[WalHealthOut.model_validate(r) for r in rows],
            )
        )

    return ClusterWalHealth(
        cluster_id=cluster_id,
        since_minutes=since_minutes,
        series=series,
    )
