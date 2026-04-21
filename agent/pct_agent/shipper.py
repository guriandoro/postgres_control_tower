"""Buffered log shipper: collectors → manager.

Design (PLAN §6 "shipper.py: buffered batches, exponential backoff,
on-disk spool"):

- Collectors call :meth:`Shipper.submit` which is non-blocking and just
  enqueues the record.
- A single background coroutine drains the queue, batches up to
  ``batch_size`` records (or flushes every ``flush_interval`` seconds),
  POSTs to ``/api/v1/logs/ingest`` and, on failure, *spools* the batch
  to disk as JSONL so the agent can survive a manager outage.
- Spooled batches are retried opportunistically on the next successful
  flush, oldest-first.

Backpressure: when both the in-memory queue and the on-disk spool fill
beyond their soft caps the oldest in-memory entry is dropped (logged).
For v1 with 5 sources × 10–20 clusters this is theoretical; we never
expect to hit it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from .log_record import LogRecord
from .manager_client import ManagerClient

logger = logging.getLogger(__name__)


_INGEST_PATH = "/api/v1/logs/ingest"
_DEFAULT_BATCH_SIZE = 200
_DEFAULT_FLUSH_INTERVAL = 5.0
_DEFAULT_QUEUE_MAXSIZE = 10_000
_BACKOFF_INITIAL = 1.0
_BACKOFF_MAX = 60.0


class Shipper:
    def __init__(
        self,
        client: ManagerClient,
        spool_dir: Path,
        *,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        flush_interval: float = _DEFAULT_FLUSH_INTERVAL,
        queue_maxsize: int = _DEFAULT_QUEUE_MAXSIZE,
    ) -> None:
        self._client = client
        self._spool_dir = spool_dir
        self._spool_dir.mkdir(parents=True, exist_ok=True)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._queue: asyncio.Queue[LogRecord] = asyncio.Queue(maxsize=queue_maxsize)

    # ---- Producer-facing API (called by collectors) ----

    def submit(self, record: LogRecord) -> None:
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            try:
                # Drop oldest to keep ingestion moving; log loudly so the
                # operator can grow the queue or fix the manager outage.
                _ = self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(record)
                logger.warning(
                    "Shipper queue full (%d); dropped oldest record",
                    self._queue.maxsize,
                )
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                logger.error("Shipper queue full; dropping record %s", record.source)

    # ---- Consumer / background loop ----

    async def run(self) -> None:
        logger.info(
            "Starting log shipper: batch=%d flush=%.1fs spool=%s",
            self._batch_size,
            self._flush_interval,
            self._spool_dir,
        )
        while True:
            try:
                batch = await self._collect_batch()
            except asyncio.CancelledError:
                logger.info("Shipper cancelled; flushing remaining queue to spool")
                await self._spill_remaining_to_spool()
                raise

            if batch:
                ok = await self._post_batch(batch)
                if not ok:
                    self._spool(batch)
                    continue

            await self._drain_spool()

    async def _collect_batch(self) -> list[LogRecord]:
        deadline = time.monotonic() + self._flush_interval
        batch: list[LogRecord] = []
        # Block for at least one record so we don't spin.
        try:
            first = await asyncio.wait_for(self._queue.get(), timeout=self._flush_interval)
            batch.append(first)
            self._queue.task_done()
        except asyncio.TimeoutError:
            return batch

        # Then drain whatever else is ready, up to the batch size or deadline.
        while len(batch) < self._batch_size and time.monotonic() < deadline:
            try:
                rec = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            batch.append(rec)
            self._queue.task_done()
        return batch

    # ---- HTTP + spool ----

    async def _post_batch(self, batch: list[LogRecord]) -> bool:
        body = {"records": [r.to_wire() for r in batch]}
        backoff = _BACKOFF_INITIAL
        attempts = 0
        while attempts < 3:
            attempts += 1
            try:
                await self._client.post(_INGEST_PATH, json=body)
                return True
            except httpx.HTTPError as exc:
                logger.warning("Log ingest attempt %d failed: %s", attempts, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
        return False

    def _spool(self, batch: list[LogRecord]) -> None:
        path = self._spool_dir / f"batch-{time.time_ns()}.jsonl"
        try:
            with path.open("w", encoding="utf-8") as fh:
                for rec in batch:
                    fh.write(json.dumps(rec.to_wire()) + "\n")
            logger.warning("Spooled %d records to %s", len(batch), path.name)
        except OSError:
            logger.exception("Failed to spool batch; %d records lost", len(batch))

    async def _drain_spool(self) -> None:
        files = sorted(self._spool_dir.glob("batch-*.jsonl"))
        for path in files:
            records = list(_read_spool_file(path))
            if not records:
                _safe_unlink(path)
                continue
            try:
                await self._client.post(_INGEST_PATH, json={"records": records})
                _safe_unlink(path)
                logger.info("Replayed spooled batch %s (%d records)", path.name, len(records))
            except httpx.HTTPError as exc:
                logger.debug("Spool replay still failing: %s; will retry later", exc)
                # Stop on first failure so older batches stay first in line.
                return

    async def _spill_remaining_to_spool(self) -> None:
        remaining: list[LogRecord] = []
        while True:
            try:
                remaining.append(self._queue.get_nowait())
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        if remaining:
            self._spool(remaining)


def _read_spool_file(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        logger.exception("Discarding malformed spool file %s", path.name)
        _safe_unlink(path)
        return []
    return out


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
