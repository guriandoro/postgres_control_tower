/**
 * Centralized TanStack Query key factories. Adding a new query?
 * Define its key here so we get one source of truth for invalidation.
 */
export const queryKeys = {
  me: () => ["me"] as const,
  clusters: () => ["clusters"] as const,
  cluster: (id: number) => ["clusters", id] as const,
  logs: <T extends object>(filters: T) => ["logs", filters] as const,
  roleTransitions: <T extends object>(filters: T) =>
    ["role-transitions", filters] as const,
  jobs: <T extends object>(filters: T) => ["jobs", filters] as const,
  job: (id: number) => ["jobs", id] as const,
  backupSchedules: () => ["backup-schedules"] as const,
  alerts: <T extends object>(filters: T) => ["alerts", filters] as const,
  alertsSummary: () => ["alerts", "summary"] as const,
  storageForecast: (clusterId: number) =>
    ["forecast", "storage", clusterId] as const,
  clusterWalHealth: (clusterId: number, sinceMinutes: number) =>
    ["clusters", clusterId, "wal-health", sinceMinutes] as const,
};
