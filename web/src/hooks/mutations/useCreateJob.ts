import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "@/api/client";
import type { Job, JobCreateRequest } from "@/api/types";

export function useCreateJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: JobCreateRequest) =>
      apiRequest<Job>("/api/v1/jobs", { method: "POST", body }),
    onSuccess: () => {
      // Refresh both the list and any single-job detail subscriptions.
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
