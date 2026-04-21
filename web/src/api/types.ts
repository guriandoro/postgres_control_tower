/**
 * Wire types for the manager's `/api/v1/*` endpoints.
 *
 * Kept hand-typed (rather than generated from OpenAPI) so it stays small
 * and the v1 surface is easy to skim. Field names mirror the Pydantic
 * schemas in `manager/pct_manager/schemas.py`.
 */

export type ClusterKind = "standalone" | "patroni";
export type AgentRole = "primary" | "replica" | "unknown";
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
}

export interface ClusterDetail {
  id: number;
  name: string;
  kind: ClusterKind;
  created_at: string;
  agents: AgentDetail[];
}

export interface LogEvent {
  id: number;
  ts_utc: string;
  agent_id: number;
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
