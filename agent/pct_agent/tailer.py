"""Generic, rotation-aware log file tailer.

The tailer:
- starts at end-of-file (we don't backfill old logs at startup);
- detects rotation via inode + size shrink, re-opens, and continues;
- yields complete lines (partial trailing chunks are buffered until ``\\n``).

It deliberately uses polling (every ``poll_interval`` seconds) instead of
``inotify``/``fsevents`` so it works inside containers and across kernels
without extra deps. Throughput targets in §1 (a few thousand events/sec
fleet-wide) leave plenty of headroom.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 0.5


async def tail_file(
    path: Path,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    from_start: bool = False,
) -> AsyncIterator[str]:
    """Yield complete lines from ``path`` forever, handling rotation.

    ``from_start=True`` reads the existing file from the beginning (useful
    for tests). The default is end-of-file so an agent restart doesn't
    re-ship the entire history.
    """
    fh = None
    inode: int | None = None
    pending = ""

    while True:
        try:
            if fh is None:
                fh, inode = await _open_at_tail(path, from_start=from_start)
                if fh is None:
                    await asyncio.sleep(poll_interval)
                    continue
                from_start = False  # only honor on first open

            chunk = fh.read()
            if chunk:
                pending += chunk
                lines = pending.split("\n")
                pending = lines.pop()
                for line in lines:
                    if line:
                        yield line
                continue

            # No new bytes — check whether we were rotated out from under us.
            if _was_rotated(path, fh, inode):
                logger.info("Detected rotation on %s; re-opening", path)
                fh.close()
                fh, inode, pending = None, None, ""
                continue

            await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            if fh is not None:
                fh.close()
            raise
        except FileNotFoundError:
            # File got deleted (e.g. during pgbackrest log roll); wait for it
            # to come back rather than crashing the collector.
            if fh is not None:
                fh.close()
                fh, inode, pending = None, None, ""
            await asyncio.sleep(poll_interval)


async def _open_at_tail(path: Path, *, from_start: bool):  # type: ignore[no-untyped-def]
    if not path.exists():
        return None, None
    fh = path.open("r", encoding="utf-8", errors="replace")
    if not from_start:
        fh.seek(0, 2)  # end
    inode = path.stat().st_ino
    logger.debug("Opened %s (inode=%d, from_start=%s)", path, inode, from_start)
    return fh, inode


def _was_rotated(path: Path, fh, inode: int | None) -> bool:  # type: ignore[no-untyped-def]
    try:
        st = path.stat()
    except FileNotFoundError:
        return True
    if inode is not None and st.st_ino != inode:
        return True
    # Truncated in place ("> file") — file shrank to a size below where we are.
    try:
        return st.st_size < fh.tell()
    except (OSError, ValueError):
        return True
