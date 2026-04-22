"""Agent-side job runner — Safe Ops (PLAN §6 / P6).

Loop forever:
    1. Long-poll ``/api/v1/agents/jobs/next``.
    2. If a job arrives, dispatch on its kind to a ``pgbackrest`` invocation.
    3. Capture stdout (combined with stderr), exit code, then POST to
       ``/api/v1/agents/jobs/{id}/result``.

# Defense-in-depth allowlist
The manager already validates ``kind`` against the same allowlist on the
API side (``schemas.JOB_KINDS``); we duplicate it here so a buggy or
malicious manager build cannot trick an agent into running ``restore``
or ``stanza-delete``. The list MUST stay in sync with
``manager/pct_manager/schemas.py::JOB_KINDS``. If you ever wire up
destructive ops, change BOTH places at once and update
``docs/safety-and-rbac.md``.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Any

import httpx

from .config import AgentSettings
from .manager_client import ManagerClient
from .pt_stalk import (
    PtStalkConfigError,
    build_pt_stalk_cmd,
    merged_env,
    tar_run_dir,
)

log = logging.getLogger("pct_agent.runner")

# Mirror of manager `schemas.JOB_KINDS`. Keep in sync.
ALLOWED_KINDS: frozenset[str] = frozenset(
    {
        "backup_full",
        "backup_diff",
        "backup_incr",
        "check",
        "stanza_create",
        # Read-only diagnostic snapshot — see pt_stalk.py.
        "pt_stalk_collect",
    }
)

# Map a job ``kind`` to the pgBackRest sub-command it produces.
_PGBACKREST_KIND_TO_ARGS: dict[str, list[str]] = {
    "backup_full": ["backup", "--type=full"],
    "backup_diff": ["backup", "--type=diff"],
    "backup_incr": ["backup", "--type=incr"],
    "check": ["check"],
    "stanza_create": ["stanza-create"],
}


async def runner_loop(
    settings: AgentSettings,
    client: ManagerClient,
) -> None:
    """Main runner loop. Cancellation-safe: cancelling the task aborts
    the long-poll cleanly and stops on the next iteration."""
    if settings.runner_long_poll_seconds <= 0:
        log.info("Job runner disabled (runner_long_poll_seconds=0).")
        return

    log.info(
        "Job runner started. long_poll=%ss timeout=%ss allowlist=%s",
        settings.runner_long_poll_seconds,
        settings.runner_job_timeout_seconds,
        sorted(ALLOWED_KINDS),
    )
    while True:
        try:
            claim = await _claim_one(client, settings.runner_long_poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # Network blips, 5xx, etc. Sleep briefly to avoid hot-looping.
            log.exception("Job claim failed; backing off.")
            await asyncio.sleep(5.0)
            continue

        if claim is None:
            # 204 — no work; loop immediately to long-poll again.
            continue

        await _run_and_report(settings, client, claim)


async def _claim_one(
    client: ManagerClient, wait_seconds: int
) -> dict[str, Any] | None:
    """One long-poll round. Returns the claimed job dict or None on 204."""
    response = await client.get(
        "/api/v1/agents/jobs/next",
        params={"wait": wait_seconds},
        # Allow generous slack over the server-side cap so the request
        # doesn't time out before the manager returns 204.
        timeout=wait_seconds + 10.0,
    )
    if response.status_code == 204:
        return None
    if response.status_code == 401:
        # ManagerClient already logged; treat like "no work" with a brief
        # backoff to avoid hammering an unauthorized agent.
        await asyncio.sleep(10.0)
        return None
    return response.json()


async def _run_and_report(
    settings: AgentSettings,
    client: ManagerClient,
    claim: dict[str, Any],
) -> None:
    job_id = int(claim["id"])
    kind = str(claim["kind"])
    params = dict(claim.get("params") or {})

    log.info("Claimed job %d kind=%s params=%s", job_id, kind, params)

    if kind not in ALLOWED_KINDS:
        # Should be impossible (manager validates) but defense in depth:
        # report the refusal and move on.
        await _report_result(
            client, job_id,
            exit_code=126,
            succeeded=False,
            stdout_tail=(
                f"Agent refused to run job kind={kind!r}: not in agent allowlist "
                f"({sorted(ALLOWED_KINDS)})."
            ),
        )
        return

    if kind == "pt_stalk_collect":
        await _run_pt_stalk(settings, client, job_id, params)
        return

    await _run_pgbackrest(settings, client, job_id, kind, params)


async def _run_pgbackrest(
    settings: AgentSettings,
    client: ManagerClient,
    job_id: int,
    kind: str,
    params: dict[str, Any],
) -> None:
    cmd = _build_pgbackrest_command(settings, kind, params)
    log.info("Job %d -> %s", job_id, " ".join(shlex.quote(c) for c in cmd))

    try:
        exit_code, output = await _exec(
            cmd, timeout=settings.runner_job_timeout_seconds
        )
    except FileNotFoundError as exc:
        await _report_result(
            client, job_id,
            exit_code=127,
            succeeded=False,
            stdout_tail=f"Executable not found: {exc}",
        )
        return
    except Exception:  # noqa: BLE001
        log.exception("Job %d crashed in runner", job_id)
        await _report_result(
            client, job_id,
            exit_code=1,
            succeeded=False,
            stdout_tail="Runner crashed; see agent logs.",
        )
        return

    tail = output[-settings.runner_stdout_tail_chars :]
    succeeded = exit_code == 0
    await _report_result(
        client, job_id,
        exit_code=exit_code,
        succeeded=succeeded,
        stdout_tail=tail,
    )
    log.info("Reported job %d exit=%s succeeded=%s", job_id, exit_code, succeeded)


async def _run_pt_stalk(
    settings: AgentSettings,
    client: ManagerClient,
    job_id: int,
    params: dict[str, Any],
) -> None:
    """Run a pt-stalk collect job and ship the resulting tarball.

    Failure ordering matters: we always try to upload whatever bundle
    pt-stalk left behind, even on a non-zero exit, so the operator has
    something to look at when the run was unhappy. The result POST is
    sent last so the ``succeeded`` flag reflects both the subprocess
    exit and the tar+upload phases.
    """
    try:
        cmd, env_overrides, dest_dir, _pid, _log_file = build_pt_stalk_cmd(
            settings, params
        )
    except PtStalkConfigError as exc:
        await _report_result(
            client, job_id,
            exit_code=2,
            succeeded=False,
            stdout_tail=f"pt-stalk: invalid params: {exc}",
        )
        return

    log.info(
        "Job %d -> %s",
        job_id,
        " ".join(shlex.quote(c) for c in cmd),
    )

    try:
        exit_code, output = await _exec(
            cmd,
            timeout=settings.pt_stalk_max_runtime_seconds,
            env=merged_env(env_overrides),
        )
    except FileNotFoundError as exc:
        await _report_result(
            client, job_id,
            exit_code=127,
            succeeded=False,
            stdout_tail=f"pt-stalk: executable not found: {exc}",
        )
        return
    except Exception:  # noqa: BLE001
        log.exception("pt-stalk job %d crashed in runner", job_id)
        await _report_result(
            client, job_id,
            exit_code=1,
            succeeded=False,
            stdout_tail="pt-stalk: runner crashed; see agent logs.",
        )
        return

    upload_status = ""
    upload_ok = True
    try:
        tarball = await asyncio.to_thread(tar_run_dir, dest_dir)
        log.info("Job %d tarball ready: %s", job_id, tarball)
        await client.post_file(
            f"/api/v1/agents/jobs/{job_id}/artifact",
            file_path=str(tarball),
            filename=tarball.name,
            content_type="application/gzip",
            timeout=settings.pt_stalk_upload_timeout_seconds,
        )
        upload_status = f"\nUploaded artifact: {tarball.name}"
    except Exception as exc:  # noqa: BLE001
        upload_ok = False
        log.exception("pt-stalk job %d artifact upload failed", job_id)
        upload_status = f"\nArtifact upload FAILED: {exc!s}"

    succeeded = exit_code == 0 and upload_ok
    tail_room = settings.runner_stdout_tail_chars - len(upload_status)
    if tail_room < 0:
        tail_room = 0
    tail = output[-tail_room:] + upload_status if tail_room else upload_status
    await _report_result(
        client, job_id,
        exit_code=exit_code,
        succeeded=succeeded,
        stdout_tail=tail,
    )
    log.info(
        "Reported pt-stalk job %d exit=%s upload_ok=%s succeeded=%s",
        job_id, exit_code, upload_ok, succeeded,
    )


def _build_pgbackrest_command(
    settings: AgentSettings, kind: str, params: dict[str, Any]
) -> list[str]:
    """Assemble the pgBackRest CLI invocation for a given job kind.

    ``params`` may contain:
    - ``stanza``: explicit stanza name; otherwise we use
      ``settings.pgbackrest_stanza`` if set.
    - ``extra_args``: list[str] of additional flags appended verbatim
      (validated only loosely — the operator is admin-gated server-side).
    """
    base_args = list(_PGBACKREST_KIND_TO_ARGS[kind])
    cmd: list[str] = [settings.pgbackrest_bin, *base_args]

    stanza = params.get("stanza") or settings.pgbackrest_stanza
    if stanza:
        cmd.append(f"--stanza={stanza}")

    extra = params.get("extra_args")
    if isinstance(extra, list):
        for arg in extra:
            if not isinstance(arg, str):
                # Skip silently — the manager API has already typed params
                # but this is the last line of defense.
                continue
            cmd.append(arg)
    return cmd


async def _exec(
    cmd: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run ``cmd`` and return (exit_code, combined_stdout_stderr).

    We merge stderr into stdout because pgBackRest writes most progress to
    stderr and we want a single chronological tail in the UI.
    """
    kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.STDOUT,
    }
    if env is not None:
        kwargs["env"] = env
    proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"Job exceeded timeout of {int(timeout)}s; killed."
    return proc.returncode or 0, stdout_bytes.decode("utf-8", errors="replace")


async def _report_result(
    client: ManagerClient,
    job_id: int,
    *,
    exit_code: int,
    succeeded: bool,
    stdout_tail: str | None,
) -> None:
    body: dict[str, Any] = {
        "exit_code": exit_code,
        "succeeded": succeeded,
        "stdout_tail": stdout_tail,
    }
    try:
        await client.post(f"/api/v1/agents/jobs/{job_id}/result", json=body)
    except httpx.HTTPStatusError as exc:
        # 409 means the manager doesn't think the job is running anymore
        # (e.g. it was retried, or the agent restarted mid-job). Log it
        # and move on; nothing the runner can do.
        log.warning(
            "Manager refused result for job %d: %s", job_id, exc.response.text
        )
    except Exception:  # noqa: BLE001
        log.exception("Failed to POST result for job %d", job_id)
