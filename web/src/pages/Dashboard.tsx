import { type ReactNode } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, BellRing, CheckCircle2, Database, Server } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { useClusters } from "@/hooks/queries/useClusters";
import { useAlertsSummary } from "@/hooks/queries/useAlerts";
import { formatRelative } from "@/lib/format";

export function DashboardPage() {
  const { data, isLoading, isError, error } = useClusters();
  const summary = useAlertsSummary();
  const openAlerts = summary.data?.open_total;
  const criticalAlerts = summary.data?.by_severity.critical ?? 0;

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Fleet</h1>
          <p className="text-sm text-muted-foreground">
            Real-time view of every PostgreSQL cluster reporting to this manager.
          </p>
        </div>
      </header>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard
          title="Clusters"
          value={data?.length ?? "—"}
          icon={<Database className="h-4 w-4 text-primary" />}
        />
        <StatCard
          title="Agents online"
          value={data?.reduce((sum, c) => sum + c.agent_count, 0) ?? "—"}
          icon={<Server className="h-4 w-4 text-primary" />}
        />
        <StatCard
          title="Reporting in last 5m"
          value={data ? data.filter((c) => isRecent(c.last_seen_at, 5 * 60_000)).length : "—"}
          icon={<CheckCircle2 className="h-4 w-4 text-success" />}
        />
        <StatCard
          title="Stale (no data &gt; 5m)"
          value={data ? data.filter((c) => !isRecent(c.last_seen_at, 5 * 60_000)).length : "—"}
          icon={<AlertTriangle className="h-4 w-4 text-warning" />}
        />
        <Link to="/alerts" className="block">
          <Card
            className={
              criticalAlerts > 0
                ? "border-destructive/40 transition-colors hover:border-destructive"
                : "transition-colors hover:border-primary/50"
            }
          >
            <CardHeader className="flex flex-row items-center justify-between gap-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Open alerts
              </CardTitle>
              <BellRing
                className={
                  criticalAlerts > 0
                    ? "h-4 w-4 text-destructive"
                    : openAlerts && openAlerts > 0
                      ? "h-4 w-4 text-warning"
                      : "h-4 w-4 text-muted-foreground"
                }
              />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-semibold">{openAlerts ?? "—"}</p>
              {summary.data && (
                <p className="text-[11px] text-muted-foreground">
                  {criticalAlerts} critical · {summary.data.open_acknowledged} ack&apos;d
                </p>
              )}
            </CardContent>
          </Card>
        </Link>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">Clusters</h2>
        {isLoading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Spinner /> loading clusters…
          </div>
        )}
        {isError && (
          <Card className="border-destructive/40">
            <CardContent className="p-4 text-sm text-destructive">
              Failed to load clusters: {(error as Error)?.message}
            </CardContent>
          </Card>
        )}
        {data && data.length === 0 && (
          <Card>
            <CardContent className="p-6 text-center text-sm text-muted-foreground">
              No clusters yet. Register an agent with{" "}
              <code className="rounded bg-muted px-1">pct-agent register</code> to get started.
            </CardContent>
          </Card>
        )}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {data?.map((cluster) => {
            const fresh = isRecent(cluster.last_seen_at, 5 * 60_000);
            return (
              <Link key={cluster.id} to={`/clusters/${cluster.id}`} className="block">
                <Card className="transition-colors hover:border-primary/50">
                  <CardHeader>
                    <div className="flex items-center justify-between gap-2">
                      <CardTitle className="truncate">{cluster.name}</CardTitle>
                      <Badge tone={cluster.kind === "patroni" ? "primary" : "muted"}>
                        {cluster.kind}
                      </Badge>
                    </div>
                    <CardDescription>
                      {cluster.agent_count} agent{cluster.agent_count === 1 ? "" : "s"}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">
                      last seen: {formatRelative(cluster.last_seen_at)}
                    </span>
                    <Badge tone={fresh ? "success" : "warning"}>
                      {fresh ? "online" : "stale"}
                    </Badge>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function StatCard({
  title,
  value,
  icon,
}: {
  title: string;
  value: number | string;
  icon: ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold">{value}</p>
      </CardContent>
    </Card>
  );
}

function isRecent(iso: string | null, withinMs: number): boolean {
  if (!iso) return false;
  return Date.now() - new Date(iso).getTime() < withinMs;
}
