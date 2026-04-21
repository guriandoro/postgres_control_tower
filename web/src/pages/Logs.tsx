import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, RefreshCw, Search, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Spinner } from "@/components/ui/Spinner";
import { useClusters } from "@/hooks/queries/useClusters";
import { useLogs, type LogFilters } from "@/hooks/queries/useLogs";
import { evaluateRules } from "@/rca/rules";
import type { LogEvent, LogSeverity, LogSource } from "@/api/types";
import { formatUtc } from "@/lib/format";
import { queryKeys } from "@/api/keys";

const SOURCES: LogSource[] = ["postgres", "pgbackrest", "patroni", "etcd", "os"];
const SEVERITIES: LogSeverity[] = ["debug", "info", "warning", "error", "critical"];

export function LogsPage() {
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();

  const filters: LogFilters = useMemo(
    () => ({
      cluster_id: numberOrUndef(params.get("cluster_id")),
      source: (params.get("source") as LogSource | null) ?? undefined,
      severity: (params.get("severity") as LogSeverity | null) ?? undefined,
      q: params.get("q") ?? undefined,
      limit: 200,
    }),
    [params],
  );
  const [qDraft, setQDraft] = useState(filters.q ?? "");

  const { data: clusters } = useClusters();
  const logsQuery = useLogs(filters);
  const events = logsQuery.data ?? [];

  const hints = useMemo(() => evaluateRules(events), [events]);

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
        <Button variant="secondary" onClick={snap}>
          <RefreshCw className="h-4 w-4" /> Instant Snap
        </Button>
      </header>

      <Card>
        <CardContent className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-5">
          <Select
            value={filters.cluster_id != null ? String(filters.cluster_id) : ""}
            onChange={(e) => patch({ cluster_id: e.target.value })}
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
            <th className="px-3 py-2 font-medium">Source</th>
            <th className="px-3 py-2 font-medium">Severity</th>
            <th className="px-3 py-2 font-medium">Agent</th>
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
              <td className="px-3 py-1.5">
                <Badge tone="muted">{e.source}</Badge>
              </td>
              <td className="px-3 py-1.5">
                <Badge tone={severityTone(e.severity)}>{e.severity}</Badge>
              </td>
              <td className="whitespace-nowrap px-3 py-1.5 font-mono text-muted-foreground">
                #{e.agent_id}
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
