"""Agent configuration.

Two layers:
  1. Static config from a YAML file (path: $PCT_AGENT_CONFIG, default
     /etc/pct-agent/config.yaml). Holds non-secret operational settings.
  2. State file holding the agent's own bearer token after registration
     (path: $PCT_AGENT_STATE, default /var/lib/pct-agent/state.json).

Env vars override file values. All env vars are prefixed with ``PCT_AGENT_``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PCT_AGENT_",
        extra="ignore",
    )

    manager_url: str = Field(
        default="http://localhost:8080",
        description="Base URL of the manager API (no trailing slash).",
    )
    config_path: Path = Path("/etc/pct-agent/config.yaml")
    state_path: Path = Path("/var/lib/pct-agent/state.json")

    bind_host: str = "127.0.0.1"
    bind_port: int = 8081

    # How often the heartbeat loop POSTs to the manager. 30s keeps
    # `last_seen_at` fresh without piling on requests for a 10–20 cluster
    # fleet (PLAN §1).
    heartbeat_interval: int = 30

    # --- Postgres probe (collectors/wal.py) ---
    # libpq DSN. Empty disables the WAL collector. Default targets a local
    # Unix-socket connection as the postgres OS user, which is the typical
    # agent install (sibling container or systemd unit).
    pg_dsn: str = ""
    wal_interval: int = 30

    # --- pgBackRest probe (collectors/pgbackrest.py) ---
    pgbackrest_bin: str = "pgbackrest"
    # Optional --stanza= filter; empty means "all stanzas configured".
    pgbackrest_stanza: str = ""
    pgbackrest_interval: int = 60

    # --- Patroni REST probe (collectors/patroni.py) ---
    # Base URL of the local node's Patroni REST API. Empty disables the
    # collector — that's the right default for standalone agents.
    # Typical compose value: ``http://patroni-1:8008``.
    patroni_rest_url: str = ""
    patroni_interval: int = 30

    # --- Log file paths (one path per source; multiple via comma-separated) ---
    # Empty disables the corresponding collector. See docs/log-sources.md
    # for the canonical defaults per Postgres / pgBackRest install.
    pg_log_paths: str = ""
    pgbackrest_log_paths: str = ""
    patroni_log_paths: str = ""
    etcd_log_paths: str = ""
    # OS source uses journalctl by default; this is for explicit fallback files.
    os_log_paths: str = ""
    # Periodic /proc-based OS sampler (collectors/host_metrics.py). Always
    # ships under source='os'. Set to 0 to disable. Defaults to one sample
    # per minute so containerized installs (no journalctl) still populate
    # the OS source; production hosts get this *in addition* to journald.
    host_metrics_interval: int = 60

    # Shipper tuning (see PLAN §6).
    shipper_batch_size: int = 200
    shipper_flush_interval: float = 5.0
    spool_dir: Path = Path("/var/lib/pct-agent/spool")

    # --- Job runner (P6 Safe Ops) ---
    # Long-poll window for /api/v1/agents/jobs/next. The manager caps this
    # server-side too. Set to 0 to disable the runner entirely.
    runner_long_poll_seconds: int = 25
    # How long any single job is allowed to run before the runner kills it.
    # Backups can legitimately take hours; default is 6h, override per-host
    # if you regularly back up larger DBs.
    runner_job_timeout_seconds: int = 6 * 3600
    # Max chars of stdout to keep on the manager (last N chars). The full
    # output is captured by the pgBackRest log tailer in any case.
    runner_stdout_tail_chars: int = 16_000

    # Subset of file-config that may also be set via env for one-shot installs:
    enrollment_token: str | None = None
    cluster_name: str | None = None
    cluster_kind: str = "standalone"
    hostname: str | None = None  # falls back to socket.gethostname() at runtime


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Agent config at {path} must be a mapping.")
    return data


def load_settings() -> AgentSettings:
    """Resolve settings with precedence: env vars > YAML file > field defaults.

    The previous implementation merged ``base.model_dump()`` (i.e. every
    field, including unset defaults like ``pgbackrest_stanza=""``) on top
    of the YAML, which silently clobbered any YAML-only setting with the
    field's default. ``pydantic_settings`` records which fields were
    populated from the environment (or constructor) in
    ``model_fields_set``; we only want those to override the YAML.
    """
    base = AgentSettings()
    file_data = _load_yaml(base.config_path)
    if not file_data:
        return base
    env_overrides = {name: getattr(base, name) for name in base.model_fields_set}
    merged = {**file_data, **env_overrides}
    return AgentSettings(**merged)


# ---------- State (token) persistence ----------


class AgentState:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
