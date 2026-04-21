import asyncio
import hmac
import logging
import time
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import (
    generate_agent_token,
    get_current_agent,
    get_current_user,
    hash_agent_token,
)
from ..config import settings
from ..db import get_db
from ..models import Agent, Cluster, Job, PgbackrestInfo, User, WalHealth
from ..schemas import (
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    AgentOut,
    AgentRegisterRequest,
    AgentRegisterResponse,
    IngestAck,
    JobClaim,
    JobResultRequest,
    PgbackrestInfoIngest,
    WalHealthIngest,
)

log = logging.getLogger("pct_manager.routes.agents")

router = APIRouter()


@router.post(
    "/register",
    response_model=AgentRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_agent(
    payload: AgentRegisterRequest,
    db: Annotated[Session, Depends(get_db)],
) -> AgentRegisterResponse:
    """Enroll a new agent. Returns a one-time agent bearer token.

    Idempotency: if an agent with the same (cluster, hostname) already exists,
    a NEW token is issued and the existing token_hash is replaced. This is
    intentional so a re-installed agent can re-register without manual cleanup.
    """
    if not hmac.compare_digest(payload.enrollment_token, settings.enrollment_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid enrollment token",
        )

    cluster = db.scalar(select(Cluster).where(Cluster.name == payload.cluster_name))
    if cluster is None:
        cluster = Cluster(name=payload.cluster_name, kind=payload.cluster_kind)
        db.add(cluster)
        db.flush()  # populate cluster.id

    agent = db.scalar(
        select(Agent).where(
            Agent.cluster_id == cluster.id,
            Agent.hostname == payload.hostname,
        )
    )

    raw_token = generate_agent_token()
    token_hash = hash_agent_token(raw_token)

    if agent is None:
        agent = Agent(
            cluster_id=cluster.id,
            hostname=payload.hostname,
            token_hash=token_hash,
        )
        db.add(agent)
    else:
        agent.token_hash = token_hash

    db.commit()
    db.refresh(agent)

    return AgentRegisterResponse(
        agent_id=agent.id,
        agent_token=raw_token,
        cluster_id=cluster.id,
    )


@router.get("", response_model=list[AgentOut])
def list_agents(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
) -> list[Agent]:
    return list(db.scalars(select(Agent).order_by(Agent.id)).all())


@router.post("/heartbeat", response_model=AgentHeartbeatResponse)
def heartbeat(
    payload: AgentHeartbeatRequest,
    db: Annotated[Session, Depends(get_db)],
    agent: Annotated[Agent, Depends(get_current_agent)],
) -> AgentHeartbeatResponse:
    """Record an agent's liveness ping.

    Updates ``last_seen_at`` to the manager's UTC clock (authoritative for
    "alive" decisions) and computes a one-way clock skew from the agent's
    self-reported ``agent_time_utc``. Skew is approximate: it does not
    account for network RTT, but it's enough to flag clocks that have drifted
    by seconds (the threshold the alerting rules care about, per PLAN §6).
    """
    server_now = datetime.now(timezone.utc)

    agent_ts = payload.agent_time_utc
    if agent_ts.tzinfo is None:
        agent_ts = agent_ts.replace(tzinfo=timezone.utc)
    skew_ms = int((server_now - agent_ts).total_seconds() * 1000)

    agent.last_seen_at = server_now
    agent.version = payload.version
    agent.role = payload.role
    agent.clock_skew_ms = skew_ms
    db.commit()

    return AgentHeartbeatResponse(server_time_utc=server_now, clock_skew_ms=skew_ms)


@router.post(
    "/pgbackrest_info",
    response_model=IngestAck,
    status_code=status.HTTP_201_CREATED,
)
def ingest_pgbackrest_info(
    payload: PgbackrestInfoIngest,
    db: Annotated[Session, Depends(get_db)],
    agent: Annotated[Agent, Depends(get_current_agent)],
) -> IngestAck:
    """Store a snapshot of ``pgbackrest --output=json info`` from the agent."""
    row = PgbackrestInfo(
        agent_id=agent.id,
        captured_at=payload.captured_at,
        payload=payload.payload,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return IngestAck(id=row.id)


@router.post(
    "/wal_health",
    response_model=IngestAck,
    status_code=status.HTTP_201_CREATED,
)
def ingest_wal_health(
    payload: WalHealthIngest,
    db: Annotated[Session, Depends(get_db)],
    agent: Annotated[Agent, Depends(get_current_agent)],
) -> IngestAck:
    """Store one WAL-archival sample and update the agent's role."""
    row = WalHealth(
        agent_id=agent.id,
        captured_at=payload.captured_at,
        last_archived_wal=payload.last_archived_wal,
        archive_lag_seconds=payload.archive_lag_seconds,
        gap_detected=payload.gap_detected,
    )
    db.add(row)
    # Role is volatile — keep it on the agent row so the UI doesn't have to
    # join wal_health every time it renders the fleet grid.
    agent.role = payload.role
    db.commit()
    db.refresh(row)
    return IngestAck(id=row.id)


# ---------- Safe Ops: agent job claim + result ----------
#
# The agent long-polls ``/jobs/next`` so backups feel "instant" without
# requiring inbound connectivity to the DB host. To keep the manager
# responsive we cap each poll at ``_LONG_POLL_SECONDS`` and return
# 204 No Content when nothing is available; the agent immediately polls
# again. ``SELECT ... FOR UPDATE SKIP LOCKED`` makes this safe even if
# two manager workers race on the same pending row.

_LONG_POLL_SECONDS = 25
_LONG_POLL_TICK = 1.0


@router.get(
    "/jobs/next",
    response_model=None,
    responses={
        200: {"model": JobClaim, "description": "A job was claimed."},
        204: {"description": "No work available within the long-poll window."},
    },
)
async def claim_next_job(
    db: Annotated[Session, Depends(get_db)],
    agent: Annotated[Agent, Depends(get_current_agent)],
    wait: int = Query(
        default=_LONG_POLL_SECONDS,
        ge=0,
        le=_LONG_POLL_SECONDS,
        description="Seconds to long-poll for a pending job (capped server-side).",
    ),
) -> Response:
    """Atomically claim the oldest pending job for this agent."""
    deadline = time.monotonic() + min(wait, _LONG_POLL_SECONDS)
    while True:
        claimed = _try_claim_one(db, agent.id)
        if claimed is not None:
            body = JobClaim(
                id=claimed.id,
                kind=claimed.kind,  # type: ignore[arg-type]
                params=claimed.params,
            )
            return Response(
                content=body.model_dump_json(),
                media_type="application/json",
            )
        if time.monotonic() >= deadline:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        # Yield to the event loop; another connection may insert work.
        await asyncio.sleep(_LONG_POLL_TICK)


def _try_claim_one(db: Session, agent_id: int) -> Job | None:
    """One transactional attempt at claiming a pending job for the agent.

    Uses ``FOR UPDATE SKIP LOCKED`` so concurrent workers never hand the
    same row to two agents. Returns the updated Job or None.
    """
    stmt = (
        select(Job)
        .where(Job.agent_id == agent_id, Job.status == "pending")
        .order_by(Job.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = db.scalar(stmt)
    if job is None:
        # Release the implicit transaction so the next poll sees fresh data.
        db.rollback()
        return None
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job


@router.post("/jobs/{job_id}/result", response_model=IngestAck)
def submit_job_result(
    job_id: int,
    payload: JobResultRequest,
    db: Annotated[Session, Depends(get_db)],
    agent: Annotated[Agent, Depends(get_current_agent)],
) -> IngestAck:
    """Agent reports the outcome of a previously-claimed job.

    The manager only accepts a result from the agent that owns the job
    and only while the job is in ``running``. Late or duplicate results
    return 409 so the agent runner can log + drop them.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.agent_id != agent.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Job belongs to another agent")
    if job.status != "running":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Job is in status {job.status!r}; cannot accept result",
        )

    job.status = "succeeded" if payload.succeeded else "failed"
    job.exit_code = payload.exit_code
    job.stdout_tail = payload.stdout_tail
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
    log.info(
        "Job %d (kind=%s) finished: status=%s exit=%s",
        job.id,
        job.kind,
        job.status,
        job.exit_code,
    )
    return IngestAck(id=job.id)
