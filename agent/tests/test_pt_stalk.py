"""Unit tests for the pt-stalk command builder.

We exercise three things — anything richer needs the actual pt-stalk
script and a Postgres, so it lives in the dell4 smoke test, not here:

* ``parse_pg_dsn`` round-trips both libpq forms and returns ``{}`` for
  empty input (the agent treats missing DSN as "no PG configured").
* ``build_pt_stalk_cmd`` always emits ``--pgsql --no-stalk --collect``
  and threads host/user/dbname/port from the DSN.
* The ``PGPASSWORD`` env override only appears when there's actually
  a password (DSN or settings override).

Run from the repo root with::

    pytest agent/tests/test_pt_stalk.py
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pct_agent.config import AgentSettings
from pct_agent.pt_stalk import (
    PtStalkConfigError,
    build_pt_stalk_cmd,
    parse_pg_dsn,
)


def _make_settings(tmp_path: Path, **overrides: object) -> AgentSettings:
    base: dict[str, object] = {
        "pg_dsn": "host=db.example user=monitor dbname=app password=s3cr3t",
        "pt_stalk_bin": "/usr/local/bin/pt-stalk",
        "pt_stalk_dest_dir": tmp_path / "pt-stalk",
        "pt_stalk_pg_password": "",
        "pt_stalk_gather_sql_path": "",
    }
    base.update(overrides)
    return AgentSettings(**base)  # type: ignore[arg-type]


def test_parse_pg_dsn_keyword_form() -> None:
    parsed = parse_pg_dsn("host=db.example user=monitor dbname=app password=s3cr3t port=5433")
    assert parsed["host"] == "db.example"
    assert parsed["user"] == "monitor"
    assert parsed["dbname"] == "app"
    assert parsed["password"] == "s3cr3t"
    assert parsed["port"] == "5433"


def test_parse_pg_dsn_url_form() -> None:
    parsed = parse_pg_dsn("postgresql://monitor:s3cr3t@db.example:5433/app")
    assert parsed["host"] == "db.example"
    assert parsed["user"] == "monitor"
    assert parsed["dbname"] == "app"
    assert parsed["password"] == "s3cr3t"
    assert parsed["port"] == "5433"


def test_parse_pg_dsn_empty() -> None:
    assert parse_pg_dsn("") == {}
    assert parse_pg_dsn("   ") == {}


def test_build_pt_stalk_cmd_basics(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    cmd, env, dest_dir, pid_file, log_file = build_pt_stalk_cmd(
        settings, params={"run_time_seconds": 45, "iterations": 2}, now=1_700_000_000
    )

    # Mode flags are mandatory.
    assert "--pgsql" in cmd
    assert "--no-stalk" in cmd
    assert "--collect" in cmd

    def arg_value(flag: str) -> str:
        idx = cmd.index(flag)
        return cmd[idx + 1]

    assert arg_value("--iterations") == "2"
    assert arg_value("--run-time") == "45"
    assert arg_value("--pg-host") == "db.example"
    assert arg_value("--pg-user") == "monitor"
    assert arg_value("--pg-database") == "app"
    assert arg_value("--dest") == str(dest_dir)
    assert arg_value("--pid") == str(pid_file)
    assert arg_value("--log") == str(log_file)

    # Password came from the DSN.
    assert env == {"PGPASSWORD": "s3cr3t"}
    assert dest_dir.parent == tmp_path / "pt-stalk"


def test_build_pt_stalk_cmd_no_password_when_dsn_lacks_one(tmp_path: Path) -> None:
    settings = _make_settings(
        tmp_path, pg_dsn="host=db.example user=monitor dbname=app"
    )
    _cmd, env, _dest, _pid, _log = build_pt_stalk_cmd(
        settings, params={}, now=1_700_000_001
    )
    assert env == {}


def test_build_pt_stalk_cmd_settings_password_overrides_dsn(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, pt_stalk_pg_password="from-settings")
    _cmd, env, _dest, _pid, _log = build_pt_stalk_cmd(
        settings, params={}, now=1_700_000_002
    )
    assert env["PGPASSWORD"] == "from-settings"


def test_build_pt_stalk_cmd_database_param_override(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    cmd, _env, _dest, _pid, _log = build_pt_stalk_cmd(
        settings, params={"database": "diagnostic"}, now=1_700_000_003
    )
    idx = cmd.index("--pg-database")
    assert cmd[idx + 1] == "diagnostic"


def test_build_pt_stalk_cmd_emits_port_when_dsn_specifies_one(tmp_path: Path) -> None:
    settings = _make_settings(
        tmp_path,
        pg_dsn="host=db.example user=monitor dbname=app port=5433",
    )
    cmd, _env, _dest, _pid, _log = build_pt_stalk_cmd(
        settings, params={}, now=1_700_000_004
    )
    idx = cmd.index("--pg-port")
    assert cmd[idx + 1] == "5433"


def test_build_pt_stalk_cmd_falls_back_to_defaults_for_missing_dsn(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path, pg_dsn="")
    cmd, env, _dest, _pid, _log = build_pt_stalk_cmd(
        settings, params={}, now=1_700_000_005
    )
    # localhost/postgres/postgres are the documented agent fallbacks.
    assert cmd[cmd.index("--pg-host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--pg-user") + 1] == "postgres"
    assert cmd[cmd.index("--pg-database") + 1] == "postgres"
    assert env == {}


def test_build_pt_stalk_cmd_rejects_oob_runtime(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    # pt-stalk's --pgsql mode hard-rejects anything below 30s.
    with pytest.raises(PtStalkConfigError):
        build_pt_stalk_cmd(settings, params={"run_time_seconds": 0}, now=1)
    with pytest.raises(PtStalkConfigError):
        build_pt_stalk_cmd(settings, params={"run_time_seconds": 29}, now=1)
    with pytest.raises(PtStalkConfigError):
        build_pt_stalk_cmd(settings, params={"run_time_seconds": 99999}, now=1)


def test_build_pt_stalk_cmd_rejects_oob_iterations(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    with pytest.raises(PtStalkConfigError):
        build_pt_stalk_cmd(settings, params={"iterations": 0}, now=1)
    with pytest.raises(PtStalkConfigError):
        build_pt_stalk_cmd(settings, params={"iterations": 999}, now=1)


def test_build_pt_stalk_cmd_includes_gather_sql_env_when_set(tmp_path: Path) -> None:
    settings = _make_settings(
        tmp_path, pt_stalk_gather_sql_path="/opt/gather.sql"
    )
    _cmd, env, _dest, _pid, _log = build_pt_stalk_cmd(
        settings, params={}, now=1_700_000_006
    )
    assert env.get("PT_STALK_GATHER_SQL") == "/opt/gather.sql"
