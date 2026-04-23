from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

ClusterKind = Literal["standalone", "patroni"]
AgentRole = Literal["primary", "replica", "unknown"]
# Patroni's own role taxonomy is richer than the project-wide AgentRole.
# We keep AgentRole as the canonical "primary | replica | unknown" used by
# alerting/older UI, and add PatroniRole for the new patroni_state ingest.
PatroniRole = Literal[
    "leader", "replica", "sync_standby", "standby_leader", "unknown"
]
UserRole = Literal["viewer", "admin"]
LogSource = Literal["postgres", "pgbackrest", "patroni", "etcd", "os"]
LogSeverity = Literal["debug", "info", "warning", "error", "critical"]


# ---------- Auth ----------


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: UserRole
    created_at: datetime


# ---------- Clusters ----------


class ClusterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: ClusterKind
    created_at: datetime


# ---------- Agents ----------


class AgentRegisterRequest(BaseModel):
    """Sent by an agent on first start. Manager validates ``enrollment_token``."""

    enrollment_token: str
    cluster_name: str = Field(min_length=1, max_length=128)
    cluster_kind: ClusterKind = "standalone"
    hostname: str = Field(min_length=1, max_length=253)


class AgentRegisterResponse(BaseModel):
    agent_id: int
    agent_token: str  # raw token, returned ONCE; never re-retrievable
    cluster_id: int


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int
    hostname: str
    role: AgentRole
    last_seen_at: datetime | None
    version: str | None
    clock_skew_ms: int | None
    created_at: datetime


class AgentHeartbeatRequest(BaseModel):
    """Posted periodically by every agent.

    ``agent_time_utc`` is the agent's wall clock at the moment the request was
    built; the manager subtracts its own UTC ``now()`` from it to derive the
    one-way clock skew (positive => agent clock is behind manager).
    """

    agent_time_utc: datetime
    version: str = Field(min_length=1, max_length=64)
    role: AgentRole = "unknown"


class AgentHeartbeatResponse(BaseModel):
    """Returned to the agent so it can self-report drift in the next cycle."""

    server_time_utc: datetime
    clock_skew_ms: int


# ---------- Agent ingest: pgBackRest + WAL ----------


class PgbackrestInfoIngest(BaseModel):
    """Payload posted by ``collectors/pgbackrest.py``.

    ``payload`` is the verbatim output of ``pgbackrest --output=json info``
    (a JSON array of stanzas). We don't validate its inner shape here so a
    pgBackRest version bump cannot break ingestion.
    """

    captured_at: datetime
    payload: Any


class WalHealthIngest(BaseModel):
    """Payload posted by ``collectors/wal.py`` after probing Postgres."""

    captured_at: datetime
    last_archived_wal: str | None = None
    archive_lag_seconds: int | None = None
    gap_detected: bool = False
    role: AgentRole = "unknown"


class PatroniMember(BaseModel):
    """One entry from Patroni's ``cluster.members`` array.

    All fields optional because the shape varies across Patroni versions
    and node states (e.g. a stopped replica may omit ``lag``). The agent
    posts the verbatim payload; we only enforce the keys the UI cares about.
    """

    name: str | None = None
    role: str | None = None
    state: str | None = None
    host: str | None = None
    port: int | None = None
    timeline: int | None = None
    # Patroni reports lag in bytes for replicas (since 3.x). Older versions
    # may be missing it entirely; the UI handles ``null`` gracefully.
    lag: int | None = None


class PatroniStateIngest(BaseModel):
    """Posted by ``collectors/patroni.py`` after polling ``/cluster``."""

    captured_at: datetime
    member_name: str = Field(min_length=1, max_length=253)
    patroni_role: PatroniRole = "unknown"
    state: str | None = Field(default=None, max_length=64)
    timeline: int | None = None
    lag_bytes: int | None = None
    leader_member: str | None = Field(default=None, max_length=253)
    members: list[PatroniMember] = Field(default_factory=list)


class IngestAck(BaseModel):
    ok: Literal[True] = True
    id: int


# ---------- Cluster read views ----------


class WalHealthOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    captured_at: datetime
    last_archived_wal: str | None
    archive_lag_seconds: int | None
    gap_detected: bool


class PgbackrestInfoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    captured_at: datetime
    payload: Any


class PatroniStateOut(BaseModel):
    """Latest Patroni snapshot a given agent shipped, surfaced in the UI."""

    model_config = ConfigDict(from_attributes=True)

    captured_at: datetime
    member_name: str
    patroni_role: PatroniRole
    state: str | None
    timeline: int | None
    lag_bytes: int | None
    leader_member: str | None
    members: list[PatroniMember]


class AgentDetail(AgentOut):
    """Per-agent block embedded in the cluster detail view."""

    latest_wal_health: WalHealthOut | None = None
    latest_pgbackrest_info: PgbackrestInfoOut | None = None
    latest_patroni_state: PatroniStateOut | None = None


class ClusterSummary(ClusterOut):
    """List view: cluster + counts + freshness, no large payloads."""

    agent_count: int
    # Agents whose ``last_seen_at`` falls inside the manager's freshness
    # window (see ``ONLINE_FRESH_SECONDS`` in ``routes/clusters.py``).
    # The fleet dashboard sums this across clusters so the "Agents online"
    # tile drops when an agent stops heartbeating.
    agents_online: int = 0
    last_seen_at: datetime | None = None


class ClusterDetail(ClusterOut):
    """Full per-cluster view with embedded agent state."""

    agents: list[AgentDetail]


class WalHealthSeries(BaseModel):
    """Per-agent timeseries of WAL archival samples for the WAL sparkline."""

    agent_id: int
    hostname: str
    role: AgentRole
    samples: list[WalHealthOut]


class ClusterWalHealth(BaseModel):
    """Cluster-wide WAL archival history grouped by agent.

    Each agent gets its own series so the UI can draw one line per node
    instead of collapsing primary + replicas into a single sparkline.
    """

    cluster_id: int
    since_minutes: int
    series: list[WalHealthSeries]


# ---------- Log ingestion + query ----------


class LogRecordIn(BaseModel):
    """One normalized log record from an agent collector.

    ``parsed`` is free-form JSON; reserved keys the manager looks at:
    - ``role_transition``: ``{"from": "<role|null>", "to": "<role>"}`` —
      promoted to the ``logs.role_transitions`` table on ingest.
    - ``message``: human-readable message extracted from ``raw``.
    """

    ts_utc: datetime
    source: LogSource
    severity: LogSeverity = "info"
    raw: str = Field(min_length=1, max_length=64_000)
    parsed: dict[str, Any] | None = None


class LogBatchIngest(BaseModel):
    records: list[LogRecordIn] = Field(default_factory=list, max_length=5_000)


class LogIngestAck(BaseModel):
    ok: Literal[True] = True
    accepted: int
    role_transitions: int = 0


class LogEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts_utc: datetime
    agent_id: int
    # Denormalized agent identity so the UI can render a "Node" column
    # without a per-row /agents lookup. Populated via a join in the
    # /logs/events route; nullable for safety if the agent row is gone.
    hostname: str | None = None
    cluster_id: int | None = None
    node_role: AgentRole = "unknown"
    source: LogSource
    severity: LogSeverity
    raw: str
    parsed: dict[str, Any] | None


class RoleTransitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts_utc: datetime
    agent_id: int
    from_role: str | None
    to_role: str
    source: str


# ---------- Safe Ops: jobs ----------

# v1 allowlist enforced in BOTH the API and the agent runner. If you add
# a kind here, mirror it in `agent/pct_agent/runner.py` and update
# `docs/safety-and-rbac.md`. `restore` and `stanza_delete` are
# deliberately absent and must stay that way until v2.
JobKind = Literal[
    "backup_full",
    "backup_diff",
    "backup_incr",
    "check",
    "stanza_create",
    # Read-only diagnostic snapshot via pt-stalk's PostgreSQL collect mode.
    # The agent runs `pt-stalk --pgsql --no-stalk --collect` against its
    # local Postgres, tar-gzips the resulting bundle and uploads it as a
    # job artifact. Never writes to the DB.
    "pt_stalk_collect",
]
JOB_KINDS: tuple[JobKind, ...] = (
    "backup_full",
    "backup_diff",
    "backup_incr",
    "check",
    "stanza_create",
    "pt_stalk_collect",
)
JobStatus = Literal["pending", "running", "succeeded", "failed"]


class JobArtifactOut(BaseModel):
    """A binary blob produced by a job (e.g. a pt-stalk bundle).

    The file lives on the manager filesystem under ``settings.artifacts_dir``;
    only metadata is stored in ``pct.job_artifacts``. The UI fetches the
    bytes via ``GET /api/v1/jobs/{job_id}/artifacts/{id}/download``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_at: datetime


class JobCreateRequest(BaseModel):
    """Operator submits a new job from the UI.

    Either ``agent_id`` or ``cluster_id`` is required. When only
    ``cluster_id`` is given the manager will route to the cluster's
    primary if it knows one, otherwise the lowest-id agent.
    """

    kind: JobKind
    agent_id: int | None = None
    cluster_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    kind: JobKind
    params: dict[str, Any]
    status: JobStatus
    requested_by: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    stdout_tail: str | None
    # Empty for the historical pgBackRest job kinds; populated for jobs
    # like ``pt_stalk_collect`` that produce a downloadable bundle.
    artifacts: list[JobArtifactOut] = Field(default_factory=list)


# Schedules only fire backups. ``check`` and ``stanza_create`` stay one-off
# (they don't need a calendar — see docs/safety-and-rbac.md). The agent
# runner allowlist is a strict superset, so any kind we add here is
# always agent-executable.
BackupScheduleKind = Literal[
    "backup_full",
    "backup_diff",
    "backup_incr",
]
BACKUP_SCHEDULE_KINDS: tuple[BackupScheduleKind, ...] = (
    "backup_full",
    "backup_diff",
    "backup_incr",
)


class BackupScheduleCreateRequest(BaseModel):
    """Operator submits a recurring backup from the UI.

    ``cron_expression`` is a 5-field POSIX cron (min hour dom mon dow)
    evaluated in UTC. The route validates it via APScheduler's
    ``CronTrigger.from_crontab`` before persisting.
    """

    cluster_id: int
    kind: BackupScheduleKind
    cron_expression: str = Field(min_length=1, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class BackupScheduleUpdateRequest(BaseModel):
    """All fields optional — caller sends only what changed.

    Edit semantics: changing ``cron_expression`` recomputes
    ``next_run_at`` from "now". Toggling ``enabled`` from false → true
    also recomputes; false → true on a stale ``next_run_at`` would
    otherwise stampede the next tick.
    """

    cron_expression: str | None = Field(default=None, min_length=1, max_length=128)
    params: dict[str, Any] | None = None
    enabled: bool | None = None
    kind: BackupScheduleKind | None = None


class BackupScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int
    kind: BackupScheduleKind
    cron_expression: str
    params: dict[str, Any]
    enabled: bool
    created_at: datetime
    created_by: int | None
    last_run_at: datetime | None
    last_job_id: int | None
    next_run_at: datetime | None


class JobClaim(BaseModel):
    """Returned by the agent long-poll endpoint when work is available."""

    id: int
    kind: JobKind
    params: dict[str, Any]


class JobResultRequest(BaseModel):
    """Posted by the agent runner after the subprocess finishes (or
    immediately on a refusal, e.g. unknown kind / blocked op)."""

    exit_code: int
    stdout_tail: str | None = Field(default=None, max_length=16_000)
    succeeded: bool


# ---------- Alerting (P7) ----------

# Mirror of the rule kinds emitted by ``alerter.rules``. Adding a new
# rule means adding it here so the API can advertise the canonical set.
AlertKind = Literal[
    "wal_lag",
    "backup_failed",
    "clock_drift",
    "role_flapping",
]
AlertSeverity = Literal["info", "warning", "critical"]


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: AlertKind
    severity: AlertSeverity
    cluster_id: int | None
    dedup_key: str
    opened_at: datetime
    resolved_at: datetime | None
    acknowledged_at: datetime | None
    acknowledged_by: int | None
    last_notified_at: datetime | None
    payload: dict[str, Any]


class AlertAckResponse(BaseModel):
    id: int
    acknowledged_at: datetime


# ---------- Storage runway forecast ----------


class StorageForecastOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cluster_id: int
    captured_at: datetime
    sample_count: int
    daily_growth_bytes: int
    current_bytes: int
    target_bytes: int | None
    days_to_target: float | None
