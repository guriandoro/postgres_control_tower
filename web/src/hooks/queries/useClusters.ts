import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/api/client";
import { queryKeys } from "@/api/keys";
import type { ClusterDetail, ClusterSummary } from "@/api/types";

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
