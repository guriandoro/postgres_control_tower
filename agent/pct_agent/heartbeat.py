"""Agent heartbeat loop.

Posts a small JSON ping to ``POST /api/v1/agents/heartbeat`` on a fixed
interval. The manager replies with its own UTC clock so the agent can log
the locally-observed skew (used for the operator-facing diagnostic and as
the value reported back on the next heartbeat).

The loop is deliberately resilient: any transport or 5xx error is logged
and retried on the next tick. Auth (4xx) failures are also logged but do
not crash the agent — re-running ``pct-agent register`` is the operator
remediation.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime, timezone

import httpx

from . import __version__
from .config import AgentSettings, AgentState

logger = logging.getLogger(__name__)


DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30
"""Cadence for the heartbeat loop. Picked to keep `last_seen_at` fresh
without spamming the manager. Configurable via ``PCT_AGENT_HEARTBEAT_INTERVAL``
(see :class:`AgentSettings` extension)."""

_HEARTBEAT_TIMEOUT_SECONDS = 10.0


async def heartbeat_loop(
    settings: AgentSettings,
    state: dict[str, object],
    interval_seconds: int = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """Run forever, posting heartbeats every ``interval_seconds``.

    ``state`` must contain ``manager_url`` and ``agent_token`` (i.e. the agent
    has been registered). If the state is missing, this coroutine logs a
    warning and returns immediately so the rest of the agent (collectors,
    diagnostic HTTP) can still come up.
    """
    manager_url = state.get("manager_url") or settings.manager_url
    agent_token = state.get("agent_token")
    if not agent_token:
        logger.warning(
            "Agent has no token in %s; skipping heartbeat loop. "
            "Run `pct-agent register` first.",
            settings.state_path,
        )
        return

    hostname = state.get("hostname") or settings.hostname or socket.gethostname()
    url = f"{str(manager_url).rstrip('/')}/api/v1/agents/heartbeat"
    headers = {"Authorization": f"Bearer {agent_token}"}

    logger.info(
        "Starting heartbeat loop: target=%s host=%s every %ss",
        url,
        hostname,
        interval_seconds,
    )

    async with httpx.AsyncClient(timeout=_HEARTBEAT_TIMEOUT_SECONDS) as client:
        while True:
            try:
                await _send_one(client, url, headers)
            except asyncio.CancelledError:
                logger.info("Heartbeat loop cancelled; exiting.")
                raise
            except Exception:  # noqa: BLE001
                # Network blips, manager restarts, etc. — keep going.
                logger.exception("Heartbeat failed; will retry in %ss", interval_seconds)

            await asyncio.sleep(interval_seconds)


async def _send_one(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> None:
    payload = {
        "agent_time_utc": datetime.now(timezone.utc).isoformat(),
        "version": __version__,
        "role": "unknown",
    }
    response = await client.post(url, json=payload, headers=headers)
    if response.status_code == 401:
        logger.error(
            "Heartbeat unauthorized (401). Token may be invalid; "
            "re-run `pct-agent register`."
        )
        return
    response.raise_for_status()

    body = response.json()
    skew_ms = body.get("clock_skew_ms")
    server_time = body.get("server_time_utc")
    logger.debug("Heartbeat ok: server=%s skew=%sms", server_time, skew_ms)
    if isinstance(skew_ms, int) and abs(skew_ms) > 2000:
        logger.warning("Clock skew vs manager is %dms (>2s threshold).", skew_ms)
