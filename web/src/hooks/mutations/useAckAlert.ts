import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "@/api/client";

export function useAckAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (alertId: number) =>
      apiRequest<{ id: number; acknowledged_at: string }>(
        `/api/v1/alerts/${alertId}/ack`,
        { method: "POST" },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}
