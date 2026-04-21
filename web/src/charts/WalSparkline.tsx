import { useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { WalHealthSeries } from "@/api/types";

/** Stable, color-blind-friendly palette for up to 8 agents per cluster. */
const AGENT_COLORS = [
  "hsl(217, 91%, 60%)",
  "hsl(142, 71%, 45%)",
  "hsl(38, 92%, 50%)",
  "hsl(0, 84%, 60%)",
  "hsl(271, 81%, 56%)",
  "hsl(189, 94%, 43%)",
  "hsl(330, 81%, 60%)",
  "hsl(83, 78%, 45%)",
];

interface ChartRow {
  ts: number;
  /** dynamic per-agent column: ``lag_<agentId>`` → seconds (or null gap) */
  [key: `lag_${number}`]: number | null;
}

/**
 * Per-agent WAL-archive-lag chart. Each agent gets its own line, sharing
 * a UTC time axis so primary vs replica drift is visible at a glance.
 *
 * Data comes from ``GET /api/v1/clusters/{id}/wal_health``; we used to
 * collapse "latest sample per agent" into a single sparkline, which made
 * the HA chart look like a single dot — see fix in cluster.tsx.
 */
export function WalSparkline({ series }: { series: WalHealthSeries[] }) {
  const { rows, agents } = useMemo(() => {
    const agentsWithSamples = series.filter((s) => s.samples.length > 0);
    if (agentsWithSamples.length === 0) {
      return { rows: [] as ChartRow[], agents: agentsWithSamples };
    }
    // Bucket every sample by epoch-ms so x-axis values line up across
    // agents that ticked at slightly different instants.
    const buckets = new Map<number, ChartRow>();
    for (const s of agentsWithSamples) {
      for (const sample of s.samples) {
        const ts = Date.parse(sample.captured_at);
        if (Number.isNaN(ts)) continue;
        const row = buckets.get(ts) ?? ({ ts } as ChartRow);
        row[`lag_${s.agent_id}`] = sample.archive_lag_seconds;
        buckets.set(ts, row);
      }
    }
    const out = [...buckets.values()].sort((a, b) => a.ts - b.ts);
    return { rows: out, agents: agentsWithSamples };
  }, [series]);

  if (rows.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center text-xs text-muted-foreground">
        no WAL samples yet
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="h-32">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 8, left: 4, bottom: 4 }}>
            <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="2 4" />
            <XAxis
              dataKey="ts"
              type="number"
              domain={["dataMin", "dataMax"]}
              tickFormatter={formatTick}
              tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
              minTickGap={32}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
              domain={[0, "dataMax"]}
              width={28}
              tickFormatter={(v) => `${v}s`}
            />
            <Tooltip
              contentStyle={{
                background: "hsl(var(--card))",
                border: "1px solid hsl(var(--border))",
                borderRadius: 6,
                fontSize: 12,
              }}
              labelFormatter={(v) => new Date(v as number).toISOString().slice(11, 19) + "Z"}
              formatter={(value: number, name: string) => {
                const id = Number(name.replace("lag_", ""));
                const agent = agents.find((a) => a.agent_id === id);
                const label = agent ? `${agent.hostname} (${agent.role})` : name;
                return [`${value}s`, label];
              }}
            />
            {agents.map((agent, i) => (
              <Line
                key={agent.agent_id}
                type="monotone"
                dataKey={`lag_${agent.agent_id}`}
                name={`lag_${agent.agent_id}`}
                stroke={AGENT_COLORS[i % AGENT_COLORS.length]}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <ul className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        {agents.map((agent, i) => (
          <li key={agent.agent_id} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: AGENT_COLORS[i % AGENT_COLORS.length] }}
            />
            <span className="font-mono">{agent.hostname}</span>
            <span>· {agent.role}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function formatTick(ts: number): string {
  const d = new Date(ts);
  return `${d.getUTCHours().toString().padStart(2, "0")}:${d
    .getUTCMinutes()
    .toString()
    .padStart(2, "0")}`;
}
