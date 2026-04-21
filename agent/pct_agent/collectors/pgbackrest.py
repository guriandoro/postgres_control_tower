"""pgBackRest snapshot collector.

Runs ``pgbackrest --output=json info`` on a fixed cadence (default 60s) and
POSTs the parsed JSON to the manager. We do not interpret the payload here:
it lands as JSONB so the manager / UI can pick out fields without an ORM
round trip.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from ..config import AgentSettings
from ..manager_client import ManagerClient

logger = logging.getLogger(__name__)


async def pgbackrest_loop(
    settings: AgentSettings,
    client: ManagerClient,
    interval_seconds: int | None = None,
) -> None:
    interval = interval_seconds or settings.pgbackrest_interval
    logger.info(
        "Starting pgBackRest collector: bin=%s stanza=%s every %ss",
        settings.pgbackrest_bin,
        settings.pgbackrest_stanza or "<all>",
        interval,
    )
    while True:
        try:
            await _collect_once(settings, client)
        except asyncio.CancelledError:
            logger.info("pgBackRest collector cancelled; exiting.")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("pgBackRest collector tick failed; will retry")
        await asyncio.sleep(interval)


async def _collect_once(settings: AgentSettings, client: ManagerClient) -> None:
    cmd = [settings.pgbackrest_bin, "--output=json"]
    if settings.pgbackrest_stanza:
        cmd.append(f"--stanza={settings.pgbackrest_stanza}")
    cmd.append("info")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    # pgbackrest exits 0 even when "no stanzas" — the JSON body still
    # contains useful structure. Anything else is a real error worth logging.
    if proc.returncode != 0:
        logger.warning(
            "pgbackrest exited %s: %s",
            proc.returncode,
            stderr.decode("utf-8", errors="replace").strip(),
        )
        return

    try:
        payload = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError:
        logger.exception("pgbackrest output was not valid JSON; skipping")
        return

    body = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    await client.post("/api/v1/agents/pgbackrest_info", json=body)
    logger.debug("Shipped pgbackrest info: %d stanza(s)",
                 len(payload) if isinstance(payload, list) else 0)
