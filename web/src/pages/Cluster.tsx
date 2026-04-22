import { useMemo, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { RetentionTimeline } from "@/charts/RetentionTimeline";
import { WalSparkline } from "@/charts/WalSparkline";
import { useCluster, useClusterWalHealth } from "@/hooks/queries/useClusters";
import { useRoleTransitions } from "@/hooks/queries/useLogs";
import { useStorageForecast } from "@/hooks/queries/useAlerts";
import type {
  AgentDetail,
  AgentRole,
  PatroniRole,
  PatroniState,
  PgbrStanza,
} from "@/api/types";
import { formatBytes, formatDuration, formatRelative, formatUtc } from "@/lib/format";

export function ClusterPage() {
  const params = useParams();
  const clusterId = Number(params.id);
  const { data, isLoading, isError, error } = useCluster(
    Number.isFinite(clusterId) ? clusterId : undefined,
  );
  const { data: transitions } = useRoleTransitions({
    cluster_id: Number.isFinite(clusterId) ? clusterId : undefined,
    limit: 25,
  });
  const { data: forecast } = useStorageForecast(
    Number.isFinite(clusterId) ? clusterId : undefined,
  );
  const { data: walHealth } = useClusterWalHealth(
    Number.isFinite(clusterId) ? clusterId : undefined,
  );

  const stanzas = useMemo<PgbrStanza[]>(() => {
    if (!data) return [];
    const out: PgbrStanza[] = [];
    for (const agent of data.agents) {
      const payload = agent.latest_pgbackrest_info?.payload;
      if (Array.isArray(payload)) {
        for (const stanza of payload as PgbrStanza[]) {
          out.push(stanza);
        }
      }
    }
    // Dedupe by stanza name (Patroni replicas often see the same repo).
    const byName = new Map<string, PgbrStanza>();
    for (const s of out) {
      if (!byName.has(s.name)) byName.set(s.name, s);
    }
    return [...byName.values()];
  }, [data]);

  // Pick a single canonical Patroni snapshot to drive the cluster-wide
  // overview (leader, timeline, member roster). All members report the
  // same view, but they may be off by one tick — prefer the leader's
  // snapshot when present, otherwise the freshest one.
  //
  // NOTE: this hook MUST run before any early-return below, otherwise
  // the hook count changes between the loading and loaded renders and
  // React bails the whole tree (blank page). See Rules of Hooks.
  const patroniOverview = useMemo<PatroniState | null>(() => {
    if (data?.kind !== "patroni") return null;
    const snapshots = data.agents
      .map((a) => a.latest_patroni_state)
      .filter((s): s is PatroniState => s != null);
    if (snapshots.length === 0) return null;
    const leaderSnap = snapshots.find(
      (s) => s.patroni_role === "leader" || s.patroni_role === "standby_leader",
    );
    if (leaderSnap) return leaderSnap;
    return [...snapshots].sort((a, b) =>
      b.captured_at.localeCompare(a.captured_at),
    )[0];
  }, [data]);

  if (!Number.isFinite(clusterId)) {
    return <p className="text-sm text-destructive">Invalid cluster id.</p>;
  }
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Spinner /> loading cluster…
      </div>
    );
  }
  if (isError || !data) {
    return (
      <Card className="border-destructive/40">
        <CardContent className="p-4 text-sm text-destructive">
          Failed to load cluster: {(error as Error)?.message ?? "unknown error"}
        </CardContent>
      </Card>
    );
  }

  // pgBackRest's info JSON doesn't put a "size" on repo[] entries — those
  // only carry {key, cipher, status}. The on-repo footprint per backup is
  // info.repository.delta (full's full size, diff/incr's added bytes).
  // Sum that across every retained backup of every stanza for a sane
  // "current repo size", matching the manager's storage forecast.
  const repoSizeBytes = stanzas.reduce(
    (sum, s) =>
      sum +
      (s.backup ?? []).reduce(
        (acc, b) =>
          acc +
          (b.info?.repository?.delta ?? b.info?.repository?.size ?? 0),
        0,
      ),
    0,
  );

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Link
            to="/"
            aria-label="Back to dashboard"
            className="grid h-9 w-9 place-items-center rounded-md text-muted-foreground hover:bg-muted"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{data.name}</h1>
            <p className="text-sm text-muted-foreground">
              {data.kind} · {data.agents.length} agent{data.agents.length === 1 ? "" : "s"}
            </p>
          </div>
        </div>
        <Link
          to={`/logs?cluster_id=${data.id}`}
          className="text-sm font-medium text-primary hover:underline"
        >
          Open logs →
        </Link>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">pgBackRest repo size</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">
            {formatBytes(repoSizeBytes || null)}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">Backups in window</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">
            {stanzas.reduce((s, st) => s + (st.backup?.length ?? 0), 0)}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">Max archive lag</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">
            {formatDuration(
              data.agents.reduce(
                (m, a) => Math.max(m, a.latest_wal_health?.archive_lag_seconds ?? 0),
                0,
              ),
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">Role transitions (recent)</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">
            {transitions?.length ?? "—"}
          </CardContent>
        </Card>
      </section>

      {data.kind === "patroni" && (
        <Card>
          <CardHeader>
            <CardTitle>Patroni cluster</CardTitle>
            <CardDescription>
              {patroniOverview ? (
                <>
                  Leader{" "}
                  <span className="font-mono">
                    {patroniOverview.leader_member ?? "—"}
                  </span>{" "}
                  · timeline{" "}
                  <span className="font-mono">
                    {patroniOverview.timeline ?? "—"}
                  </span>{" "}
                  · {patroniOverview.members.length} member
                  {patroniOverview.members.length === 1 ? "" : "s"} ·
                  refreshed {formatRelative(patroniOverview.captured_at)}
                </>
              ) : (
                "Waiting for the first Patroni REST snapshot from any agent…"
              )}
            </CardDescription>
          </CardHeader>
          {patroniOverview && (
            <CardContent>
              <div className="overflow-hidden rounded-md border">
                <table className="w-full border-collapse text-xs">
                  <thead className="bg-muted/40 text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">Member</th>
                      <th className="px-3 py-2 text-left font-medium">Role</th>
                      <th className="px-3 py-2 text-left font-medium">State</th>
                      <th className="px-3 py-2 text-right font-medium">TL</th>
                      <th className="px-3 py-2 text-right font-medium">Lag</th>
                    </tr>
                  </thead>
                  <tbody>
                    {patroniOverview.members.map((m, i) => (
                      <tr
                        key={`${m.name ?? "?"}-${i}`}
                        className="border-t border-border/60"
                      >
                        <td className="px-3 py-2 font-mono">{m.name ?? "—"}</td>
                        <td className="px-3 py-2">
                          <Badge tone={patroniRoleTone(m.role)}>
                            {m.role ?? "unknown"}
                          </Badge>
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">
                          {m.state ?? "—"}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          {m.timeline ?? "—"}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          {m.lag != null ? formatBytes(m.lag) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          )}
        </Card>
      )}

      {forecast && (
        <Card>
          <CardHeader>
            <CardTitle>Storage runway</CardTitle>
            <CardDescription>
              Linear regression over the last week of pgBackRest repo size
              snapshots.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-3 text-sm">
            <Field label="Daily growth">
              <span
                className={
                  forecast.daily_growth_bytes > 0
                    ? "font-mono"
                    : "font-mono text-muted-foreground"
                }
              >
                {forecast.daily_growth_bytes >= 0 ? "+" : "−"}
                {formatBytes(Math.abs(forecast.daily_growth_bytes))}/day
              </span>
            </Field>
            <Field label="Current repo size">
              <span className="font-mono">
                {formatBytes(forecast.current_bytes)}
              </span>
            </Field>
            <Field label="Days to target">
              {forecast.target_bytes == null ? (
                <span className="text-muted-foreground">
                  no target configured
                </span>
              ) : forecast.days_to_target == null ? (
                <span className="text-muted-foreground">— (no growth)</span>
              ) : (
                <span
                  className={
                    forecast.days_to_target < 14
                      ? "font-mono text-destructive"
                      : forecast.days_to_target < 60
                        ? "font-mono text-warning"
                        : "font-mono text-success"
                  }
                >
                  {forecast.days_to_target.toFixed(1)}d
                </span>
              )}
            </Field>
            <p className="col-span-full text-[11px] text-muted-foreground">
              {forecast.sample_count} samples · refreshed{" "}
              {formatRelative(forecast.captured_at)}
            </p>
          </CardContent>
        </Card>
      )}

      <section className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Safety window — pgBackRest retention</CardTitle>
            <CardDescription>
              Each bar is one backup. The span from the leftmost bar to "now" is the
              window in which a PITR is possible.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <RetentionTimeline stanzas={stanzas} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>WAL archive lag</CardTitle>
            <CardDescription>
              {walHealth
                ? `Per-agent series, last ${walHealth.since_minutes} min.`
                : "Per-agent series."}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <WalSparkline series={walHealth?.series ?? []} />
          </CardContent>
        </Card>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">Agents</h2>
        <div className="grid gap-3 lg:grid-cols-2">
          {data.agents.map((agent) => (
            <AgentCard key={agent.id} agent={agent} />
          ))}
        </div>
      </section>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-muted-foreground">{label}</div>
      <div className="mt-0.5">{children}</div>
    </div>
  );
}

function AgentCard({ agent }: { agent: AgentDetail }) {
  const patroni = agent.latest_patroni_state;
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="truncate font-mono text-sm">
            {agent.hostname}
          </CardTitle>
          <div className="flex items-center gap-1">
            {patroni && (
              <Badge tone={patroniRoleTone(patroni.patroni_role)}>
                {patroni.patroni_role}
              </Badge>
            )}
            <Badge tone={roleTone(agent.role)}>{agent.role}</Badge>
          </div>
        </div>
        <CardDescription>
          {agent.version ?? "v?"} · last seen {formatRelative(agent.last_seen_at)}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-y-2 text-xs">
        <Field label="Clock skew">
          {agent.clock_skew_ms != null ? `${agent.clock_skew_ms} ms` : "—"}
        </Field>
        <Field label="Archive lag">
          {formatDuration(agent.latest_wal_health?.archive_lag_seconds ?? null)}
        </Field>
        <Field label="Last archived WAL">
          <span className="font-mono">
            {agent.latest_wal_health?.last_archived_wal ?? "—"}
          </span>
        </Field>
        <Field label="WAL gap">
          {agent.latest_wal_health?.gap_detected ? (
            <Badge tone="destructive">YES</Badge>
          ) : (
            <Badge tone="success">no</Badge>
          )}
        </Field>
        <Field label="pgBackRest snapshot">
          {formatUtc(agent.latest_pgbackrest_info?.captured_at ?? null)}
        </Field>
        <Field label="Last seen">{formatUtc(agent.last_seen_at)}</Field>
        {patroni && (
          <>
            <Field label="Patroni state">
              <span className="text-muted-foreground">
                {patroni.state ?? "—"}
              </span>
            </Field>
            <Field label="Patroni timeline">
              <span className="font-mono">{patroni.timeline ?? "—"}</span>
            </Field>
            <Field label="Replica lag">
              {patroni.lag_bytes != null
                ? formatBytes(patroni.lag_bytes)
                : patroni.patroni_role === "leader" ||
                    patroni.patroni_role === "standby_leader"
                  ? "n/a (leader)"
                  : "—"}
            </Field>
            <Field label="Patroni snapshot">
              {formatRelative(patroni.captured_at)}
            </Field>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function roleTone(role: AgentRole): "primary" | "muted" | "neutral" {
  if (role === "primary") return "primary";
  if (role === "replica") return "neutral";
  return "muted";
}

function patroniRoleTone(
  role: PatroniRole | string | undefined,
): "primary" | "neutral" | "warning" | "muted" {
  switch (role) {
    case "leader":
    case "standby_leader":
      return "primary";
    case "sync_standby":
      return "warning";
    case "replica":
      return "neutral";
    default:
      return "muted";
  }
}
