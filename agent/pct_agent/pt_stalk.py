"""pt-stalk PostgreSQL collect — command builder + bundle helpers.

The agent's job runner dispatches the ``pt_stalk_collect`` Safe Ops
kind to :func:`build_pt_stalk_cmd`. After the subprocess finishes,
the runner tar-gzips the freshly created run directory via
:func:`tar_run_dir` and POSTs the resulting tarball to the manager
(see :mod:`pct_agent.runner`).

Connection params come from the agent's existing ``pg_dsn`` (parsed
with libpq) so operators don't have to enter credentials in the UI.
The password — if any — is exported as ``PGPASSWORD`` on the
subprocess environment so we never spawn pt-stalk in an interactive
``--pg-ask-pass`` mode.
"""

from __future__ import annotations

import logging
import os
import tarfile
import time
from pathlib import Path
from typing import Any

import psycopg.conninfo

from .config import AgentSettings

logger = logging.getLogger("pct_agent.pt_stalk")


_DEFAULT_RUN_TIME_SECONDS = 30
_DEFAULT_ITERATIONS = 1
# Both caps mirror what's reasonable for a single ad-hoc snapshot. The
# manager API doesn't enforce them; that's intentional — defense in
# depth lives here, in the agent that actually executes the command.
_MAX_RUN_TIME_SECONDS = 3600
_MAX_ITERATIONS = 60


class PtStalkConfigError(ValueError):
    """Raised when settings or params can't produce a valid pt-stalk run."""


def parse_pg_dsn(dsn: str) -> dict[str, str]:
    """Parse a libpq DSN into a flat ``{key: str}`` map.

    Both URL form (``postgresql://user:pw@host:5432/db``) and keyword
    form (``host=... user=... dbname=...``) are accepted, mirroring how
    the WAL collector consumes ``pg_dsn``. Empty input yields an empty
    dict so callers can fall back to defaults.
    """
    if not dsn or not dsn.strip():
        return {}
    return psycopg.conninfo.conninfo_to_dict(dsn)


def build_pt_stalk_cmd(
    settings: AgentSettings,
    params: dict[str, Any],
    *,
    now: float | None = None,
) -> tuple[list[str], dict[str, str], Path, Path, Path]:
    """Assemble the pt-stalk invocation for a ``pt_stalk_collect`` job.

    Returns ``(cmd, env_overrides, dest_dir, pid_file, log_file)``:

    * ``cmd`` — the argv to pass to ``asyncio.create_subprocess_exec``.
    * ``env_overrides`` — additions to ``os.environ`` for the subprocess
      (mainly ``PGPASSWORD`` and optionally ``PT_STALK_GATHER_SQL``).
    * ``dest_dir`` — the unique directory pt-stalk will write to. The
      runner tarballs this after the subprocess exits.
    * ``pid_file`` / ``log_file`` — paths the runner can clean up.

    Honored ``params`` keys (all optional):

    * ``run_time_seconds`` (int, default 30, capped at 3600)
    * ``iterations`` (int, default 1, capped at 60)
    * ``database`` (str) — overrides the DSN's dbname; defaults to
      ``postgres`` if the DSN doesn't specify one either.
    """
    pg = parse_pg_dsn(settings.pg_dsn)

    run_time = _coerce_int(
        params.get("run_time_seconds"), _DEFAULT_RUN_TIME_SECONDS, "run_time_seconds"
    )
    iterations = _coerce_int(
        params.get("iterations"), _DEFAULT_ITERATIONS, "iterations"
    )
    if not 1 <= run_time <= _MAX_RUN_TIME_SECONDS:
        raise PtStalkConfigError(
            f"run_time_seconds must be 1..{_MAX_RUN_TIME_SECONDS}; got {run_time}"
        )
    if not 1 <= iterations <= _MAX_ITERATIONS:
        raise PtStalkConfigError(
            f"iterations must be 1..{_MAX_ITERATIONS}; got {iterations}"
        )

    host = pg.get("host") or pg.get("hostaddr") or "127.0.0.1"
    user = pg.get("user") or "postgres"
    database = (
        params.get("database")
        or pg.get("dbname")
        or "postgres"
    )
    if not isinstance(database, str) or not database:
        raise PtStalkConfigError("database param must be a non-empty string")

    timestamp = int(now if now is not None else time.time())
    dest_root = Path(settings.pt_stalk_dest_dir)
    dest_root.mkdir(parents=True, exist_ok=True)
    # pt-stalk creates files directly under --dest, prefixed with a
    # timestamp. We give every job its own subdirectory so concurrent
    # jobs don't entangle their files and so tarring is trivial.
    dest_dir = dest_root / f"pct-{timestamp}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    pid_file = dest_dir / "pt-stalk.pid"
    log_file = dest_dir / "pt-stalk.log"

    cmd: list[str] = [
        settings.pt_stalk_bin,
        "--pgsql",
        "--no-stalk",
        "--collect",
        "--iterations", str(iterations),
        "--run-time", str(run_time),
        "--pg-host", host,
        "--pg-user", user,
        "--pg-database", database,
        "--dest", str(dest_dir),
        "--pid", str(pid_file),
        "--log", str(log_file),
    ]
    port = pg.get("port")
    if port:
        cmd.extend(["--pg-port", str(port)])

    env_overrides: dict[str, str] = {}
    password = settings.pt_stalk_pg_password or pg.get("password") or ""
    if password:
        env_overrides["PGPASSWORD"] = password
    if settings.pt_stalk_gather_sql_path:
        env_overrides["PT_STALK_GATHER_SQL"] = settings.pt_stalk_gather_sql_path

    return cmd, env_overrides, dest_dir, pid_file, log_file


def tar_run_dir(dest_dir: Path) -> Path:
    """Tar+gzip ``dest_dir`` to ``<dest_dir>.tgz`` and return the path.

    Files inside the tarball are anchored on the basename of
    ``dest_dir`` so an operator who extracts the bundle gets a single
    top-level folder rather than a flat dump. Existing tarballs are
    overwritten (the runner only calls this once per job).
    """
    if not dest_dir.is_dir():
        raise FileNotFoundError(f"pt-stalk dest_dir does not exist: {dest_dir}")
    tarball = dest_dir.with_suffix(dest_dir.suffix + ".tgz")
    if tarball.exists():
        tarball.unlink()
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(dest_dir, arcname=dest_dir.name)
    return tarball


def merged_env(env_overrides: dict[str, str]) -> dict[str, str]:
    """Build the full env passed to the subprocess.

    We start from the agent's own environment (so PATH/locale/etc.
    propagate) and only layer the overrides on top so we never leak
    PGPASSWORD into a process that didn't ask for it.
    """
    env = dict(os.environ)
    env.update(env_overrides)
    return env


def _coerce_int(value: Any, default: int, name: str) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PtStalkConfigError(f"{name} must be an integer") from exc
