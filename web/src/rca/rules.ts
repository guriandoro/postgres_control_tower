/**
 * Root-cause-analysis hints (PLAN §6, P5 deliverable).
 *
 * v1 ships 5 hardcoded rules. Each rule is a deterministic predicate over
 * the visible event window; if it fires, the Logs page surfaces the hint
 * with the specific events it correlated. We deliberately keep these
 * dumb-and-explainable — no ML, no scoring — so the operator can verify
 * the suggestion in seconds.
 *
 * Future (v2): rule engine driven by config, learning thresholds, etc.
 * Until then this list grows by hand. Each rule lives in one place so
 * adding a new one is a single edit.
 */

import type { LogEvent } from "@/api/types";

export interface RcaHint {
  id: string;
  title: string;
  body: string;
  /** Highlighted events (max 5) the operator should look at first. */
  evidence: LogEvent[];
  severity: "info" | "warning" | "critical";
}

interface RuleContext {
  events: LogEvent[];
  /** Indexed by id for cheap evidence lookup. */
  byId: Map<number, LogEvent>;
}

interface Rule {
  id: string;
  evaluate: (ctx: RuleContext) => RcaHint | null;
}

const RULES: Rule[] = [
  {
    id: "oom-before-failover",
    evaluate: ({ events }) => {
      // OOM in OS source within 30s before a Patroni promotion → likely cause.
      const oom = events.find(
        (e) =>
          e.source === "os" &&
          (e.parsed as { category?: string } | null)?.category === "oom_killer",
      );
      const promotion = events.find(
        (e) =>
          e.source === "patroni" &&
          (e.parsed as { role_transition?: { to?: string } } | null)?.role_transition
            ?.to === "primary",
      );
      if (!oom || !promotion) return null;
      const dt = Math.abs(
        new Date(promotion.ts_utc).getTime() - new Date(oom.ts_utc).getTime(),
      );
      if (dt > 30_000) return null;
      return {
        id: "oom-before-failover",
        title: "OOM Killer fired ~30s before Patroni promotion",
        body:
          "An out-of-memory event on the host preceded a leader change. " +
          "Investigate work_mem / shared_buffers / non-Postgres processes on this host.",
        severity: "critical",
        evidence: [oom, promotion],
      };
    },
  },
  {
    id: "wal-archive-failures",
    evaluate: ({ events }) => {
      const failures = events.filter(
        (e) =>
          e.source === "postgres" &&
          /archiv(?:e|ing).*(failed|error)/i.test(
            String((e.parsed as { message?: string } | null)?.message ?? ""),
          ),
      );
      if (failures.length < 3) return null;
      return {
        id: "wal-archive-failures",
        title: `WAL archive failed ${failures.length} times in window`,
        body:
          "Repeated archive_command failures detected. Check pgBackRest stanza " +
          "permissions, repo connectivity, and disk space on the repo host.",
        severity: "warning",
        evidence: failures.slice(0, 5),
      };
    },
  },
  {
    id: "etcd-leader-flap",
    evaluate: ({ events }) => {
      const transitions = events.filter(
        (e) =>
          e.source === "etcd" &&
          (e.parsed as { role_transition?: unknown } | null)?.role_transition,
      );
      if (transitions.length < 3) return null;
      return {
        id: "etcd-leader-flap",
        title: `etcd leadership changed ${transitions.length} times in window`,
        body:
          "etcd is unstable. Patroni decisions on top of a flapping etcd are " +
          "unreliable; verify network latency between etcd peers and quorum size " +
          "(production needs 3+ nodes).",
        severity: "critical",
        evidence: transitions.slice(0, 5),
      };
    },
  },
  {
    id: "fatal-auth-spam",
    evaluate: ({ events }) => {
      const fails = events.filter(
        (e) =>
          e.source === "postgres" &&
          e.severity === "critical" &&
          /authentication failed|password authentication/i.test(
            String((e.parsed as { message?: string } | null)?.message ?? ""),
          ),
      );
      if (fails.length < 5) return null;
      return {
        id: "fatal-auth-spam",
        title: `${fails.length} authentication FATALs in window`,
        body:
          "A burst of failed logins suggests credential rotation, a brute-force " +
          "attempt, or a stale connection pool still using an expired secret.",
        severity: "warning",
        evidence: fails.slice(0, 5),
      };
    },
  },
  {
    id: "io-error-near-failover",
    evaluate: ({ events }) => {
      const ioErr = events.find(
        (e) =>
          e.source === "os" &&
          (e.parsed as { category?: string } | null)?.category === "io_error",
      );
      const failover = events.find(
        (e) =>
          (e.parsed as { role_transition?: unknown } | null)?.role_transition,
      );
      if (!ioErr || !failover) return null;
      const dt = Math.abs(
        new Date(failover.ts_utc).getTime() - new Date(ioErr.ts_utc).getTime(),
      );
      if (dt > 60_000) return null;
      return {
        id: "io-error-near-failover",
        title: "Disk I/O error within 1 minute of a role change",
        body:
          "Storage faults often manifest as Postgres hangs that Patroni interprets " +
          "as a dead leader. Check dmesg / SMART on the affected host.",
        severity: "critical",
        evidence: [ioErr, failover],
      };
    },
  },
];

export function evaluateRules(events: LogEvent[]): RcaHint[] {
  const ctx: RuleContext = {
    events,
    byId: new Map(events.map((e) => [e.id, e])),
  };
  const out: RcaHint[] = [];
  for (const rule of RULES) {
    try {
      const hint = rule.evaluate(ctx);
      if (hint) out.push(hint);
    } catch (err) {
      // RCA must never crash the page — log and continue.
      console.warn("RCA rule failed", rule.id, err);
    }
  }
  return out;
}
