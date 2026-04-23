import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Download, RefreshCw, Search, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Spinner } from "@/components/ui/Spinner";
import { useAgents } from "@/hooks/queries/useAgents";
import { useClusters } from "@/hooks/queries/useClusters";
import { useLogs, type LogFilters } from "@/hooks/queries/useLogs";
import { evaluateRules } from "@/rca/rules";
import { apiRequest } from "@/api/client";
import type { AgentRole, LogEvent, LogSeverity, LogSource } from "@/api/types";
import { formatUtc } from "@/lib/format";
import { queryKeys } from "@/api/keys";

// Hard cap matches `_MAX_QUERY_LIMIT` in `manager/pct_manager/routes/logs.py`.
// We deliberately fetch up to this cap (rather than reusing the table's 200)
// so a "Download" gives the operator the largest filtered batch the manager
// will serve in a single hop, without us having to paginate client-side.
const DOWNLOAD_LIMIT = 1_000;

const SOURCES: LogSource[] = ["postgres", "pgbackrest", "patroni", "etcd", "os"];
const SEVERITIES: LogSeverity[] = ["debug", "info", "warning", "error", "critical"];

export function LogsPage() {
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();

  const filters: LogFilters = useMemo(
    () => ({
      cluster_id: numberOrUndef(params.get("cluster_id")),
      agent_id: numberOrUndef(params.get("agent_id")),
      source: (params.get("source") as LogSource | null) ?? undefined,
      severity: (params.get("severity") as LogSeverity | null) ?? undefined,
      q: params.get("q") ?? undefined,
      limit: 200,
    }),
    [params],
  );
  const [qDraft, setQDraft] = useState(filters.q ?? "");

  const { data: clusters } = useClusters();
  const { data: agents } = useAgents();
  const logsQuery = useLogs(filters);
  const events = logsQuery.data ?? [];

  // When a cluster is selected, narrow the Node dropdown to its members
  // so a 3-node Patroni demo doesn't show 12 unrelated hostnames.
  const visibleAgents = useMemo(() => {
    if (!agents) return [];
    if (filters.cluster_id == null) return agents;
    return agents.filter((a) => a.cluster_id === filters.cluster_id);
  }, [agents, filters.cluster_id]);

  const hints = useMemo(() => evaluateRules(events), [events]);

  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  function patch(next: Partial<Record<keyof LogFilters, string>>) {
    const merged = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v) merged.set(k, v);
      else merged.delete(k);
    }
    setParams(merged, { replace: true });
  }

  function snap() {
    queryClient.invalidateQueries({ queryKey: queryKeys.logs(filters) });
  }

  async function download() {
    setDownloading(true);
    setDownloadError(null);
    try {
      // Re-query with the highest limit the backend allows so the file
      // can carry more than the 200 rows the table renders. Spread first,
      // then override `limit` so the table's React Query cache entry
      // stays untouched.
      const data = await apiRequest<LogEvent[]>("/api/v1/logs/events", {
        query: { ...filters, limit: DOWNLOAD_LIMIT },
      });
      const ndjson = data.map((e) => JSON.stringify(e)).join("\n") + "\n";
      const blob = new Blob([ndjson], { type: "application/x-ndjson" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = buildFilename(filters, data.length);
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Defer the revoke so Safari has time to start the download
      // before the URL goes away.
      setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Logs Surgeon</h1>
          <p className="text-sm text-muted-foreground">
            Unified, UTC-normalized stream across Postgres, pgBackRest, Patroni,
            etcd and OS journald. Auto-refreshes every 5 minutes.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={download}
              disabled={downloading}
              title={`Download up to ${DOWNLOAD_LIMIT} matching events as NDJSON`}
            >
              {downloading ? <Spinner /> : <Download className="h-4 w-4" />}
              Download
            </Button>
            <Button variant="secondary" onClick={snap}>
              <RefreshCw className="h-4 w-4" /> Instant Snap
            </Button>
          </div>
          {downloadError && (
            <p className="text-xs text-destructive" role="alert">
              Download failed: {downloadError}
            </p>
          )}
        </div>
      </header>

      <Card>
        <CardContent className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-6">
          <Select
            value={filters.cluster_id != null ? String(filters.cluster_id) : ""}
            onChange={(e) =>
              // Clear the node filter when switching clusters so the user
              // doesn't end up with an empty result set from a stale agent_id.
              patch({ cluster_id: e.target.value, agent_id: "" })
            }
            aria-label="Cluster"
          >
            <option value="">All clusters</option>
            {clusters?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
          <Select
            value={filters.agent_id != null ? String(filters.agent_id) : ""}
            onChange={(e) => patch({ agent_id: e.target.value })}
            aria-label="Node"
          >
            <option value="">All nodes</option>
            {visibleAgents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.hostname}
                {a.role !== "unknown" ? ` (${a.role})` : ""}
              </option>
            ))}
          </Select>
          <Select
            value={filters.source ?? ""}
            onChange={(e) => patch({ source: e.target.value })}
            aria-label="Source"
          >
            <option value="">All sources</option>
            {SOURCES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
          <Select
            value={filters.severity ?? ""}
            onChange={(e) => patch({ severity: e.target.value })}
            aria-label="Severity"
          >
            <option value="">All severities</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
          <form
            className="relative sm:col-span-2"
            onSubmit={(e) => {
              e.preventDefault();
              patch({ q: qDraft });
            }}
          >
            <Search className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={qDraft}
              onChange={(e) => setQDraft(e.target.value)}
              placeholder="search raw + parsed (free text)"
              className="pl-8"
            />
          </form>
        </CardContent>
      </Card>

      {hints.length > 0 && (
        <section className="grid gap-2 md:grid-cols-2">
          {hints.map((h) => (
            <Card key={h.id} className="border-warning/40">
              <CardHeader className="flex flex-row items-start gap-3 space-y-0">
                <Sparkles
                  className={
                    h.severity === "critical"
                      ? "mt-0.5 h-4 w-4 text-destructive"
                      : "mt-0.5 h-4 w-4 text-warning"
                  }
                />
                <div className="flex-1">
                  <CardTitle className="text-sm">{h.title}</CardTitle>
                  <CardDescription>{h.body}</CardDescription>
                </div>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground">
                Evidence:&nbsp;
                {h.evidence.map((e, i) => (
                  <span key={e.id} className="font-mono">
                    {i > 0 && ", "}#{e.id}
                  </span>
                ))}
              </CardContent>
            </Card>
          ))}
        </section>
      )}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-2">
          <CardTitle className="text-sm">
            Events {events.length > 0 && (
              <span className="ml-2 text-muted-foreground">
                ({events.length} shown)
              </span>
            )}
          </CardTitle>
          {logsQuery.isFetching && <Spinner />}
        </CardHeader>
        <CardContent className="p-0">
          {logsQuery.isError && (
            <div className="flex items-center gap-2 p-4 text-sm text-destructive">
              <AlertTriangle className="h-4 w-4" />
              Failed to load logs: {(logsQuery.error as Error).message}
            </div>
          )}
          {!logsQuery.isError && events.length === 0 && !logsQuery.isLoading && (
            <p className="p-6 text-center text-sm text-muted-foreground">
              No events match these filters.
            </p>
          )}
          <LogTable events={events} />
        </CardContent>
      </Card>
    </div>
  );
}

function LogTable({ events }: { events: LogEvent[] }) {
  if (events.length === 0) return null;
  return (
    <div className="scroll-thin max-h-[70vh] overflow-auto">
      <table className="w-full border-collapse text-xs">
        <thead className="sticky top-0 z-10 bg-card">
          <tr className="border-b border-border text-left text-muted-foreground">
            <th className="px-3 py-2 font-medium">Time (UTC)</th>
            <th className="px-3 py-2 font-medium">Node</th>
            <th className="px-3 py-2 font-medium">Source</th>
            <th className="px-3 py-2 font-medium">Severity</th>
            <th className="px-3 py-2 font-medium">Message</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr
              key={e.id}
              className="border-b border-border/60 align-top hover:bg-muted/40"
            >
              <td className="whitespace-nowrap px-3 py-1.5 font-mono text-muted-foreground">
                {formatUtc(e.ts_utc)}
              </td>
              <td className="whitespace-nowrap px-3 py-1.5">
                <NodeCell event={e} />
              </td>
              <td className="px-3 py-1.5">
                <Badge tone="muted">{e.source}</Badge>
              </td>
              <td className="px-3 py-1.5">
                <Badge tone={severityTone(e.severity)}>{e.severity}</Badge>
              </td>
              <td className="px-3 py-1.5">
                <div className="font-mono leading-snug">
                  {extractMessage(e)}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NodeCell({ event }: { event: LogEvent }) {
  const label = event.hostname ?? `agent #${event.agent_id}`;
  return (
    <div className="flex items-center gap-1.5">
      <span className="font-mono text-foreground">{label}</span>
      {event.node_role !== "unknown" && (
        <Badge tone={roleTone(event.node_role)}>{event.node_role}</Badge>
      )}
    </div>
  );
}

function roleTone(role: AgentRole): "muted" | "neutral" | "primary" {
  switch (role) {
    case "primary":
      return "primary";
    case "replica":
      return "neutral";
    default:
      return "muted";
  }
}

function severityTone(s: LogSeverity): "muted" | "neutral" | "warning" | "destructive" {
  switch (s) {
    case "critical":
    case "error":
      return "destructive";
    case "warning":
      return "warning";
    case "info":
      return "neutral";
    default:
      return "muted";
  }
}

function extractMessage(e: LogEvent): string {
  const parsed = e.parsed as Record<string, unknown> | null;
  if (parsed && typeof parsed.message === "string") return parsed.message;
  return e.raw;
}

function numberOrUndef(v: string | null): number | undefined {
  if (!v) return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

function buildFilename(filters: LogFilters, count: number): string {
  // Compact UTC timestamp, e.g. 20260423T155841Z. Shell- and Windows-safe.
  const ts = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
  const parts = ["pct-logs", ts, `n${count}`];
  if (filters.cluster_id != null) parts.push(`cluster${filters.cluster_id}`);
  if (filters.agent_id != null) parts.push(`agent${filters.agent_id}`);
  if (filters.source) parts.push(filters.source);
  if (filters.severity) parts.push(filters.severity);
  return `${parts.join("-")}.ndjson`;
}
