"""WAL health collector.

Probes the local Postgres on a fixed cadence and ships a small structured
summary to the manager:

- ``last_archived_wal``      from ``pg_stat_archiver.last_archived_wal``
- ``archive_lag_seconds``    ``EXTRACT(EPOCH FROM now() - last_archived_time)``
- ``gap_detected``           true when an archive failure happened more
                             recently than the last success
- ``role``                   ``'primary' | 'replica'`` from
                             ``pg_is_in_recovery()``

The collector runs synchronously inside ``asyncio.to_thread`` so a slow DB
doesn't stall the event loop. If ``pg_dsn`` is empty the collector logs
once and exits — no Postgres, no WAL probe.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import psycopg

from ..config import AgentSettings
from ..manager_client import ManagerClient
from ..runtime_state import AgentRuntimeState

logger = logging.getLogger(__name__)


_WAL_QUERY = """
SELECT
    pg_is_in_recovery() AS in_recovery,
    s.last_archived_wal,
    s.last_archived_time,
    s.last_failed_time,
    EXTRACT(EPOCH FROM (now() - s.last_archived_time))::int
        AS archive_lag_seconds
FROM pg_stat_archiver s
"""


async def wal_loop(
    settings: AgentSettings,
    client: ManagerClient,
    runtime_state: AgentRuntimeState,
    interval_seconds: int | None = None,
) -> None:
    if not settings.pg_dsn:
        logger.warning(
            "PCT_AGENT_PG_DSN is empty; WAL collector disabled. "
            "Set it to a libpq DSN (e.g. 'host=/var/run/postgresql user=postgres dbname=postgres')."
        )
        return

    interval = interval_seconds or settings.wal_interval
    logger.info("Starting WAL collector: every %ss", interval)
    while True:
        try:
            sample = await asyncio.to_thread(_probe_pg, settings.pg_dsn)
        except asyncio.CancelledError:
            logger.info("WAL collector cancelled; exiting.")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("WAL probe failed; will retry")
            await asyncio.sleep(interval)
            continue

        # Update the in-process role cache so the heartbeat reports the
        # correct value even when the Patroni collector is disabled (e.g.
        # standalone agents). Patroni signals always win over WAL — see
        # ``runtime_state._SOURCE_RANK``.
        probed_role = sample.get("role")
        if isinstance(probed_role, str):
            runtime_state.update_role(probed_role, "wal")

        try:
            await client.post("/api/v1/agents/wal_health", json=sample)
            logger.debug("Shipped wal_health: %s", sample)
        except Exception:  # noqa: BLE001
            logger.exception("WAL health POST failed; will retry next tick")

        await asyncio.sleep(interval)


def _probe_pg(dsn: str) -> dict[str, Any]:
    """Synchronous Postgres probe; intended to be called via ``to_thread``."""
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(_WAL_QUERY)
            row = cur.fetchone()

    if row is None:
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "last_archived_wal": None,
            "archive_lag_seconds": None,
            "gap_detected": False,
            "role": "unknown",
        }

    in_recovery, last_archived_wal, last_archived_time, last_failed_time, lag = row
    role = "replica" if in_recovery else "primary"

    # Gap heuristic: a failure newer than the last success means archiving
    # is currently broken. If we have only failures, also count it.
    gap_detected = False
    if last_failed_time is not None:
        gap_detected = (
            last_archived_time is None or last_failed_time > last_archived_time
        )

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "last_archived_wal": last_archived_wal,
        "archive_lag_seconds": int(lag) if lag is not None else None,
        "gap_detected": bool(gap_detected),
        "role": role,
    }
