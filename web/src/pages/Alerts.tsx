import { Fragment, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  BellOff,
  CheckCircle2,
  Clock,
  Filter,
  RefreshCw,
} from "lucide-react";
import { ApiError } from "@/api/client";
import type { Alert, AlertKind, AlertSeverity } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { Spinner } from "@/components/ui/Spinner";
import { useClusters } from "@/hooks/queries/useClusters";
import { useAlerts } from "@/hooks/queries/useAlerts";
import { useAckAlert } from "@/hooks/mutations/useAckAlert";
import { formatRelative, formatUtc } from "@/lib/format";

const KINDS: AlertKind[] = ["wal_lag", "backup_failed", "clock_drift", "role_flapping"];
const STATUSES = ["open", "acknowledged", "resolved", "all"] as const;
type StatusFilter = (typeof STATUSES)[number];

export function AlertsPage() {
  const [params, setParams] = useSearchParams();
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const filters = useMemo(
    () => ({
      status: ((params.get("status") as StatusFilter | null) ?? "open") as StatusFilter,
      kind: (params.get("kind") as AlertKind | null) ?? undefined,
      cluster_id: numberOrUndef(params.get("cluster_id")),
      limit: 200,
    }),
    [params],
  );
  const { data: clusters } = useClusters();
  const alerts = useAlerts(filters);
  const ack = useAckAlert();

  function patch(next: Partial<Record<string, string>>) {
    const merged = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v) merged.set(k, v);
      else merged.delete(k);
    }
    setParams(merged, { replace: true });
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Alerts</h1>
          <p className="text-sm text-muted-foreground">
            Rules: WAL lag &gt; 60s (crit &gt; 5m), backup failure, clock drift &gt; 2s,
            role flapping (≥3 transitions in 10m). Acknowledge to silence renotifications.
          </p>
        </div>
        <Button variant="secondary" onClick={() => alerts.refetch()}>
          <RefreshCw className="h-4 w-4" /> Refresh
        </Button>
      </header>

      <Card>
        <CardContent className="grid gap-3 p-4 sm:grid-cols-3">
          <Select
            value={filters.status}
            onChange={(e) => patch({ status: e.target.value })}
            aria-label="Status"
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </Select>
          <Select
            value={filters.kind ?? ""}
            onChange={(e) => patch({ kind: e.target.value })}
            aria-label="Kind"
          >
            <option value="">Any kind</option>
            {KINDS.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </Select>
          <Select
            value={filters.cluster_id != null ? String(filters.cluster_id) : ""}
            onChange={(e) => patch({ cluster_id: e.target.value })}
            aria-label="Cluster"
          >
            <option value="">All clusters</option>
            {clusters?.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </Select>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-sm flex items-center gap-2">
            <Filter className="h-4 w-4" /> Alerts
            {alerts.data && (
              <span className="ml-2 text-muted-foreground">
                ({alerts.data.length} shown)
              </span>
            )}
          </CardTitle>
          {alerts.isFetching && <Spinner />}
        </CardHeader>
        <CardContent className="p-0">
          {alerts.isError && (
            <div className="flex items-center gap-2 p-4 text-sm text-destructive">
              <AlertTriangle className="h-4 w-4" />
              {(alerts.error as Error).message}
            </div>
          )}
          {alerts.data && alerts.data.length === 0 && (
            <p className="p-6 text-center text-sm text-muted-foreground">
              No alerts match these filters. {filters.status === "open" ? "All clear!" : ""}
            </p>
          )}
          {alerts.data && alerts.data.length > 0 && (
            <AlertTable
              alerts={alerts.data}
              clusters={clusters ?? []}
              expandedId={expandedId}
              onToggle={(id) => setExpandedId((p) => (p === id ? null : id))}
              isAdmin={isAdmin}
              ackPending={ack.isPending}
              onAck={(id) => ack.mutate(id)}
              ackError={ack.error}
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function AlertTable({
  alerts,
  clusters,
  expandedId,
  onToggle,
  isAdmin,
  onAck,
  ackPending,
  ackError,
}: {
  alerts: Alert[];
  clusters: { id: number; name: string }[];
  expandedId: number | null;
  onToggle: (id: number) => void;
  isAdmin: boolean;
  onAck: (id: number) => void;
  ackPending: boolean;
  ackError: unknown;
}) {
  const clusterName = (id: number | null) =>
    id == null ? "—" : clusters.find((c) => c.id === id)?.name ?? `#${id}`;

  return (
    <div className="scroll-thin max-h-[70vh] overflow-auto">
      <table className="w-full border-collapse text-sm">
        <thead className="sticky top-0 z-10 bg-card">
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="px-3 py-2 font-medium">Sev.</th>
            <th className="px-3 py-2 font-medium">Kind</th>
            <th className="px-3 py-2 font-medium">Cluster</th>
            <th className="px-3 py-2 font-medium">Target</th>
            <th className="px-3 py-2 font-medium">Opened (UTC)</th>
            <th className="px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium" />
          </tr>
        </thead>
        <tbody>
          {alerts.map((alert) => {
            const open = expandedId === alert.id;
            return (
              <Fragment key={alert.id}>
                <tr
                  className="cursor-pointer border-b border-border/60 hover:bg-muted/40"
                  onClick={() => onToggle(alert.id)}
                >
                  <td className="px-3 py-2">
                    <SeverityBadge severity={alert.severity} />
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{alert.kind}</td>
                  <td className="px-3 py-2 text-xs">{clusterName(alert.cluster_id)}</td>
                  <td className="px-3 py-2 font-mono text-xs">
                    {alert.dedup_key || "—"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">
                    {formatUtc(alert.opened_at)}
                    <div className="text-[10px] text-muted-foreground">
                      {formatRelative(alert.opened_at)}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <StatusBadge alert={alert} />
                  </td>
                  <td className="px-3 py-2 text-right">
                    {alert.resolved_at == null && alert.acknowledged_at == null && (
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          onAck(alert.id);
                        }}
                        disabled={!isAdmin || ackPending}
                        title={isAdmin ? "Ack to silence renotifications" : "Admin only"}
                      >
                        <BellOff className="h-3 w-3" /> Ack
                      </Button>
                    )}
                  </td>
                </tr>
                {open && (
                  <tr className="border-b border-border/60">
                    <td colSpan={7} className="bg-muted/20 px-3 py-3">
                      <AlertDetails alert={alert} ackError={ackError} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function AlertDetails({ alert, ackError }: { alert: Alert; ackError: unknown }) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <div className="space-y-1 text-xs">
        <div className="grid grid-cols-2 gap-y-1">
          <span className="text-muted-foreground">Resolved at</span>
          <span>{alert.resolved_at ? formatUtc(alert.resolved_at) : "—"}</span>
          <span className="text-muted-foreground">Acknowledged at</span>
          <span>{alert.acknowledged_at ? formatUtc(alert.acknowledged_at) : "—"}</span>
          <span className="text-muted-foreground">Acknowledged by</span>
          <span>{alert.acknowledged_by != null ? `user #${alert.acknowledged_by}` : "—"}</span>
          <span className="text-muted-foreground">Last notified at</span>
          <span>{alert.last_notified_at ? formatUtc(alert.last_notified_at) : "—"}</span>
        </div>
        {ackError instanceof ApiError && (
          <p className="pt-2 text-destructive">{ackError.message}</p>
        )}
      </div>
      <div className="space-y-1 text-xs">
        <div className="text-muted-foreground">Payload</div>
        <pre className="scroll-thin max-h-60 overflow-auto rounded-md border border-border bg-background p-2 font-mono text-[11px]">
          {JSON.stringify(alert.payload, null, 2)}
        </pre>
      </div>
    </div>
  );
}

function SeverityBadge({ severity }: { severity: AlertSeverity }) {
  if (severity === "critical")
    return (
      <Badge tone="destructive">
        <AlertTriangle className="h-3 w-3" /> critical
      </Badge>
    );
  if (severity === "warning")
    return (
      <Badge tone="warning">
        <AlertTriangle className="h-3 w-3" /> warning
      </Badge>
    );
  return <Badge tone="muted">info</Badge>;
}

function StatusBadge({ alert }: { alert: Alert }) {
  if (alert.resolved_at) {
    return (
      <Badge tone="success">
        <CheckCircle2 className="h-3 w-3" /> resolved
      </Badge>
    );
  }
  if (alert.acknowledged_at) {
    return (
      <Badge tone="muted">
        <BellOff className="h-3 w-3" /> ack&apos;d
      </Badge>
    );
  }
  return (
    <Badge tone="primary">
      <Clock className="h-3 w-3" /> open
    </Badge>
  );
}

function numberOrUndef(v: string | null): number | undefined {
  if (!v) return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}
