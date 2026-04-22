"""Patroni REST snapshot collector.

Polls the local node's Patroni REST API (``GET <patroni_rest_url>/cluster``)
on a fixed cadence and POSTs a normalized snapshot to the manager. Also
updates the shared :class:`AgentRuntimeState` so the heartbeat loop
reports the right ``primary | replica`` value even before the manager
processes the dedicated patroni_state ingest.

Why a separate collector (and not piggyback on the WAL probe):

- ``pg_is_in_recovery()`` is a *node-local* answer. It says nothing about
  who Patroni currently considers the leader, the replica's apply lag in
  bytes, or the timeline. The dashboard wants those details.
- Patroni 3.x's lag accounting is in bytes and replica state is richer
  ("streaming" vs "running" vs "start failed"). We surface that verbatim.

Fail-safe behavior: any HTTP / connection error logs and re-tries on the
next tick. The collector never raises out of the loop — that would kill
the agent's lifespan task and silently break the heartbeat too.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import AgentSettings
from ..manager_client import ManagerClient
from ..runtime_state import AgentRuntimeState

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_SECONDS = 5.0

# Map Patroni's role taxonomy down to the project-wide AgentRole used by
# the heartbeat. Anything else (including a missing/unknown role) falls
# through as "unknown" so the manager doesn't lie about a node we can't
# classify.
_PATRONI_TO_AGENT_ROLE: dict[str, str] = {
    "leader": "primary",
    "standby_leader": "primary",
    "replica": "replica",
    "sync_standby": "replica",
}


async def patroni_loop(
    settings: AgentSettings,
    client: ManagerClient,
    runtime_state: AgentRuntimeState,
    hostname: str,
    interval_seconds: int | None = None,
) -> None:
    """Run forever, polling Patroni and shipping a snapshot per tick."""
    if not settings.patroni_rest_url:
        logger.info(
            "PCT_AGENT_PATRONI_REST_URL is empty; Patroni collector disabled. "
            "This is expected for standalone agents."
        )
        return

    base_url = settings.patroni_rest_url.rstrip("/")
    interval = interval_seconds or settings.patroni_interval
    logger.info(
        "Starting Patroni collector: target=%s host=%s every %ss",
        base_url,
        hostname,
        interval,
    )

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as http:
        while True:
            try:
                snapshot = await _probe_once(http, base_url, hostname)
            except asyncio.CancelledError:
                logger.info("Patroni collector cancelled; exiting.")
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Patroni probe failed; will retry in %ss", interval)
                await asyncio.sleep(interval)
                continue

            if snapshot is None:
                # Cluster reachable but our member isn't in the response yet
                # (Patroni still bootstrapping, or hostname mismatch). Skip
                # the POST so the manager's "latest" doesn't go stale-clean.
                await asyncio.sleep(interval)
                continue

            mapped_role = _PATRONI_TO_AGENT_ROLE.get(
                snapshot["patroni_role"], "unknown"
            )
            runtime_state.update_role(mapped_role, "patroni")

            try:
                await client.post("/api/v1/agents/patroni_state", json=snapshot)
                logger.debug(
                    "Shipped patroni_state: role=%s state=%s timeline=%s lag=%s",
                    snapshot["patroni_role"],
                    snapshot["state"],
                    snapshot["timeline"],
                    snapshot["lag_bytes"],
                )
            except Exception:  # noqa: BLE001
                logger.exception("patroni_state POST failed; will retry next tick")

            await asyncio.sleep(interval)


async def _probe_once(
    http: httpx.AsyncClient, base_url: str, hostname: str
) -> dict[str, Any] | None:
    """One GET ``/cluster`` round.

    Returns a JSON-ready ingest payload, or ``None`` when the response
    doesn't contain an entry for the local member yet (we don't want to
    POST a snapshot that says "I'm unknown" while Patroni is still
    initializing — the heartbeat already covers that case).
    """
    url = f"{base_url}/cluster"
    response = await http.get(url)
    response.raise_for_status()
    data = response.json()

    members = data.get("members") or []
    if not isinstance(members, list):
        members = []

    own = _find_own_member(members, hostname)
    leader = _find_leader_name(members)
    captured_at = datetime.now(timezone.utc).isoformat()

    if own is None:
        logger.warning(
            "Patroni /cluster response from %s has no member named %r "
            "(saw %s). Skipping ingest.",
            url,
            hostname,
            [m.get("name") for m in members if isinstance(m, dict)],
        )
        return None

    return {
        "captured_at": captured_at,
        "member_name": str(own.get("name") or hostname),
        "patroni_role": str(own.get("role") or "unknown"),
        "state": (str(own.get("state")) if own.get("state") is not None else None),
        "timeline": _coerce_int(own.get("timeline")),
        "lag_bytes": _coerce_int(own.get("lag")),
        "leader_member": leader,
        "members": [m for m in members if isinstance(m, dict)],
    }


def _find_own_member(members: list[Any], hostname: str) -> dict[str, Any] | None:
    for member in members:
        if not isinstance(member, dict):
            continue
        if member.get("name") == hostname:
            return member
        # Patroni's "host" is sometimes a DNS name that matches the agent's
        # hostname even when "name" is something different (rare, but seen
        # with custom PATRONI_NAME). Fall through to that as a backup.
        if member.get("host") == hostname:
            return member
    return None


def _find_leader_name(members: list[Any]) -> str | None:
    for member in members:
        if not isinstance(member, dict):
            continue
        if member.get("role") in ("leader", "standby_leader"):
            name = member.get("name")
            if isinstance(name, str):
                return name
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
