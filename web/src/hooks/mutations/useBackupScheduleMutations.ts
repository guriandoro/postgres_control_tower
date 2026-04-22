import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "@/api/client";
import { queryKeys } from "@/api/keys";
import type {
  BackupSchedule,
  BackupScheduleCreateRequest,
  BackupScheduleUpdateRequest,
} from "@/api/types";

export function useCreateBackupSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BackupScheduleCreateRequest) =>
      apiRequest<BackupSchedule>("/api/v1/schedules", {
        method: "POST",
        body,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.backupSchedules() });
    },
  });
}

export function useUpdateBackupSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: number;
      patch: BackupScheduleUpdateRequest;
    }) =>
      apiRequest<BackupSchedule>(`/api/v1/schedules/${id}`, {
        method: "PATCH",
        body: patch,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.backupSchedules() });
    },
  });
}

export function useDeleteBackupSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      apiRequest<void>(`/api/v1/schedules/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.backupSchedules() });
    },
  });
}
