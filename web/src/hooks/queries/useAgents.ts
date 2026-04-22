import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/api/client";
import { queryKeys } from "@/api/keys";
import type { AgentRole } from "@/api/types";

/**
 * Wire shape of `GET /api/v1/agents` (mirrors `AgentOut` in
 * `manager/pct_manager/schemas.py`). Kept local to this hook because
 * the existing typed surface in `api/types.ts` only models the richer
 * `AgentDetail` returned per cluster.
 */
export interface AgentListItem {
  id: number;
  cluster_id: number;
  hostname: string;
  role: AgentRole;
  last_seen_at: string | null;
  version: string | null;
  clock_skew_ms: number | null;
  created_at: string;
}

/**
 * Flat list of every enrolled agent. Used by the Logs page to build a
 * "Node" filter dropdown so operators can isolate one member of a
 * Patroni cluster.
 */
export function useAgents() {
  return useQuery({
    queryKey: queryKeys.agents(),
    queryFn: () => apiRequest<AgentListItem[]>("/api/v1/agents"),
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  });
}
