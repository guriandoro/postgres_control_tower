import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/api/client";
import { queryKeys } from "@/api/keys";
import type { LogEvent, LogSeverity, LogSource, RoleTransition } from "@/api/types";

export interface LogFilters {
  cluster_id?: number;
  agent_id?: number;
  source?: LogSource;
  severity?: LogSeverity;
  q?: string;
  since?: string;
  until?: string;
  limit?: number;
}

export function useLogs(filters: LogFilters) {
  return useQuery({
    queryKey: queryKeys.logs(filters),
    queryFn: () =>
      apiRequest<LogEvent[]>("/api/v1/logs/events", { query: filters }),
    // Auto-refresh per PLAN §6: every 5 minutes; "Instant Snap" forces refetch.
    staleTime: 30_000,
    refetchInterval: 5 * 60_000,
  });
}

export function useRoleTransitions(filters: {
  cluster_id?: number;
  agent_id?: number;
  since?: string;
  until?: string;
  limit?: number;
}) {
  return useQuery({
    queryKey: queryKeys.roleTransitions(filters),
    queryFn: () =>
      apiRequest<RoleTransition[]>("/api/v1/logs/role_transitions", {
        query: filters,
      }),
    staleTime: 30_000,
    refetchInterval: 5 * 60_000,
  });
}
