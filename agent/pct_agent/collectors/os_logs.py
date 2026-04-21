"""journald (and plain syslog) collector.

Default mode reads ``journalctl -fo json``. Each line is a JSON object the
parser converts to a :class:`LogRecord`. If ``journalctl`` isn't on PATH
(e.g. inside a minimal container), the agent falls back to tailing
``/var/log/messages`` if present, or logs once and exits.

OOM Killer and I/O error detection happen in
:func:`pct_agent.parsers.parse_os_journald_json` so anything written to the
journal — including non-systemd messages forwarded into it — is checked.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import tzinfo
from pathlib import Path

from ..parsers import parse_os_journald_json
from ..shipper import Shipper
from .log_files import tail_one

logger = logging.getLogger(__name__)


async def os_loop(
    shipper: Shipper,
    host_tz: tzinfo,
    *,
    extra_paths: list[Path] | None = None,
    journalctl_args: list[str] | None = None,
) -> None:
    """Stream OS-level events. Runs forever; cooperates with task cancellation."""
    journalctl = shutil.which("journalctl")
    if journalctl is None:
        logger.warning("journalctl not found; OS collector will only tail explicit paths")
        await _tail_only(shipper, host_tz, extra_paths or [])
        return

    args = journalctl_args or ["-f", "-o", "json", "--no-pager"]
    logger.info("Starting OS collector via %s %s", journalctl, " ".join(args))

    while True:
        proc = await asyncio.create_subprocess_exec(
            journalctl,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if not line:
                    continue
                try:
                    rec = parse_os_journald_json(line, host_tz)
                except Exception:  # noqa: BLE001
                    logger.exception("OS parser raised; dropping line")
                    continue
                shipper.submit(rec)
        except asyncio.CancelledError:
            proc.terminate()
            await proc.wait()
            raise
        finally:
            if proc.returncode is None:
                proc.terminate()
                await proc.wait()

        # journalctl exited unexpectedly; pause and respawn so a transient
        # failure (log rotation in journald, etc.) doesn't kill the collector.
        logger.warning("journalctl exited with %s; restarting in 2s", proc.returncode)
        await asyncio.sleep(2.0)


async def _tail_only(
    shipper: Shipper, host_tz: tzinfo, paths: list[Path]
) -> None:
    if not paths:
        logger.info("OS collector idle: no journalctl and no tail paths configured")
        # Sleep forever so the lifespan owns task cancellation cleanly.
        while True:
            await asyncio.sleep(3600)
    tasks = [
        asyncio.create_task(
            tail_one(p, parse_os_journald_json, shipper, host_tz, label="os"),
            name=f"pct-agent-tail-os-{p.name}",
        )
        for p in paths
    ]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        raise
