"""Host-level OS sampler.

The ``os`` source in PCT is normally fed by ``journalctl -fo json`` (see
:mod:`.os_logs`). That works on a typical systemd host, but inside our
demo's minimal ``python:3.12-slim`` agent containers there is no
``journalctl`` binary, no ``/var/log/messages``, and no journald socket.
The result was that every test cluster shipped 0 records with
``source='os'``, leaving the Logs UI's "OS" filter permanently empty.

This collector closes that gap with a portable, capability-free fallback:
it samples ``/proc/loadavg``, ``/proc/meminfo`` and ``/proc/uptime`` on
a fixed cadence and ships one normalized :class:`LogRecord` per tick.
A simple threshold check bumps severity to ``warning`` / ``critical``
when memory pressure or load average crosses well-known watermarks, so
the UI surfaces real signal instead of pure noise.

Production hosts that *do* have journalctl get both streams in parallel
— the journald path keeps reporting OOM-killer events, kernel I/O
errors, etc., and this loop adds a steady "the host is alive" signal
that's also useful for ``last_seen_at``-style queries.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..log_record import LogRecord, LogSeverity
from ..shipper import Shipper

logger = logging.getLogger(__name__)

# Severity watermarks. These are deliberately conservative for v1 — the
# point is "make the UI light up when something is actually wrong",
# not "be a full-featured host monitor". Tune in PCT_AGENT_* env vars if
# you find them noisy on your fleet.
_MEM_USED_PCT_WARNING = 85.0
_MEM_USED_PCT_CRITICAL = 95.0
# Load average is per-CPU: 1.5*ncpu = sustained queueing.
_LOAD_PER_CPU_WARNING = 1.5
_LOAD_PER_CPU_CRITICAL = 3.0


async def host_metrics_loop(
    shipper: Shipper,
    *,
    interval_seconds: int,
    proc_dir: Path = Path("/proc"),
) -> None:
    """Ship one ``source='os'`` LogRecord every ``interval_seconds``.

    ``interval_seconds <= 0`` disables the loop (returns immediately
    after a single info log). ``proc_dir`` is parameterized for tests;
    callers in production should leave the default.
    """
    if interval_seconds <= 0:
        logger.info("Host metrics collector disabled (interval=%s)", interval_seconds)
        return

    cpu_count = os.cpu_count() or 1
    logger.info(
        "Starting host metrics collector: every %ss (cpus=%d, proc=%s)",
        interval_seconds,
        cpu_count,
        proc_dir,
    )

    while True:
        try:
            sample = _sample(proc_dir, cpu_count)
        except asyncio.CancelledError:
            logger.info("Host metrics collector cancelled; exiting.")
            raise
        except Exception:  # noqa: BLE001
            # /proc reads should never fail on Linux, but if they do we
            # don't want to take down the whole agent — sleep and retry.
            logger.exception("Host metrics sample failed; will retry next tick")
            await asyncio.sleep(interval_seconds)
            continue

        shipper.submit(_record_from_sample(sample))
        await asyncio.sleep(interval_seconds)


# ---------- Sampling ----------


def _sample(proc_dir: Path, cpu_count: int) -> dict[str, Any]:
    loadavg = _read_loadavg(proc_dir)
    meminfo = _read_meminfo(proc_dir)
    uptime = _read_uptime(proc_dir)

    mem_total = meminfo.get("MemTotal")
    mem_available = meminfo.get("MemAvailable")
    mem_used_pct: float | None = None
    if mem_total and mem_available is not None and mem_total > 0:
        mem_used_pct = round(100.0 * (mem_total - mem_available) / mem_total, 1)

    return {
        "cpu_count": cpu_count,
        "loadavg_1m": loadavg[0] if len(loadavg) >= 1 else None,
        "loadavg_5m": loadavg[1] if len(loadavg) >= 2 else None,
        "loadavg_15m": loadavg[2] if len(loadavg) >= 3 else None,
        "mem_total_kb": mem_total,
        "mem_available_kb": mem_available,
        "mem_used_pct": mem_used_pct,
        "uptime_seconds": uptime,
    }


def _read_loadavg(proc_dir: Path) -> list[float]:
    """Return ``[1m, 5m, 15m]`` from ``/proc/loadavg``; missing → empty."""
    try:
        text = (proc_dir / "loadavg").read_text(encoding="utf-8").strip()
    except OSError:
        return []
    parts = text.split()
    out: list[float] = []
    for p in parts[:3]:
        try:
            out.append(float(p))
        except ValueError:
            return out
    return out


def _read_meminfo(proc_dir: Path) -> dict[str, int]:
    """Parse ``/proc/meminfo`` into a kB-int dict. Empty if file missing."""
    out: dict[str, int] = {}
    try:
        text = (proc_dir / "meminfo").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        # Each line is "Key:    1234 kB" (or rarely no unit).
        key, sep, rest = line.partition(":")
        if not sep:
            continue
        tokens = rest.strip().split()
        if not tokens:
            continue
        try:
            out[key.strip()] = int(tokens[0])
        except ValueError:
            continue
    return out


def _read_uptime(proc_dir: Path) -> int | None:
    try:
        text = (proc_dir / "uptime").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    first = text.split(maxsplit=1)[0] if text else ""
    try:
        return int(float(first))
    except ValueError:
        return None


# ---------- Record assembly ----------


def _record_from_sample(sample: dict[str, Any]) -> LogRecord:
    severity = _classify(sample)
    raw = _format_raw(sample, severity)
    parsed: dict[str, Any] = {
        "category": "host_sample",
        "message": raw,
        **sample,
    }
    return LogRecord(
        ts_utc=datetime.now(timezone.utc),
        source="os",
        severity=severity,
        raw=raw,
        parsed=parsed,
    )


def _classify(sample: dict[str, Any]) -> LogSeverity:
    mem_pct = sample.get("mem_used_pct")
    if isinstance(mem_pct, (int, float)):
        if mem_pct >= _MEM_USED_PCT_CRITICAL:
            return "critical"
        if mem_pct >= _MEM_USED_PCT_WARNING:
            return "warning"

    cpu_count = sample.get("cpu_count") or 1
    load_1 = sample.get("loadavg_1m")
    if isinstance(load_1, (int, float)):
        per_cpu = load_1 / max(cpu_count, 1)
        if per_cpu >= _LOAD_PER_CPU_CRITICAL:
            return "critical"
        if per_cpu >= _LOAD_PER_CPU_WARNING:
            return "warning"

    return "info"


def _format_raw(sample: dict[str, Any], severity: LogSeverity) -> str:
    """Render the sample as a single human-readable line.

    Keeping this stable matters because the manager stores it in
    ``logs.events.raw`` and the Logs UI shows it verbatim when
    ``parsed.message`` is missing.
    """
    load_1 = sample.get("loadavg_1m")
    load_5 = sample.get("loadavg_5m")
    load_15 = sample.get("loadavg_15m")
    mem_pct = sample.get("mem_used_pct")
    uptime = sample.get("uptime_seconds")
    return (
        f"host_sample severity={severity} "
        f"loadavg={load_1}/{load_5}/{load_15} "
        f"mem_used_pct={mem_pct} uptime_s={uptime}"
    )
