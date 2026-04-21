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

log = logging.getLogger("pct_agent.runner")

# Mirror of manager `schemas.JOB_KINDS`. Keep in sync.
ALLOWED_KINDS: frozenset[str] = frozenset(
    {
        "backup_full",
        "backup_diff",
        "backup_incr",
        "check",
        "stanza_create",
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

    cmd = _build_command(settings, kind, params)
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


def _build_command(
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


async def _exec(cmd: list[str], *, timeout: float) -> tuple[int, str]:
    """Run ``cmd`` and return (exit_code, combined_stdout_stderr).

    We merge stderr into stdout because pgBackRest writes most progress to
    stderr and we want a single chronological tail in the UI.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
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
