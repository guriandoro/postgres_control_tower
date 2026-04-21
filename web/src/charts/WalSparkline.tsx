import { useMemo } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";

interface SparkPoint {
  ts: string;
  lag: number | null;
}

/**
 * Tiny WAL-archive-lag sparkline. Datapoints come from the per-cluster
 * detail (one point per agent in v1, since we only persist the latest
 * sample per agent). Once we keep history (P7 storage runway), this will
 * become a real time series chart.
 */
export function WalSparkline({ points }: { points: SparkPoint[] }) {
  const data = useMemo(
    () =>
      points
        .filter((p) => p.lag !== null)
        .map((p, i) => ({ idx: i, ts: p.ts, lag: p.lag as number })),
    [points],
  );

  if (data.length === 0) {
    return (
      <div className="flex h-16 items-center justify-center text-xs text-muted-foreground">
        no WAL samples yet
      </div>
    );
  }

  return (
    <div className="h-16">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 4, left: 4, bottom: 4 }}>
          <YAxis hide domain={[0, "dataMax"]} />
          <Tooltip
            contentStyle={{
              background: "hsl(var(--card))",
              border: "1px solid hsl(var(--border))",
              borderRadius: 6,
              fontSize: 12,
            }}
            labelFormatter={(_l, payload) =>
              payload?.[0]?.payload ? `agent ${payload[0].payload.idx + 1}` : ""
            }
            formatter={(value: number) => [`${value}s`, "archive lag"]}
          />
          <Line
            type="monotone"
            dataKey="lag"
            stroke="hsl(var(--primary))"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
