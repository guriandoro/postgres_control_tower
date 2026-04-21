import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/api/client";
import { queryKeys } from "@/api/keys";
import type { Job, JobStatus } from "@/api/types";

export interface JobFilters {
  cluster_id?: number;
  agent_id?: number;
  status?: JobStatus;
  limit?: number;
}

export function useJobs(filters: JobFilters = {}) {
  return useQuery({
    queryKey: queryKeys.jobs(filters),
    queryFn: () => apiRequest<Job[]>("/api/v1/jobs", { query: filters }),
    // Jobs UI is interactive — refresh fast enough that "running" rows
    // visibly tick toward "succeeded/failed".
    refetchInterval: 5_000,
    staleTime: 2_000,
  });
}

export function useJob(id: number | undefined) {
  return useQuery({
    queryKey: id != null ? queryKeys.job(id) : ["jobs", "none"],
    queryFn: () => apiRequest<Job>(`/api/v1/jobs/${id}`),
    enabled: id !== undefined,
    refetchInterval: (q) => {
      const data = q.state.data as Job | undefined;
      // Stop polling once the job has reached a terminal state.
      if (data && (data.status === "succeeded" || data.status === "failed")) {
        return false;
      }
      return 3_000;
    },
  });
}
