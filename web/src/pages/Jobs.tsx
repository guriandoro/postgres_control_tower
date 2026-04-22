import { Fragment, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  Clock,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Spinner } from "@/components/ui/Spinner";
import { useClusters } from "@/hooks/queries/useClusters";
import { useJobs } from "@/hooks/queries/useJobs";
import { useCreateJob } from "@/hooks/mutations/useCreateJob";
import { useBackupSchedules } from "@/hooks/queries/useBackupSchedules";
import {
  useCreateBackupSchedule,
  useDeleteBackupSchedule,
  useUpdateBackupSchedule,
} from "@/hooks/mutations/useBackupScheduleMutations";
import { useAuth } from "@/auth/AuthContext";
import {
  BACKUP_SCHEDULE_KINDS,
  JOB_KINDS,
  type BackupSchedule,
  type BackupScheduleKind,
  type ClusterSummary,
  type Job,
  type JobKind,
  type JobStatus,
} from "@/api/types";
import { ApiError } from "@/api/client";
import { formatRelative, formatUtc } from "@/lib/format";

type SubmitMode = "one_off" | "schedule";

interface CronPreset {
  label: string;
  expression: string;
}

const CRON_PRESETS: CronPreset[] = [
  { label: "Every day · 02:00 UTC", expression: "0 2 * * *" },
  { label: "Every 6 hours", expression: "0 */6 * * *" },
  { label: "Every hour", expression: "0 * * * *" },
  { label: "Sundays · 03:00 UTC", expression: "0 3 * * 0" },
  { label: "Weekdays · 02:30 UTC", expression: "30 2 * * 1-5" },
];

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const [openSubmit, setOpenSubmit] = useState<SubmitMode | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const filters = useMemo(
    () => ({
      cluster_id: numberOrUndef(params.get("cluster_id")),
      status: (params.get("status") as JobStatus | null) ?? undefined,
      limit: 100,
    }),
    [params],
  );

  const { data: clusters } = useClusters();
  const jobs = useJobs(filters);
  const schedules = useBackupSchedules();

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
          <h1 className="text-2xl font-semibold tracking-tight">Safe Ops · Jobs</h1>
          <p className="text-sm text-muted-foreground">
            Allowed kinds: {JOB_KINDS.join(", ")}. Restore and stanza-delete are
            blocked at both API and agent layers.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => jobs.refetch()}>
            <RefreshCw className="h-4 w-4" /> Refresh
          </Button>
          <Button
            onClick={() => setOpenSubmit("one_off")}
            disabled={!isAdmin}
            title={isAdmin ? "" : "Admin role required to submit jobs"}
          >
            <Plus className="h-4 w-4" /> New job
          </Button>
        </div>
      </header>

      <Card>
        <CardContent className="grid gap-3 p-4 sm:grid-cols-3">
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
            value={filters.status ?? ""}
            onChange={(e) => patch({ status: e.target.value })}
            aria-label="Status"
          >
            <option value="">Any status</option>
            <option value="pending">pending</option>
            <option value="running">running</option>
            <option value="succeeded">succeeded</option>
            <option value="failed">failed</option>
          </Select>
        </CardContent>
      </Card>

      <BackupSchedulesCard
        schedules={schedules.data ?? []}
        isLoading={schedules.isLoading}
        isError={schedules.isError}
        error={schedules.error as Error | null}
        clusters={clusters ?? []}
        isAdmin={isAdmin}
      />

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-sm">
            Jobs {jobs.data && (
              <span className="ml-2 text-muted-foreground">
                ({jobs.data.length} shown)
              </span>
            )}
          </CardTitle>
          {jobs.isFetching && <Spinner />}
        </CardHeader>
        <CardContent className="p-0">
          {jobs.isError && (
            <div className="flex items-center gap-2 p-4 text-sm text-destructive">
              <AlertTriangle className="h-4 w-4" />
              {(jobs.error as Error).message}
            </div>
          )}
          {jobs.data && jobs.data.length === 0 && (
            <p className="p-6 text-center text-sm text-muted-foreground">
              No jobs match these filters yet.
            </p>
          )}
          {jobs.data && jobs.data.length > 0 && (
            <JobTable
              jobs={jobs.data}
              expandedId={expandedId}
              onToggle={(id) => setExpandedId((prev) => (prev === id ? null : id))}
            />
          )}
        </CardContent>
      </Card>

      <SubmitJobDialog
        open={openSubmit !== null}
        initialMode={openSubmit ?? "one_off"}
        onClose={() => setOpenSubmit(null)}
      />
    </div>
  );
}

// ---------- Backup schedules section ----------

function BackupSchedulesCard({
  schedules,
  isLoading,
  isError,
  error,
  clusters,
  isAdmin,
}: {
  schedules: BackupSchedule[];
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  clusters: ClusterSummary[];
  isAdmin: boolean;
}) {
  const update = useUpdateBackupSchedule();
  const remove = useDeleteBackupSchedule();
  const clusterById = useMemo(() => {
    const map = new Map<number, ClusterSummary>();
    for (const c of clusters) map.set(c.id, c);
    return map;
  }, [clusters]);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2 text-sm">
          <CalendarClock className="h-4 w-4 text-muted-foreground" />
          Backup schedules
          <span className="ml-2 text-muted-foreground">
            ({schedules.length} configured)
          </span>
        </CardTitle>
        {isLoading && <Spinner />}
      </CardHeader>
      <CardContent className="p-0">
        {isError && (
          <div className="flex items-center gap-2 p-4 text-sm text-destructive">
            <AlertTriangle className="h-4 w-4" />
            {error?.message ?? "Failed to load schedules"}
          </div>
        )}
        {!isError && schedules.length === 0 && (
          <p className="p-6 text-center text-sm text-muted-foreground">
            No backup schedules yet. Use{" "}
            <span className="font-medium">New job → Recurring schedule</span>{" "}
            to create one.
          </p>
        )}
        {schedules.length > 0 && (
          <div className="scroll-thin overflow-auto">
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0 z-10 bg-card">
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="px-3 py-2 font-medium">#</th>
                  <th className="px-3 py-2 font-medium">Cluster</th>
                  <th className="px-3 py-2 font-medium">Kind</th>
                  <th className="px-3 py-2 font-medium">Cron (UTC)</th>
                  <th className="px-3 py-2 font-medium">Next run</th>
                  <th className="px-3 py-2 font-medium">Last run</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {schedules.map((s) => {
                  const cluster = clusterById.get(s.cluster_id);
                  const isPending =
                    (update.isPending && update.variables?.id === s.id) ||
                    (remove.isPending && remove.variables === s.id);
                  return (
                    <tr
                      key={s.id}
                      className="border-b border-border/60 hover:bg-muted/40"
                    >
                      <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                        {s.id}
                      </td>
                      <td className="px-3 py-2">
                        {cluster?.name ?? `cluster #${s.cluster_id}`}
                      </td>
                      <td className="px-3 py-2">
                        <Badge tone="muted">{s.kind}</Badge>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">
                        {s.cron_expression}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-xs">
                        {s.enabled ? (
                          <>
                            <div>{formatUtc(s.next_run_at)}</div>
                            <div className="text-muted-foreground">
                              {formatRelativeFuture(s.next_run_at)}
                            </div>
                          </>
                        ) : (
                          <span className="text-muted-foreground">paused</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-xs">
                        {s.last_run_at ? (
                          <>
                            <div>{formatUtc(s.last_run_at)}</div>
                            <div className="text-muted-foreground">
                              {s.last_job_id != null
                                ? `job #${s.last_job_id}`
                                : "—"}
                            </div>
                          </>
                        ) : (
                          <span className="text-muted-foreground">never</span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        {s.enabled ? (
                          <Badge tone="success">enabled</Badge>
                        ) : (
                          <Badge tone="warning">paused</Badge>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={!isAdmin || isPending}
                            title={
                              !isAdmin
                                ? "Admin role required"
                                : s.enabled
                                  ? "Pause schedule"
                                  : "Resume schedule"
                            }
                            onClick={() =>
                              update.mutate({
                                id: s.id,
                                patch: { enabled: !s.enabled },
                              })
                            }
                          >
                            {s.enabled ? (
                              <Pause className="h-3.5 w-3.5" />
                            ) : (
                              <Play className="h-3.5 w-3.5" />
                            )}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={!isAdmin || isPending}
                            title={
                              isAdmin ? "Delete schedule" : "Admin role required"
                            }
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Delete schedule #${s.id} (${s.kind} on ${
                                    cluster?.name ?? `cluster ${s.cluster_id}`
                                  })?`,
                                )
                              ) {
                                remove.mutate(s.id);
                              }
                            }}
                          >
                            <Trash2 className="h-3.5 w-3.5 text-destructive" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {(update.isError || remove.isError) && (
          <div className="flex items-center gap-2 border-t border-border p-3 text-xs text-destructive">
            <AlertTriangle className="h-3.5 w-3.5" />
            {(update.error as Error | undefined)?.message ??
              (remove.error as Error | undefined)?.message}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------- Jobs table ----------

function JobTable({
  jobs,
  expandedId,
  onToggle,
}: {
  jobs: Job[];
  expandedId: number | null;
  onToggle: (id: number) => void;
}) {
  return (
    <div className="scroll-thin max-h-[70vh] overflow-auto">
      <table className="w-full border-collapse text-sm">
        <thead className="sticky top-0 z-10 bg-card">
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="px-3 py-2 font-medium">#</th>
            <th className="px-3 py-2 font-medium">Kind</th>
            <th className="px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium">Agent</th>
            <th className="px-3 py-2 font-medium">Created (UTC)</th>
            <th className="px-3 py-2 font-medium">Duration</th>
            <th className="px-3 py-2 font-medium">Exit</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => {
            const isOpen = expandedId === job.id;
            return (
              <Fragment key={job.id}>
                <tr
                  className="cursor-pointer border-b border-border/60 hover:bg-muted/40"
                  onClick={() => onToggle(job.id)}
                >
                  <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                    {job.id}
                  </td>
                  <td className="px-3 py-2">
                    <Badge tone="muted">{job.kind}</Badge>
                  </td>
                  <td className="px-3 py-2">
                    <StatusBadge status={job.status} />
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">#{job.agent_id}</td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">
                    {formatUtc(job.created_at)}
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">
                    {duration(job)}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {job.exit_code ?? "—"}
                  </td>
                </tr>
                {isOpen && (
                  <tr className="border-b border-border/60">
                    <td colSpan={7} className="bg-muted/20 px-3 py-3">
                      <JobDetails job={job} />
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

function StatusBadge({ status }: { status: JobStatus }) {
  if (status === "succeeded")
    return (
      <Badge tone="success">
        <CheckCircle2 className="h-3 w-3" /> succeeded
      </Badge>
    );
  if (status === "failed")
    return (
      <Badge tone="destructive">
        <XCircle className="h-3 w-3" /> failed
      </Badge>
    );
  if (status === "running")
    return (
      <Badge tone="primary">
        <Play className="h-3 w-3" /> running
      </Badge>
    );
  return (
    <Badge tone="warning">
      <Clock className="h-3 w-3" /> pending
    </Badge>
  );
}

function JobDetails({ job }: { job: Job }) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <div className="space-y-1 text-xs">
        <div className="text-muted-foreground">Params</div>
        <pre className="scroll-thin max-h-40 overflow-auto rounded-md border border-border bg-background p-2 font-mono text-[11px]">
          {JSON.stringify(job.params, null, 2)}
        </pre>
        <div className="grid grid-cols-2 gap-y-1 pt-2">
          <span className="text-muted-foreground">Started</span>
          <span>{formatUtc(job.started_at)}</span>
          <span className="text-muted-foreground">Finished</span>
          <span>{formatUtc(job.finished_at)}</span>
          <span className="text-muted-foreground">Requested by</span>
          <span>{job.requested_by != null ? `user #${job.requested_by}` : "scheduler"}</span>
        </div>
      </div>
      <div className="space-y-1 text-xs">
        <div className="text-muted-foreground">Stdout tail</div>
        <pre className="scroll-thin max-h-60 overflow-auto rounded-md border border-border bg-background p-2 font-mono text-[11px]">
          {job.stdout_tail || "(no output yet)"}
        </pre>
      </div>
    </div>
  );
}

// ---------- Submit dialog (one-off + recurring) ----------

function SubmitJobDialog({
  open,
  initialMode,
  onClose,
}: {
  open: boolean;
  initialMode: SubmitMode;
  onClose: () => void;
}) {
  const { data: clusters } = useClusters();
  const create = useCreateJob();
  const createSchedule = useCreateBackupSchedule();
  const [mode, setMode] = useState<SubmitMode>(initialMode);
  const [kind, setKind] = useState<JobKind>("backup_incr");
  const [clusterId, setClusterId] = useState<string>("");
  const [stanza, setStanza] = useState("");
  const [cron, setCron] = useState<string>(CRON_PRESETS[0].expression);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset internal state whenever the dialog re-opens (or the entry mode
  // changes) so a stale draft doesn't leak between sessions. Mutations
  // are reset too so a previous error doesn't flash on the next open.
  useEffect(() => {
    if (!open) return;
    setMode(initialMode);
    setKind("backup_incr");
    setClusterId("");
    setStanza("");
    setCron(CRON_PRESETS[0].expression);
    setConfirm(false);
    setError(null);
    create.reset();
    createSchedule.reset();
    // We intentionally don't depend on the mutation refs — they're
    // stable across renders and including them would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialMode]);

  function close() {
    onClose();
  }

  // Schedules can only fire backup kinds. If the operator flips into
  // schedule mode while a non-backup kind is selected, snap it back to
  // a sensible default so the form can't enter an invalid state.
  const availableKinds: readonly JobKind[] =
    mode === "schedule" ? BACKUP_SCHEDULE_KINDS : JOB_KINDS;
  const effectiveKind: JobKind =
    mode === "schedule" && !BACKUP_SCHEDULE_KINDS.includes(kind as BackupScheduleKind)
      ? "backup_incr"
      : kind;

  const isPending = create.isPending || createSchedule.isPending;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!confirm) {
      setError("Please tick the confirmation checkbox.");
      return;
    }
    if (!clusterId) {
      setError("Pick a cluster.");
      return;
    }
    try {
      if (mode === "schedule") {
        if (!cron.trim()) {
          setError("Cron expression is required.");
          return;
        }
        await createSchedule.mutateAsync({
          cluster_id: Number(clusterId),
          kind: effectiveKind as BackupScheduleKind,
          cron_expression: cron.trim(),
          params: stanza ? { stanza } : {},
          enabled: true,
        });
      } else {
        await create.mutateAsync({
          kind: effectiveKind,
          cluster_id: Number(clusterId),
          params: stanza ? { stanza } : {},
        });
      }
      close();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title={mode === "schedule" ? "New backup schedule" : "Submit job"}
      description={
        mode === "schedule"
          ? "The manager will queue a job for this cluster every time the cron expression matches (UTC)."
          : "The selected cluster's primary agent will execute this on its next long-poll."
      }
      footer={
        <>
          <Button variant="ghost" onClick={close} type="button" disabled={isPending}>
            Cancel
          </Button>
          <Button
            type="submit"
            form="job-form"
            disabled={isPending || !confirm || !clusterId}
          >
            {isPending
              ? "Submitting…"
              : mode === "schedule"
                ? "Create schedule"
                : "Submit"}
          </Button>
        </>
      }
    >
      <form id="job-form" onSubmit={onSubmit} className="space-y-3">
        <ModeToggle mode={mode} onChange={setMode} />

        <Field label="Cluster">
          <Select
            value={clusterId}
            onChange={(e) => setClusterId(e.target.value)}
            required
          >
            <option value="">Select…</option>
            {clusters?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Kind">
          <Select
            value={effectiveKind}
            onChange={(e) => setKind(e.target.value as JobKind)}
          >
            {availableKinds.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </Select>
          {mode === "schedule" && (
            <p className="text-[11px] text-muted-foreground">
              Schedules only fire backups. <code>check</code> and{" "}
              <code>stanza_create</code> stay one-off.
            </p>
          )}
        </Field>

        <Field label="Stanza (optional)">
          <Input
            value={stanza}
            onChange={(e) => setStanza(e.target.value)}
            placeholder="leave blank to use agent default"
          />
        </Field>

        {mode === "schedule" && (
          <Field label="Cron expression (UTC)">
            <Input
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              placeholder="m h dom mon dow"
              required
              spellCheck={false}
              className="font-mono"
            />
            <div className="flex flex-wrap gap-1 pt-1">
              {CRON_PRESETS.map((p) => (
                <button
                  key={p.expression}
                  type="button"
                  onClick={() => setCron(p.expression)}
                  className="rounded-md border border-border bg-muted/30 px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted"
                >
                  {p.label}
                </button>
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground">
              5-field POSIX cron (<code>min hour dom mon dow</code>), evaluated
              in UTC. The scheduler ticks once a minute.
            </p>
          </Field>
        )}

        <label className="flex items-start gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={confirm}
            onChange={(e) => setConfirm(e.target.checked)}
            className="mt-0.5 h-4 w-4 accent-primary"
          />
          <span>
            {mode === "schedule" ? (
              <>
                I understand the manager will run <code>{effectiveKind}</code>{" "}
                against the selected cluster on every cron match.
              </>
            ) : (
              <>
                I understand this will run <code>{effectiveKind}</code> against
                the selected cluster's primary agent. (v1 has no "type cluster
                name to confirm" modal — see <code>docs/safety-and-rbac.md</code>
                .)
              </>
            )}
          </span>
        </label>

        {error && (
          <p className="text-xs text-destructive" role="alert">
            {error}
          </p>
        )}
      </form>
    </Dialog>
  );
}

function ModeToggle({
  mode,
  onChange,
}: {
  mode: SubmitMode;
  onChange: (next: SubmitMode) => void;
}) {
  return (
    <div className="inline-flex rounded-md border border-border bg-muted/30 p-0.5 text-xs">
      <ModeButton
        active={mode === "one_off"}
        onClick={() => onChange("one_off")}
      >
        One-off job
      </ModeButton>
      <ModeButton
        active={mode === "schedule"}
        onClick={() => onChange("schedule")}
      >
        <CalendarClock className="h-3.5 w-3.5" /> Recurring schedule
      </ModeButton>
    </div>
  );
}

function ModeButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "inline-flex items-center gap-1 rounded-sm px-2.5 py-1 transition-colors " +
        (active
          ? "bg-card text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground")
      }
    >
      {children}
    </button>
  );
}

// ---------- Field + helpers ----------

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="text-xs font-medium text-muted-foreground">{label}</label>
      {children}
    </div>
  );
}

function duration(job: Job): string {
  if (!job.started_at) return formatRelative(job.created_at);
  const start = new Date(job.started_at).getTime();
  const end = job.finished_at ? new Date(job.finished_at).getTime() : Date.now();
  const sec = Math.max(0, Math.round((end - start) / 1000));
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  return `${(sec / 3600).toFixed(1)}h`;
}

function formatRelativeFuture(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diffMs = d.getTime() - Date.now();
  if (diffMs <= 0) return "due now";
  const sec = Math.round(diffMs / 1000);
  if (sec < 60) return `in ${sec}s`;
  const min = Math.round(sec / 60);
  if (min < 60) return `in ${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `in ${hr}h`;
  const day = Math.round(hr / 24);
  return `in ${day}d`;
}

function numberOrUndef(v: string | null): number | undefined {
  if (!v) return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}
