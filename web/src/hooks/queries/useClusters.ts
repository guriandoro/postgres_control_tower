import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/api/client";
import { queryKeys } from "@/api/keys";
import type {
  ClusterDetail,
  ClusterSummary,
  ClusterWalHealth,
} from "@/api/types";

export function useClusters() {
  return useQuery({
    queryKey: queryKeys.clusters(),
    queryFn: () => apiRequest<ClusterSummary[]>("/api/v1/clusters"),
    staleTime: 30_000,
    refetchInterval: 5 * 60_000,
  });
}

export function useCluster(clusterId: number | undefined) {
  return useQuery({
    queryKey: clusterId ? queryKeys.cluster(clusterId) : ["clusters", "none"],
    queryFn: () =>
      apiRequest<ClusterDetail>(`/api/v1/clusters/${clusterId}`),
    enabled: clusterId !== undefined,
    staleTime: 30_000,
    refetchInterval: 5 * 60_000,
  });
}

/**
 * WAL archive lag history grouped by agent. Backs the per-cluster
 * sparkline; refetched on a shorter interval than the cluster detail
 * because a stale chart is more visually misleading than a stale tile.
 */
export function useClusterWalHealth(
  clusterId: number | undefined,
  sinceMinutes = 60,
) {
  return useQuery({
    queryKey:
      clusterId !== undefined
        ? queryKeys.clusterWalHealth(clusterId, sinceMinutes)
        : ["clusters", "none", "wal-health"],
    queryFn: () =>
      apiRequest<ClusterWalHealth>(
        `/api/v1/clusters/${clusterId}/wal_health?since_minutes=${sinceMinutes}`,
      ),
    enabled: clusterId !== undefined,
    staleTime: 15_000,
    refetchInterval: 60_000,
  });
}
