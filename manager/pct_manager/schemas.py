from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

ClusterKind = Literal["standalone", "patroni"]
AgentRole = Literal["primary", "replica", "unknown"]
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


class AgentDetail(AgentOut):
    """Per-agent block embedded in the cluster detail view."""

    latest_wal_health: WalHealthOut | None = None
    latest_pgbackrest_info: PgbackrestInfoOut | None = None


class ClusterSummary(ClusterOut):
    """List view: cluster + counts + freshness, no large payloads."""

    agent_count: int
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
]
JOB_KINDS: tuple[JobKind, ...] = (
    "backup_full",
    "backup_diff",
    "backup_incr",
    "check",
    "stanza_create",
)
JobStatus = Literal["pending", "running", "succeeded", "failed"]


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
