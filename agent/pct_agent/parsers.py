"""Per-source line parsers.

Each parser takes a raw log line (and the host tzinfo) and returns a
:class:`LogRecord` ready for the shipper. Parsers are deliberately
permissive: any line that doesn't match the expected format is still
emitted with the line itself as ``raw`` and ``severity='info'`` so we
never drop data on a format change.

Patroni and etcd parsers also detect leader / follower transitions and
attach a ``role_transition`` block to ``parsed`` so the manager can
populate ``logs.role_transitions``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone, tzinfo
from typing import Any

from .log_record import LogRecord, LogSeverity, normalize_to_utc


# ---------------- Severity normalization ----------------

# Map raw upstream level strings (case-insensitive) to our 5-level scale.
_SEVERITY_MAP: dict[str, LogSeverity] = {
    # Postgres
    "DEBUG": "debug", "DEBUG1": "debug", "DEBUG2": "debug",
    "DEBUG3": "debug", "DEBUG4": "debug", "DEBUG5": "debug",
    "LOG": "info", "INFO": "info", "NOTICE": "info", "STATEMENT": "info",
    "DETAIL": "info", "HINT": "info", "CONTEXT": "info",
    "WARNING": "warning",
    "ERROR": "error", "FATAL": "critical", "PANIC": "critical",
    # pgBackRest / Patroni / etcd / journald common levels
    "TRACE": "debug",
    "WARN": "warning",
    "CRITICAL": "critical", "CRIT": "critical",
}


def _severity(raw_level: str | None) -> LogSeverity:
    if not raw_level:
        return "info"
    return _SEVERITY_MAP.get(raw_level.strip().upper(), "info")


# ---------------- Postgres ----------------
#
# Default log_line_prefix in our compose demo is `'%t [%p]: '`, producing
# lines like:
#     2026-04-21 12:34:56.123 UTC [42]: [1-1] LOG:  database system is ready
# The regex is intentionally tolerant: a missing TZ or PID still parses.

_PG_RE = re.compile(
    r"""^
    (?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*
    (?P<tz>[A-Z]{2,5}|[+-]\d{2}:?\d{2})?\s*
    (?:\[(?P<pid>\d+)\])?\s*:?\s*   # optional [pid] then optional ':'
    (?:\[\d+-\d+\])?\s*             # optional [line_num-session]
    (?P<level>LOG|INFO|NOTICE|WARNING|ERROR|FATAL|PANIC|DEBUG\d?|STATEMENT|DETAIL|HINT|CONTEXT)
    :\s+(?P<msg>.*)$""",
    re.VERBOSE,
)


def parse_postgres_line(line: str, host_tz: tzinfo) -> LogRecord:
    match = _PG_RE.match(line)
    if not match:
        return LogRecord(
            ts_utc=datetime.now(timezone.utc),
            source="postgres",
            severity="info",
            raw=line,
            parsed={"message": line},
        )
    ts = _parse_ts(match["ts"], match["tz"], host_tz)
    parsed: dict[str, Any] = {
        "message": match["msg"],
        "level": match["level"],
    }
    if match["pid"]:
        parsed["pid"] = int(match["pid"])
    return LogRecord(
        ts_utc=ts,
        source="postgres",
        severity=_severity(match["level"]),
        raw=line,
        parsed=parsed,
    )


# ---------------- pgBackRest ----------------
#
# pgBackRest default file format:
#     2026-04-21 12:34:56.789 P00   INFO: archive-get command begin
# Process tag is one or two letters + digits (P00, P01, ...).

_PGBR_RE = re.compile(
    r"""^
    (?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+
    (?P<proc>[A-Z]\d+)\s+
    (?P<level>DETAIL|INFO|WARN|ERROR|TRACE|DEBUG):\s+
    (?P<msg>.*)$""",
    re.VERBOSE,
)


def parse_pgbackrest_line(line: str, host_tz: tzinfo) -> LogRecord:
    match = _PGBR_RE.match(line)
    if not match:
        return LogRecord(
            ts_utc=datetime.now(timezone.utc),
            source="pgbackrest",
            severity="info",
            raw=line,
            parsed={"message": line},
        )
    ts = _parse_ts(match["ts"], None, host_tz)
    return LogRecord(
        ts_utc=ts,
        source="pgbackrest",
        severity=_severity(match["level"]),
        raw=line,
        parsed={
            "message": match["msg"],
            "level": match["level"],
            "proc": match["proc"],
        },
    )


# ---------------- Patroni ----------------
#
# Standard Patroni format:
#     2026-04-21 12:34:56,789 INFO: promoted self to leader by acquiring session lock
# Role-transition phrases (see Patroni source: ha.py / postgresql.py):

_PATRONI_RE = re.compile(
    r"""^
    (?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.]\d+)\s+
    (?P<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL):\s+
    (?P<msg>.*)$""",
    re.VERBOSE,
)

_PATRONI_TRANSITIONS: list[tuple[re.Pattern[str], str | None, str]] = [
    (re.compile(r"promoted self to leader", re.I),                 "replica", "primary"),
    (re.compile(r"acquired session lock as a leader", re.I),       None,      "primary"),
    (re.compile(r"demoting self because", re.I),                   "primary", "replica"),
    (re.compile(r"demoted self", re.I),                            "primary", "replica"),
    (re.compile(r"following a different leader", re.I),            "primary", "replica"),
    (re.compile(r"starting as a (?:secondary|replica)", re.I),     None,      "replica"),
]


def parse_patroni_line(line: str, host_tz: tzinfo) -> LogRecord:
    match = _PATRONI_RE.match(line)
    if not match:
        return LogRecord(
            ts_utc=datetime.now(timezone.utc),
            source="patroni",
            severity="info",
            raw=line,
            parsed={"message": line},
        )
    # Patroni uses ',' as ms separator; normalize so fromisoformat works.
    ts_str = match["ts"].replace(",", ".")
    ts = _parse_ts(ts_str, None, host_tz)
    parsed: dict[str, Any] = {"message": match["msg"], "level": match["level"]}
    rt = _detect_role_transition(match["msg"], _PATRONI_TRANSITIONS)
    if rt is not None:
        parsed["role_transition"] = rt
    return LogRecord(
        ts_utc=ts,
        source="patroni",
        severity=_severity(match["level"]),
        raw=line,
        parsed=parsed,
    )


# ---------------- etcd ----------------
#
# Modern etcd (>=3.4) emits structured JSON lines, e.g.:
#   {"level":"info","ts":"2026-04-21T12:34:56.789Z","msg":"raft.node: ...",
#    "leader-changed-from":"abc","leader-changed-to":"def"}
# Older releases use a textual format; we fall back to a permissive parser.

_ETCD_TEXT_RE = re.compile(
    r"""^
    (?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)\s+
    (?P<level>INFO|WARN|WARNING|ERROR|DEBUG|FATAL)\s+
    \|\s+(?P<msg>.*)$""",
    re.VERBOSE,
)


def parse_etcd_line(line: str, host_tz: tzinfo) -> LogRecord:
    stripped = line.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            ts = _parse_ts(obj.get("ts", ""), None, host_tz)
            level = obj.get("level", "info")
            parsed: dict[str, Any] = {
                "message": obj.get("msg", stripped),
                "level": level,
                "fields": {k: v for k, v in obj.items() if k not in ("ts", "level", "msg")},
            }
            rt = _etcd_role_transition_from_json(obj)
            if rt is not None:
                parsed["role_transition"] = rt
            return LogRecord(
                ts_utc=ts,
                source="etcd",
                severity=_severity(level),
                raw=line,
                parsed=parsed,
            )
        except json.JSONDecodeError:
            pass

    match = _ETCD_TEXT_RE.match(line)
    if not match:
        return LogRecord(
            ts_utc=datetime.now(timezone.utc),
            source="etcd",
            severity="info",
            raw=line,
            parsed={"message": line},
        )
    ts = _parse_ts(match["ts"], None, host_tz)
    return LogRecord(
        ts_utc=ts,
        source="etcd",
        severity=_severity(match["level"]),
        raw=line,
        parsed={"message": match["msg"], "level": match["level"]},
    )


def _etcd_role_transition_from_json(obj: dict[str, Any]) -> dict[str, Any] | None:
    msg = str(obj.get("msg", "")).lower()
    if "leader-changed-to" in obj or "new-leader" in obj:
        return {
            "from": obj.get("leader-changed-from") or obj.get("old-leader"),
            "to": obj.get("leader-changed-to") or obj.get("new-leader"),
        }
    if "elected leader" in msg or "became leader" in msg:
        return {"from": None, "to": "leader"}
    if "lost leader" in msg or "stepped down" in msg:
        return {"from": "leader", "to": None}
    return None


# ---------------- OS / journald ----------------
#
# We feed `journalctl -fo json` so each line is already a JSON object with
# `__REALTIME_TIMESTAMP` (microseconds since epoch) and `MESSAGE`.

_OOM_RE = re.compile(r"out of memory: kill(?:ed)? process", re.I)
_IO_ERR_RE = re.compile(r"\b(I/O error|EIO|hardware error|filesystem .* read-only)\b", re.I)


def parse_os_journald_json(line: str, host_tz: tzinfo) -> LogRecord:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return LogRecord(
            ts_utc=datetime.now(timezone.utc),
            source="os",
            severity="info",
            raw=line,
            parsed={"message": line},
        )

    ts_us = obj.get("__REALTIME_TIMESTAMP")
    if ts_us:
        try:
            ts = datetime.fromtimestamp(int(ts_us) / 1_000_000, tz=timezone.utc)
        except (TypeError, ValueError):
            ts = datetime.now(timezone.utc)
    else:
        ts = datetime.now(timezone.utc)

    message = str(obj.get("MESSAGE", ""))
    severity: LogSeverity = "info"
    parsed: dict[str, Any] = {
        "message": message,
        "unit": obj.get("_SYSTEMD_UNIT") or obj.get("UNIT"),
        "host": obj.get("_HOSTNAME"),
    }
    if _OOM_RE.search(message):
        severity = "critical"
        parsed["category"] = "oom_killer"
    elif _IO_ERR_RE.search(message):
        severity = "error"
        parsed["category"] = "io_error"
    elif (priority := obj.get("PRIORITY")) is not None:
        severity = _journald_priority_to_severity(priority)

    return LogRecord(ts_utc=ts, source="os", severity=severity, raw=line, parsed=parsed)


def _journald_priority_to_severity(priority: Any) -> LogSeverity:
    """Map syslog priorities (0-7) to our 5-level scale."""
    try:
        p = int(priority)
    except (TypeError, ValueError):
        return "info"
    if p <= 2:
        return "critical"  # emerg/alert/crit
    if p == 3:
        return "error"
    if p == 4:
        return "warning"
    if p == 7:
        return "debug"
    return "info"


# ---------------- Helpers ----------------


def _parse_ts(ts: str, tz_hint: str | None, host_tz: tzinfo) -> datetime:
    """Parse a timestamp string into a UTC-aware datetime.

    Handles:
    - ISO-8601 with explicit offset or trailing Z
    - "YYYY-MM-DD HH:MM:SS[.ffffff]" with optional separate TZ token
    """
    cleaned = ts.replace(" ", "T")
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.now(timezone.utc)

    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)

    # Postgres-style "UTC" / "EST" / "+0200" tokens come in tz_hint.
    if tz_hint:
        try:
            offset_dt = datetime.fromisoformat(cleaned + _normalize_offset(tz_hint))
            if offset_dt.tzinfo is not None:
                return offset_dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return normalize_to_utc(dt, host_tz)


def _normalize_offset(token: str) -> str:
    """Convert a Postgres TZ token to an ISO offset suffix or empty string."""
    if token.upper() == "UTC" or token.upper() == "GMT" or token.upper() == "Z":
        return "+00:00"
    if re.fullmatch(r"[+-]\d{4}", token):
        return f"{token[:3]}:{token[3:]}"
    if re.fullmatch(r"[+-]\d{2}:\d{2}", token):
        return token
    return ""


def _detect_role_transition(
    msg: str,
    table: list[tuple[re.Pattern[str], str | None, str]],
) -> dict[str, Any] | None:
    for pattern, from_role, to_role in table:
        if pattern.search(msg):
            return {"from": from_role, "to": to_role}
    return None
