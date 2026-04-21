/**
 * Centralized TanStack Query key factories. Adding a new query?
 * Define its key here so we get one source of truth for invalidation.
 */
export const queryKeys = {
  me: () => ["me"] as const,
  clusters: () => ["clusters"] as const,
  cluster: (id: number) => ["clusters", id] as const,
  logs: (filters: Record<string, unknown>) => ["logs", filters] as const,
  roleTransitions: (filters: Record<string, unknown>) =>
    ["role-transitions", filters] as const,
  jobs: (filters: Record<string, unknown>) => ["jobs", filters] as const,
  job: (id: number) => ["jobs", id] as const,
  alerts: (filters: Record<string, unknown>) => ["alerts", filters] as const,
  alertsSummary: () => ["alerts", "summary"] as const,
  storageForecast: (clusterId: number) =>
    ["forecast", "storage", clusterId] as const,
};
