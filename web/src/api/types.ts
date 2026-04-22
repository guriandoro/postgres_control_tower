/**
 * Wire types for the manager's `/api/v1/*` endpoints.
 *
 * Kept hand-typed (rather than generated from OpenAPI) so it stays small
 * and the v1 surface is easy to skim. Field names mirror the Pydantic
 * schemas in `manager/pct_manager/schemas.py`.
 */

export type ClusterKind = "standalone" | "patroni";
export type AgentRole = "primary" | "replica" | "unknown";
/**
 * Patroni's own role taxonomy. Richer than {@link AgentRole}; surfaced
 * verbatim by the `/api/v1/agents/patroni_state` ingest so the dashboard
 * can distinguish a synchronous standby from an async one.
 */
export type PatroniRole =
  | "leader"
  | "replica"
  | "sync_standby"
  | "standby_leader"
  | "unknown";
export type LogSource = "postgres" | "pgbackrest" | "patroni" | "etcd" | "os";
export type LogSeverity = "debug" | "info" | "warning" | "error" | "critical";

export interface UserOut {
  id: number;
  email: string;
  role: "viewer" | "admin";
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
}

export interface ClusterSummary {
  id: number;
  name: string;
  kind: ClusterKind;
  created_at: string;
  agent_count: number;
  last_seen_at: string | null;
}

export interface WalHealth {
  captured_at: string;
  last_archived_wal: string | null;
  archive_lag_seconds: number | null;
  gap_detected: boolean;
}

export interface PgbackrestInfo {
  captured_at: string;
  /** Raw output of `pgbackrest --output=json info`. Loosely typed on purpose. */
  payload: unknown;
}

/** One entry from Patroni's `cluster.members` array. */
export interface PatroniMember {
  name?: string;
  role?: string;
  state?: string;
  host?: string;
  port?: number;
  timeline?: number;
  /** Replica WAL apply lag in bytes (Patroni 3.x). Absent on the leader. */
  lag?: number;
}

export interface PatroniState {
  captured_at: string;
  member_name: string;
  patroni_role: PatroniRole;
  state: string | null;
  timeline: number | null;
  /** This member's WAL apply lag in bytes. Null for the leader. */
  lag_bytes: number | null;
  /** Whichever member held the leader lock at capture time. */
  leader_member: string | null;
  members: PatroniMember[];
}

export interface AgentDetail {
  id: number;
  cluster_id: number;
  hostname: string;
  role: AgentRole;
  last_seen_at: string | null;
  version: string | null;
  clock_skew_ms: number | null;
  created_at: string;
  latest_wal_health: WalHealth | null;
  latest_pgbackrest_info: PgbackrestInfo | null;
  /** Present only when the cluster runs Patroni and the agent has shipped
   *  at least one snapshot. */
  latest_patroni_state: PatroniState | null;
}

export interface ClusterDetail {
  id: number;
  name: string;
  kind: ClusterKind;
  created_at: string;
  agents: AgentDetail[];
}

export interface WalHealthSeries {
  agent_id: number;
  hostname: string;
  role: AgentRole;
  samples: WalHealth[];
}

export interface ClusterWalHealth {
  cluster_id: number;
  since_minutes: number;
  series: WalHealthSeries[];
}

export interface LogEvent {
  id: number;
  ts_utc: string;
  agent_id: number;
  /** Denormalized agent identity. Null if the originating agent row is gone. */
  hostname: string | null;
  cluster_id: number | null;
  /** Last-known role of the agent at query time, NOT at log emission time. */
  node_role: AgentRole;
  source: LogSource;
  severity: LogSeverity;
  raw: string;
  parsed: Record<string, unknown> | null;
}

export interface RoleTransition {
  id: number;
  ts_utc: string;
  agent_id: number;
  from_role: string | null;
  to_role: string;
  source: string;
}

// ---------- Alerts (P7) ----------

export type AlertKind =
  | "wal_lag"
  | "backup_failed"
  | "clock_drift"
  | "role_flapping";

export type AlertSeverity = "info" | "warning" | "critical";

export interface Alert {
  id: number;
  kind: AlertKind;
  severity: AlertSeverity;
  cluster_id: number | null;
  dedup_key: string;
  opened_at: string;
  resolved_at: string | null;
  acknowledged_at: string | null;
  acknowledged_by: number | null;
  last_notified_at: string | null;
  payload: Record<string, unknown>;
}

export interface AlertSummary {
  open_total: number;
  open_acknowledged: number;
  by_severity: Partial<Record<AlertSeverity, number>>;
}

export interface StorageForecast {
  cluster_id: number;
  captured_at: string;
  sample_count: number;
  daily_growth_bytes: number;
  current_bytes: number;
  target_bytes: number | null;
  days_to_target: number | null;
}

// ---------- Safe Ops: jobs ----------

export type JobKind =
  | "backup_full"
  | "backup_diff"
  | "backup_incr"
  | "check"
  | "stanza_create";

export const JOB_KINDS: readonly JobKind[] = [
  "backup_full",
  "backup_diff",
  "backup_incr",
  "check",
  "stanza_create",
] as const;

export type JobStatus = "pending" | "running" | "succeeded" | "failed";

export interface Job {
  id: number;
  agent_id: number;
  kind: JobKind;
  params: Record<string, unknown>;
  status: JobStatus;
  requested_by: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  stdout_tail: string | null;
}

export interface JobCreateRequest {
  kind: JobKind;
  agent_id?: number;
  cluster_id?: number;
  params?: Record<string, unknown>;
}

// ---------- Safe Ops: backup schedules ----------

/** Schedules only fire backups; ``check`` and ``stanza_create`` stay one-off. */
export type BackupScheduleKind = "backup_full" | "backup_diff" | "backup_incr";

export const BACKUP_SCHEDULE_KINDS: readonly BackupScheduleKind[] = [
  "backup_full",
  "backup_diff",
  "backup_incr",
] as const;

export interface BackupSchedule {
  id: number;
  cluster_id: number;
  kind: BackupScheduleKind;
  /** 5-field POSIX cron evaluated in UTC by the manager. */
  cron_expression: string;
  params: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
  created_by: number | null;
  last_run_at: string | null;
  last_job_id: number | null;
  next_run_at: string | null;
}

export interface BackupScheduleCreateRequest {
  cluster_id: number;
  kind: BackupScheduleKind;
  cron_expression: string;
  params?: Record<string, unknown>;
  enabled?: boolean;
}

export interface BackupScheduleUpdateRequest {
  cron_expression?: string;
  params?: Record<string, unknown>;
  enabled?: boolean;
  kind?: BackupScheduleKind;
}

/** Subset of the pgBackRest JSON we actually render — see PLAN §6 retention timeline. */
export interface PgbrStanza {
  name: string;
  status?: { code?: number; message?: string };
  backup?: PgbrBackup[];
  archive?: { id?: string; min?: string; max?: string }[];
  repo?: PgbrRepo[];
}

export interface PgbrBackup {
  label: string;
  type: "full" | "diff" | "incr" | string;
  timestamp: { start: number; stop: number };
  info?: {
    size?: number;
    delta?: number;
    repository?: { size?: number; delta?: number };
  };
  archive?: { start?: string; stop?: string };
  error?: boolean;
}

export interface PgbrRepo {
  key?: number;
  cipher?: string;
  status?: { code?: number; message?: string };
  // Note: pgBackRest does NOT put a size here. Per-backup footprint is
  // PgbrBackup.info.repository.{size,delta}. Don't add `size?: number`
  // — it'll always be undefined and silently zero out aggregations.
}
