import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PgbrBackup, PgbrStanza } from "@/api/types";
import { formatBytes } from "@/lib/format";

/**
 * "Safety Window" retention chart for pgBackRest backups.
 *
 * Each backup is a horizontal bar on its stanza row, spanning from
 * `timestamp.start` to `timestamp.stop`. The earliest start across all
 * stanzas is the start of the safety window; "now" is the right edge.
 * Read this chart as: "we can restore to any point covered by a bar".
 *
 * Recharts doesn't ship a Gantt out of the box; we fake it by stacking
 * an invisible "offset" bar plus the real "duration" bar (PLAN §6).
 */
interface RetentionRow {
  label: string;
  type: PgbrBackup["type"];
  stanza: string;
  /** Seconds since the chart's t0. */
  offset: number;
  duration: number;
  startIso: string;
  stopIso: string;
  size?: number;
}

const TYPE_COLOR: Record<string, string> = {
  full: "hsl(var(--primary))",
  diff: "hsl(var(--success))",
  incr: "hsl(var(--warning))",
};

export function RetentionTimeline({ stanzas }: { stanzas: PgbrStanza[] }) {
  const { rows, t0Iso, t1Iso } = useMemo(() => buildRows(stanzas), [stanzas]);

  if (rows.length === 0) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
        no pgBackRest backups recorded yet
      </div>
    );
  }

  // One row per backup, sorted oldest → newest. Chart height grows so we
  // never squash bars when a stanza has lots of backups.
  const height = Math.max(160, rows.length * 22 + 40);

  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={rows}
          layout="vertical"
          margin={{ top: 8, right: 16, left: 24, bottom: 8 }}
          barCategoryGap={2}
        >
          <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" />
          <XAxis
            type="number"
            tickFormatter={(v) => formatOffset(v)}
            stroke="hsl(var(--muted-foreground))"
            fontSize={11}
            domain={[0, "dataMax"]}
          />
          <YAxis
            type="category"
            dataKey="label"
            stroke="hsl(var(--muted-foreground))"
            fontSize={11}
            width={120}
          />
          <Tooltip
            cursor={{ fill: "hsl(var(--muted) / 0.4)" }}
            contentStyle={{
              background: "hsl(var(--card))",
              border: "1px solid hsl(var(--border))",
              borderRadius: 6,
              fontSize: 12,
            }}
            labelFormatter={(label, payload) => {
              const row = payload?.[0]?.payload as RetentionRow | undefined;
              if (!row) return String(label);
              return `${row.stanza}/${row.label} (${row.type})`;
            }}
            formatter={(_value, name, item) => {
              if (name === "offset") return ["", ""];
              const row = item.payload as RetentionRow;
              return [
                `${row.startIso} → ${row.stopIso} (${formatBytes(row.size)})`,
                "window",
              ];
            }}
          />
          <Bar dataKey="offset" stackId="t" fill="transparent" isAnimationActive={false} />
          <Bar
            dataKey="duration"
            stackId="t"
            radius={2}
            fillOpacity={0.85}
            isAnimationActive={false}
            minPointSize={2}
          >
            {rows.map((row) => (
              <Cell
                key={row.label}
                fill={TYPE_COLOR[row.type] ?? "hsl(var(--primary))"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>oldest restore point: {t0Iso}</span>
        <span>now: {t1Iso}</span>
      </div>
    </div>
  );
}

function buildRows(stanzas: PgbrStanza[]): {
  rows: RetentionRow[];
  t0Iso: string;
  t1Iso: string;
} {
  const flat: RetentionRow[] = [];
  let minStart = Number.POSITIVE_INFINITY;
  let maxStop = 0;

  for (const stanza of stanzas) {
    for (const backup of stanza.backup ?? []) {
      const start = backup.timestamp?.start ?? 0;
      const stop = backup.timestamp?.stop ?? start;
      if (!start) continue;
      minStart = Math.min(minStart, start);
      maxStop = Math.max(maxStop, stop);
      flat.push({
        label: backup.label,
        type: backup.type,
        stanza: stanza.name,
        offset: start,
        duration: Math.max(stop - start, 60),
        startIso: new Date(start * 1000).toISOString().replace("T", " ").slice(0, 19),
        stopIso: new Date(stop * 1000).toISOString().replace("T", " ").slice(0, 19),
        size: backup.info?.size,
      });
    }
  }

  if (flat.length === 0) {
    return { rows: [], t0Iso: "", t1Iso: "" };
  }
  const now = Math.floor(Date.now() / 1000);
  const t0 = minStart;
  const t1 = Math.max(maxStop, now);

  // Re-base offsets to 0 so the X axis starts at the oldest backup.
  const rows = flat
    .sort((a, b) => a.offset - b.offset)
    .map((r) => ({ ...r, offset: r.offset - t0 }));

  return {
    rows,
    t0Iso: new Date(t0 * 1000).toISOString().replace("T", " ").slice(0, 19),
    t1Iso: new Date(t1 * 1000).toISOString().replace("T", " ").slice(0, 19),
  };
}

function formatOffset(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}
