import { useQuery } from "@tanstack/react-query";
import { apiRequest, getStoredToken } from "@/api/client";
import { queryKeys } from "@/api/keys";
import type { JobArtifact } from "@/api/types";

/**
 * Lists every artifact (downloadable bundle) attached to a job. Used
 * by the Jobs detail panel to render a download list for diagnostic
 * jobs like ``pt_stalk_collect``. We only poll while the parent job is
 * still running because artifacts are never re-uploaded after a job
 * reaches a terminal state.
 */
export function useJobArtifacts(
  jobId: number | undefined,
  options: { isTerminal?: boolean } = {},
) {
  const { isTerminal = false } = options;
  return useQuery({
    queryKey:
      jobId != null ? queryKeys.jobArtifacts(jobId) : ["jobs", "none", "artifacts"],
    queryFn: () =>
      apiRequest<JobArtifact[]>(`/api/v1/jobs/${jobId}/artifacts`),
    enabled: jobId !== undefined,
    refetchInterval: isTerminal ? false : 5_000,
    staleTime: 2_000,
  });
}

/**
 * Triggers a browser download for an artifact. We can't put the JWT in
 * a query string for the manager's `FileResponse`, and an `<a download>`
 * tag would lose our `Authorization` header — so we fetch the bytes,
 * stuff them into a blob URL, and click a temporary anchor.
 */
export async function downloadJobArtifact(
  artifact: JobArtifact,
): Promise<void> {
  const token = getStoredToken();
  const response = await fetch(
    `/api/v1/jobs/${artifact.job_id}/artifacts/${artifact.id}/download`,
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    },
  );
  if (!response.ok) {
    throw new Error(`Download failed: HTTP ${response.status}`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = artifact.filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    // Defer revocation so the browser has time to start the download.
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
  }
}
