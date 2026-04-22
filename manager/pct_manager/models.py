from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Cluster(Base):
    __tablename__ = "clusters"
    __table_args__ = {"schema": "pct"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # 'standalone' | 'patroni'
    kind: Mapped[str] = mapped_column(String, nullable=False, default="standalone")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    agents: Mapped[list["Agent"]] = relationship(back_populates="cluster")


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("cluster_id", "hostname", name="uq_agents_cluster_hostname"),
        {"schema": "pct"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("pct.clusters.id", ondelete="CASCADE"), nullable=False
    )
    hostname: Mapped[str] = mapped_column(String, nullable=False)
    # 'primary' | 'replica' | 'unknown'
    role: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[str | None] = mapped_column(String, nullable=True)
    clock_skew_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    cluster: Mapped[Cluster] = relationship(back_populates="agents")


class PgbackrestInfo(Base):
    """Snapshot of ``pgbackrest --output=json info`` for a single agent.

    Stored as raw JSONB so we can render any future pgBackRest field in the
    UI without an ORM migration. Read paths use the latest row per agent.
    """

    __tablename__ = "pgbackrest_info"
    __table_args__ = (
        Index("ix_pgbackrest_info_agent_captured", "agent_id", "captured_at"),
        {"schema": "pct"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("pct.agents.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class WalHealth(Base):
    """One sample of WAL archival health from a single agent."""

    __tablename__ = "wal_health"
    __table_args__ = (
        Index("ix_wal_health_agent_captured", "agent_id", "captured_at"),
        {"schema": "pct"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("pct.agents.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_archived_wal: Mapped[str | None] = mapped_column(String, nullable=True)
    archive_lag_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gap_detected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class PatroniState(Base):
    """Per-agent snapshot of the Patroni cluster as seen from this node.

    The agent's Patroni collector polls the local node's REST API
    (``http://<host>:8008/cluster``) and ships one row per tick. We keep
    a short history (the latest row drives the UI; older rows are useful
    for debugging leader churn alongside ``logs.role_transitions``).

    Field shape mirrors what Patroni's REST API returns; ``members`` is
    stored as raw JSONB so a Patroni version bump cannot break ingest.
    Roles in ``patroni_role`` are richer than the project-wide
    ``agents.role`` (which stays ``primary | replica | unknown`` per the
    invariant in ``00-project.mdc``).
    """

    __tablename__ = "patroni_state"
    __table_args__ = (
        Index("ix_patroni_state_agent_captured", "agent_id", "captured_at"),
        {"schema": "pct"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("pct.agents.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    member_name: Mapped[str] = mapped_column(String, nullable=False)
    # 'leader' | 'replica' | 'sync_standby' | 'standby_leader' | 'unknown'
    patroni_role: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    # Free-form Patroni state string: 'running' | 'streaming' | 'start failed' | ...
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    timeline: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Replica WAL lag in bytes (Patroni reports it directly). Null on the leader.
    lag_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Name of whichever member currently holds the leader role at capture time.
    leader_member: Mapped[str | None] = mapped_column(String, nullable=True)
    # Verbatim ``cluster.members`` array from the Patroni REST response.
    members: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )


class LogEvent(Base):
    """One normalized log record from any agent / source.

    Stored in the partitioned ``logs.events`` parent table. The composite PK
    includes ``ts_utc`` because PG requires the partition key in any unique
    constraint on a partitioned table.
    """

    __tablename__ = "events"
    __table_args__ = ({"schema": "logs"},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("pct.agents.id", ondelete="CASCADE"), nullable=False
    )
    # 'postgres' | 'pgbackrest' | 'patroni' | 'etcd' | 'os'
    source: Mapped[str] = mapped_column(String, nullable=False)
    # 'debug' | 'info' | 'warning' | 'error' | 'critical'
    severity: Mapped[str] = mapped_column(String, nullable=False)
    raw: Mapped[str] = mapped_column(Text, nullable=False)
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class RoleTransition(Base):
    """Derived from Patroni / etcd log lines on ingest. Powers the
    Cluster page's leader-history Gantt chart."""

    __tablename__ = "role_transitions"
    __table_args__ = (
        Index("ix_logs_role_transitions_ts", "ts_utc"),
        {"schema": "logs"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("pct.agents.id", ondelete="CASCADE"), nullable=False
    )
    from_role: Mapped[str | None] = mapped_column(String, nullable=True)
    to_role: Mapped[str] = mapped_column(String, nullable=False)
    # 'patroni' | 'etcd'
    source: Mapped[str] = mapped_column(String, nullable=False)


class Job(Base):
    """A unit of work the manager hands to an agent.

    Lifecycle: ``pending`` → ``running`` → (``succeeded`` | ``failed``).
    Cancellation is intentionally *not* in v1 — once an agent claims a
    job, it runs to completion. ``stdout_tail`` keeps only the last
    ~16KB of output so the manager DB doesn't bloat from chatty backups;
    the full stream lands in ``logs.events`` via the pgBackRest log
    tailer.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_agent_status", "agent_id", "status"),
        Index("ix_jobs_status_created", "status", "created_at"),
        {"schema": "pct"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("pct.agents.id", ondelete="CASCADE"), nullable=False
    )
    # Allowlisted in v1; restore/stanza-delete are blocked at both API and
    # agent layers (see PLAN §6 / docs/safety-and-rbac.md).
    kind: Mapped[str] = mapped_column(String, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # 'pending' | 'running' | 'succeeded' | 'failed'
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    requested_by: Mapped[int | None] = mapped_column(
        ForeignKey("pct.users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout_tail: Mapped[str | None] = mapped_column(Text, nullable=True)

    artifacts: Mapped[list["JobArtifact"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobArtifact.uploaded_at",
    )


class JobArtifact(Base):
    """A binary blob produced by a job (e.g. a pt-stalk diagnostic bundle).

    The file is stored on the manager filesystem under
    ``settings.artifacts_dir/<job_id>/<id>-<filename>``; this row only
    holds metadata. ``sha256`` is computed at upload time so the UI can
    surface integrity info. Cascading from ``Job`` removes the row on
    job delete; the on-disk file is best-effort cleaned by the route
    handler that triggers the delete.
    """

    __tablename__ = "job_artifacts"
    __table_args__ = (
        Index("ix_job_artifacts_job", "job_id"),
        {"schema": "pct"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("pct.jobs.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(127), nullable=False, default="application/gzip"
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped[Job] = relationship(back_populates="artifacts")


class BackupSchedule(Base):
    """Recurring backup definition fired by the manager scheduler.

    A schedule pairs a cluster with a cron expression (UTC, 5-field
    POSIX cron) and a backup ``kind``. The manager's ``schedules`` tick
    job inspects rows where ``enabled = true`` and ``next_run_at <= now()``,
    inserts a fresh ``pct.jobs`` row, and recomputes ``next_run_at`` from
    the cron expression.

    Limited by design to ``backup_full | backup_diff | backup_incr``.
    ``check`` and ``stanza_create`` stay one-off — they don't need a
    calendar (see ``docs/safety-and-rbac.md``).
    """

    __tablename__ = "backup_schedules"
    __table_args__ = (
        Index("ix_backup_schedules_cluster", "cluster_id"),
        Index("ix_backup_schedules_due", "enabled", "next_run_at"),
        {"schema": "pct"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("pct.clusters.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    # Standard 5-field cron, evaluated in UTC.
    cron_expression: Mapped[str] = mapped_column(String(128), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("pct.users.id", ondelete="SET NULL"), nullable=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ON DELETE SET NULL so purging old jobs doesn't drop the schedule row.
    last_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("pct.jobs.id", ondelete="SET NULL"), nullable=True
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Alert(Base):
    """An open or resolved alert raised by the rule engine.

    Dedup key is the tuple ``(kind, cluster_id, dedup_key)``: for any open
    alert with that key the engine merges new evaluations into ``payload``
    instead of opening a duplicate. Once the rule no longer triggers,
    ``resolved_at`` is set on the next pass and a fresh occurrence will
    open a new row. Acknowledging an alert (``acknowledged_at``) only
    silences notifications — it does NOT mark it resolved.
    """

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_kind_cluster_open", "kind", "cluster_id", "resolved_at"),
        Index("ix_alerts_opened_at", "opened_at"),
        {"schema": "pct"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 'wal_lag' | 'backup_failed' | 'clock_drift' | 'role_flapping'
    kind: Mapped[str] = mapped_column(String, nullable=False)
    # 'info' | 'warning' | 'critical'
    severity: Mapped[str] = mapped_column(String, nullable=False, default="warning")
    cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("pct.clusters.id", ondelete="CASCADE"), nullable=True
    )
    # Stable extra-key for deduplication (e.g. agent_id for per-agent alerts).
    dedup_key: Mapped[str] = mapped_column(String, nullable=False, default="")
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by: Mapped[int | None] = mapped_column(
        ForeignKey("pct.users.id", ondelete="SET NULL"), nullable=True
    )
    # Last time we sent a notification for this alert (used for re-notify
    # throttling — avoid Slack spam from a long-lived condition).
    last_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class StorageForecast(Base):
    """Latest "Storage Runway" forecast per cluster.

    Computed periodically by the scheduler with a linear regression on
    ``pgbackrest_info.payload->repo->size`` over the trailing N days
    (default 7). One row per cluster; the previous row is overwritten on
    each refresh — we don't keep history because the *trend* is what the
    operator cares about, not the prediction archive.
    """

    __tablename__ = "storage_forecast"
    __table_args__ = (
        UniqueConstraint("cluster_id", name="uq_storage_forecast_cluster"),
        {"schema": "pct"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("pct.clusters.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Number of pgbackrest_info samples used in the regression.
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # bytes/day slope. May be negative (repo shrinking after expire).
    daily_growth_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    current_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Configurable per-cluster cap; null when not set (no runway estimate).
    target_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Days until ``current`` reaches ``target`` at ``daily_growth``.
    # Null when growth <= 0 or target unset.
    days_to_target: Mapped[float | None] = mapped_column(
        Float(asdecimal=False), nullable=True
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "pct"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    # 'viewer' | 'admin'
    role: Mapped[str] = mapped_column(String, nullable=False, default="viewer")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
