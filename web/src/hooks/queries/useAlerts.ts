import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/api/client";
import { queryKeys } from "@/api/keys";
import type { Alert, AlertKind, AlertSummary, StorageForecast } from "@/api/types";

export interface AlertFilters {
  status?: "open" | "resolved" | "acknowledged" | "all";
  kind?: AlertKind;
  cluster_id?: number;
  limit?: number;
}

export function useAlerts(filters: AlertFilters = {}) {
  return useQuery({
    queryKey: queryKeys.alerts(filters),
    queryFn: () => apiRequest<Alert[]>("/api/v1/alerts", { query: filters }),
    refetchInterval: 15_000,
    staleTime: 5_000,
  });
}

export function useAlertsSummary() {
  return useQuery({
    queryKey: queryKeys.alertsSummary(),
    queryFn: () => apiRequest<AlertSummary>("/api/v1/alerts/summary"),
    refetchInterval: 15_000,
    staleTime: 5_000,
  });
}

export function useStorageForecast(clusterId: number | undefined) {
  return useQuery({
    queryKey: clusterId != null
      ? queryKeys.storageForecast(clusterId)
      : ["forecast", "storage", "none"],
    queryFn: () =>
      apiRequest<StorageForecast | null>(
        `/api/v1/clusters/${clusterId}/storage_forecast`,
      ),
    enabled: clusterId !== undefined,
    refetchInterval: 60_000,
  });
}
