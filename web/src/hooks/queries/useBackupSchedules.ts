import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/api/client";
import { queryKeys } from "@/api/keys";
import type { BackupSchedule } from "@/api/types";

export function useBackupSchedules() {
  return useQuery({
    queryKey: queryKeys.backupSchedules(),
    queryFn: () => apiRequest<BackupSchedule[]>("/api/v1/schedules"),
    staleTime: 30_000,
    // Refresh often enough that the "next run" column ticks in real time
    // without hammering the manager — schedules don't change frequently.
    refetchInterval: 30_000,
  });
}
