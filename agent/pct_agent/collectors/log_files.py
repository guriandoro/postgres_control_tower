"""File-based log collectors (postgres, pgbackrest, patroni, etcd).

Each collector tails one or more files, runs the source-specific parser
on every line, and hands the resulting :class:`LogRecord` to the shipper.
journald is special-cased in :mod:`.os_logs`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import tzinfo
from pathlib import Path

from ..log_record import LogRecord
from ..shipper import Shipper
from ..tailer import tail_file

logger = logging.getLogger(__name__)


ParserFn = Callable[[str, tzinfo], LogRecord]


async def tail_one(
    path: Path,
    parser: ParserFn,
    shipper: Shipper,
    host_tz: tzinfo,
    *,
    label: str,
) -> None:
    logger.info("Starting %s collector on %s", label, path)
    async for line in tail_file(path):
        try:
            record = parser(line, host_tz)
        except Exception:  # noqa: BLE001
            logger.exception("%s parser raised on line: %r", label, line[:200])
            continue
        shipper.submit(record)


async def tail_many(
    paths: Iterable[Path],
    parser: ParserFn,
    shipper: Shipper,
    host_tz: tzinfo,
    *,
    label: str,
) -> None:
    """Fan out to one ``tail_one`` task per path. Returns when all complete."""
    tasks = [
        asyncio.create_task(
            tail_one(p, parser, shipper, host_tz, label=label),
            name=f"pct-agent-tail-{label}-{p.name}",
        )
        for p in paths
    ]
    if not tasks:
        logger.info("%s collector has no paths configured; idle.", label)
        return
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        raise
