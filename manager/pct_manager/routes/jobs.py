"""Safe Ops — UI-facing jobs API.

Endpoints (all require a UI session JWT; ``POST`` requires admin):

- ``POST   /api/v1/jobs``       create a new job
- ``GET    /api/v1/jobs``       filtered list (latest first)
- ``GET    /api/v1/jobs/{id}``  detail (status, exit code, stdout tail)

Agent-side claim/result endpoints live under ``/api/v1/agents/jobs/*``
in ``routes/agents.py`` so they share the bearer-token dependency.

The kind allowlist is defined in ``schemas.JOB_KINDS`` and enforced
both here (FastAPI's literal validator) and in the agent runner
(defense in depth — see ``agent/pct_agent/runner.py``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_admin
from ..db import get_db
from ..models import Agent, Job, User
from ..schemas import (
    JobCreateRequest,
    JobOut,
    JobStatus,
)

router = APIRouter()

_MAX_LIST_LIMIT = 500


@router.post(
    "",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
def create_job(
    body: JobCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin)],
) -> Job:
    """Queue a new job for an agent.

    The caller picks either an explicit ``agent_id`` or a ``cluster_id``.
    For cluster routing we prefer the agent currently flagged ``primary``
    so backups land where pgBackRest expects them; if no primary is
    known we fall back to the lowest-id agent in the cluster.
    """
    if body.agent_id is None and body.cluster_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide either agent_id or cluster_id",
        )

    agent: Agent | None
    if body.agent_id is not None:
        agent = db.get(Agent, body.agent_id)
        if agent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
        if body.cluster_id is not None and agent.cluster_id != body.cluster_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "agent_id does not belong to cluster_id",
            )
    else:
        # cluster_id is set per the guard above.
        agent = db.scalar(
            select(Agent)
            .where(Agent.cluster_id == body.cluster_id)
            .order_by(
                # 'primary' sorts before 'replica'/'unknown' alphabetically;
                # add an explicit case to be safe across PG locales.
                (Agent.role == "primary").desc(),
                Agent.id.asc(),
            )
            .limit(1)
        )
        if agent is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "No agents registered in that cluster",
            )

    job = Job(
        agent_id=agent.id,
        kind=body.kind,
        params=body.params,
        status="pending",
        requested_by=user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    cluster_id: int | None = None,
    agent_id: int | None = None,
    status_filter: JobStatus | None = Query(default=None, alias="status"),
    since: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=_MAX_LIST_LIMIT),
) -> list[Job]:
    stmt = select(Job).order_by(Job.id.desc()).limit(limit)
    if cluster_id is not None:
        stmt = stmt.where(
            Job.agent_id.in_(
                select(Agent.id).where(Agent.cluster_id == cluster_id)
            )
        )
    if agent_id is not None:
        stmt = stmt.where(Job.agent_id == agent_id)
    if status_filter is not None:
        stmt = stmt.where(Job.status == status_filter)
    if since is not None:
        stmt = stmt.where(Job.created_at >= since)
    return list(db.scalars(stmt).all())


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job
