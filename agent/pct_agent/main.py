"""Agent diagnostic HTTP server. Bound to localhost only; intended for
operator introspection (e.g. ``curl http://127.0.0.1:8081/healthz``).

Background work (heartbeat in P2; collectors / shipper / runner in P3+) is
started from the FastAPI ``lifespan`` so it shares the same event loop.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from datetime import datetime
from pathlib import Path

from . import __version__
from .collectors.log_files import tail_many
from .collectors.os_logs import os_loop
from .collectors.pgbackrest import pgbackrest_loop
from .collectors.wal import wal_loop
from .config import AgentSettings, AgentState, load_settings
from .heartbeat import heartbeat_loop
from .manager_client import ManagerClient
from .parsers import (
    parse_etcd_line,
    parse_patroni_line,
    parse_pgbackrest_line,
    parse_postgres_line,
)
from .runner import runner_loop
from .shipper import Shipper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("pct_agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    state = AgentState(settings.state_path).load()

    background_tasks: list[asyncio.Task[None]] = []
    manager_client: ManagerClient | None = None

    agent_token = state.get("agent_token")
    if agent_token:
        manager_url = str(state.get("manager_url") or settings.manager_url)
        manager_client = ManagerClient(manager_url, str(agent_token))

        background_tasks.append(
            asyncio.create_task(
                heartbeat_loop(settings, state, settings.heartbeat_interval),
                name="pct-agent-heartbeat",
            )
        )
        background_tasks.append(
            asyncio.create_task(
                pgbackrest_loop(settings, manager_client),
                name="pct-agent-pgbackrest",
            )
        )
        background_tasks.append(
            asyncio.create_task(
                wal_loop(settings, manager_client),
                name="pct-agent-wal",
            )
        )

        # Log shipper + per-source tailers (P4).
        shipper = Shipper(
            manager_client,
            settings.spool_dir,
            batch_size=settings.shipper_batch_size,
            flush_interval=settings.shipper_flush_interval,
        )
        background_tasks.append(
            asyncio.create_task(shipper.run(), name="pct-agent-shipper")
        )
        background_tasks.extend(_start_log_collectors(settings, shipper))

        # Safe Ops runner (P6) — long-polls /jobs/next and executes
        # allowlisted pgBackRest commands.
        background_tasks.append(
            asyncio.create_task(
                runner_loop(settings, manager_client),
                name="pct-agent-runner",
            )
        )
    else:
        log.warning(
            "Agent is not registered (no token in %s). Diagnostic HTTP will "
            "still serve, but no heartbeats / collectors will run. Run "
            "`pct-agent register` to enroll.",
            settings.state_path,
        )

    log.info("Postgres Control Tower agent v%s started", __version__)
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if manager_client is not None:
            await manager_client.aclose()
        log.info("Postgres Control Tower agent shutting down")


app = FastAPI(
    title="Postgres Control Tower — Agent (local)",
    version=__version__,
    lifespan=lifespan,
)


def _split_paths(spec: str) -> list[Path]:
    return [Path(p.strip()) for p in spec.split(",") if p.strip()]


def _host_tz():  # type: ignore[no-untyped-def]
    """Resolve the host's local tzinfo, falling back to UTC.

    We use ``datetime.now().astimezone().tzinfo`` so the result reflects
    whatever ``/etc/localtime`` is set to inside the container — matching
    what Postgres / pgBackRest write to their log files.
    """
    return datetime.now().astimezone().tzinfo


def _start_log_collectors(
    settings: AgentSettings, shipper: Shipper
) -> list[asyncio.Task[None]]:
    host_tz = _host_tz()
    tasks: list[asyncio.Task[None]] = []

    sources: list[tuple[str, list[Path], object]] = [
        ("postgres",   _split_paths(settings.pg_log_paths),         parse_postgres_line),
        ("pgbackrest", _split_paths(settings.pgbackrest_log_paths), parse_pgbackrest_line),
        ("patroni",    _split_paths(settings.patroni_log_paths),    parse_patroni_line),
        ("etcd",       _split_paths(settings.etcd_log_paths),       parse_etcd_line),
    ]
    for label, paths, parser in sources:
        if not paths:
            log.info("Source %s has no paths configured; skipping.", label)
            continue
        tasks.append(
            asyncio.create_task(
                tail_many(paths, parser, shipper, host_tz, label=label),  # type: ignore[arg-type]
                name=f"pct-agent-tail-{label}",
            )
        )

    tasks.append(
        asyncio.create_task(
            os_loop(shipper, host_tz, extra_paths=_split_paths(settings.os_log_paths)),
            name="pct-agent-os",
        )
    )
    return tasks


@app.get("/healthz")
def healthz() -> dict[str, object]:
    settings = load_settings()
    state = AgentState(settings.state_path).load()
    return {
        "ok": True,
        "version": __version__,
        "registered": "agent_token" in state,
        "manager_url": settings.manager_url,
        "agent_id": state.get("agent_id"),
        "cluster_id": state.get("cluster_id"),
    }
