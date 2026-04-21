"""File-based log collectors (postgres, pgbackrest, patroni, etcd).

Each collector tails one or more files, runs the source-specific parser
on every line, and hands the resulting :class:`LogRecord` to the shipper.
journald is special-cased in :mod:`.os_logs`.

Paths may contain shell-style glob metacharacters (``*``, ``?``, ``[``).
This is required for sources like Postgres, whose ``log_filename`` is
``postgresql-%Y-%m-%d_%H%M%S.log`` — a brand-new file every rotation.
A glob entry spawns a watcher that re-scans its parent directory and
fans out a per-file tailer for every match (existing files are tailed
from end, files discovered later are tailed from the beginning so we
don't lose the first lines of a freshly rotated log).
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

GLOB_CHARS = frozenset("*?[")
DEFAULT_SCAN_INTERVAL = 2.0


ParserFn = Callable[[str, tzinfo], LogRecord]


def _has_glob(path: Path) -> bool:
    return any(ch in GLOB_CHARS for ch in path.name) or any(
        ch in GLOB_CHARS for part in path.parts for ch in part
    )


async def tail_one(
    path: Path,
    parser: ParserFn,
    shipper: Shipper,
    host_tz: tzinfo,
    *,
    label: str,
    from_start: bool = False,
) -> None:
    logger.info(
        "Starting %s collector on %s (from_start=%s)", label, path, from_start
    )
    async for line in tail_file(path, from_start=from_start):
        try:
            record = parser(line, host_tz)
        except Exception:  # noqa: BLE001
            logger.exception("%s parser raised on line: %r", label, line[:200])
            continue
        shipper.submit(record)


async def tail_glob(
    pattern: Path,
    parser: ParserFn,
    shipper: Shipper,
    host_tz: tzinfo,
    *,
    label: str,
    scan_interval: float = DEFAULT_SCAN_INTERVAL,
) -> None:
    """Tail every file matching ``pattern``; pick up new matches as they appear.

    Existing matches at first scan are tailed from end-of-file to avoid
    re-shipping pre-restart history. Files that show up on a later scan
    (typical for Postgres' timestamped rotation) are tailed from the
    beginning so the first few lines aren't lost.
    """
    parent = pattern.parent if str(pattern.parent) != "" else Path(".")
    glob_pat = pattern.name
    if not glob_pat:
        logger.warning(
            "%s collector got empty glob pattern %s; skipping", label, pattern
        )
        return

    logger.info(
        "Starting %s glob watcher on %s/%s (scan=%.1fs)",
        label, parent, glob_pat, scan_interval,
    )
    seen: dict[Path, asyncio.Task[None]] = {}
    first_scan = True
    try:
        while True:
            try:
                matches = sorted(parent.glob(glob_pat)) if parent.is_dir() else []
            except OSError as exc:
                logger.warning(
                    "%s glob scan failed for %s: %r", label, pattern, exc
                )
                matches = []

            for match in matches:
                if match in seen:
                    continue
                from_start = not first_scan
                seen[match] = asyncio.create_task(
                    tail_one(
                        match, parser, shipper, host_tz,
                        label=label, from_start=from_start,
                    ),
                    name=f"pct-agent-tail-{label}-{match.name}",
                )
            first_scan = False

            for p, t in list(seen.items()):
                if not t.done():
                    continue
                if not t.cancelled() and t.exception() is not None:
                    logger.warning(
                        "%s tail task for %s ended: %r",
                        label, p, t.exception(),
                    )
                seen.pop(p, None)

            await asyncio.sleep(scan_interval)
    except asyncio.CancelledError:
        for t in seen.values():
            t.cancel()
        raise


async def tail_many(
    paths: Iterable[Path],
    parser: ParserFn,
    shipper: Shipper,
    host_tz: tzinfo,
    *,
    label: str,
) -> None:
    """Fan out one tailer per path. Glob entries get a watcher; literal
    paths get a single ``tail_one``. Returns when all child tasks complete.
    """
    tasks: list[asyncio.Task[None]] = []
    for p in paths:
        if _has_glob(p):
            tasks.append(
                asyncio.create_task(
                    tail_glob(p, parser, shipper, host_tz, label=label),
                    name=f"pct-agent-tail-{label}-glob-{p.name}",
                )
            )
        else:
            tasks.append(
                asyncio.create_task(
                    tail_one(p, parser, shipper, host_tz, label=label),
                    name=f"pct-agent-tail-{label}-{p.name}",
                )
            )
    if not tasks:
        logger.info("%s collector has no paths configured; idle.", label)
        return
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        raise
