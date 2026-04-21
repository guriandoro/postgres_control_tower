"""Normalized log record produced by every collector.

UTC normalization happens **here**: collectors hand us a naive or
host-local timestamp plus the host's tzinfo, and we convert to a UTC
``datetime`` before serializing. The wire format is JSON-friendly and
matches :class:`pct_manager.schemas.LogRecordIn`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, tzinfo
from typing import Any, Literal

LogSource = Literal["postgres", "pgbackrest", "patroni", "etcd", "os"]
LogSeverity = Literal["debug", "info", "warning", "error", "critical"]


@dataclass(slots=True)
class LogRecord:
    ts_utc: datetime
    source: LogSource
    severity: LogSeverity
    raw: str
    parsed: dict[str, Any] | None = field(default=None)

    def to_wire(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts_utc"] = self.ts_utc.astimezone(timezone.utc).isoformat()
        return d


def normalize_to_utc(naive_or_aware: datetime, host_tz: tzinfo) -> datetime:
    """Return a tz-aware UTC datetime regardless of the input's awareness.

    Naive inputs are assumed to be in ``host_tz`` (matches Postgres /
    pgBackRest behavior, which print the host's local clock).
    """
    if naive_or_aware.tzinfo is None:
        aware = naive_or_aware.replace(tzinfo=host_tz)
    else:
        aware = naive_or_aware
    return aware.astimezone(timezone.utc)
