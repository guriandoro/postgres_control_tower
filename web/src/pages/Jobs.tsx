import { Fragment, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Clock, Play, Plus, RefreshCw, XCircle } from "lucide-react";
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
import { useAuth } from "@/auth/AuthContext";
import { JOB_KINDS, type Job, type JobKind, type JobStatus } from "@/api/types";
import { ApiError } from "@/api/client";
import { formatRelative, formatUtc } from "@/lib/format";

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const [openSubmit, setOpenSubmit] = useState(false);
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
            onClick={() => setOpenSubmit(true)}
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
        open={openSubmit}
        onClose={() => setOpenSubmit(false)}
      />
    </div>
  );
}

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
          <span>{job.requested_by != null ? `user #${job.requested_by}` : "—"}</span>
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

function SubmitJobDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { data: clusters } = useClusters();
  const create = useCreateJob();
  const [kind, setKind] = useState<JobKind>("backup_incr");
  const [clusterId, setClusterId] = useState<string>("");
  const [stanza, setStanza] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setKind("backup_incr");
    setClusterId("");
    setStanza("");
    setConfirm(false);
    setError(null);
    create.reset();
  }

  function close() {
    reset();
    onClose();
  }

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
      await create.mutateAsync({
        kind,
        cluster_id: Number(clusterId),
        params: stanza ? { stanza } : {},
      });
      close();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Submit job"
      description="The selected cluster's primary agent will execute this on its next long-poll."
      footer={
        <>
          <Button variant="ghost" onClick={close} type="button" disabled={create.isPending}>
            Cancel
          </Button>
          <Button
            type="submit"
            form="job-form"
            disabled={create.isPending || !confirm || !clusterId}
          >
            {create.isPending ? "Submitting…" : "Submit"}
          </Button>
        </>
      }
    >
      <form id="job-form" onSubmit={onSubmit} className="space-y-3">
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
          <Select value={kind} onChange={(e) => setKind(e.target.value as JobKind)}>
            {JOB_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Stanza (optional)">
          <Input
            value={stanza}
            onChange={(e) => setStanza(e.target.value)}
            placeholder="leave blank to use agent default"
          />
        </Field>
        <label className="flex items-start gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={confirm}
            onChange={(e) => setConfirm(e.target.checked)}
            className="mt-0.5 h-4 w-4 accent-primary"
          />
          <span>
            I understand this will run <code>{kind}</code> against the selected
            cluster's primary agent. (v1 has no "type cluster name to confirm"
            modal — see <code>docs/safety-and-rbac.md</code>.)
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

function numberOrUndef(v: string | null): number | undefined {
  if (!v) return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}
