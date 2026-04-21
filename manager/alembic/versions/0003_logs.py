"""logs.events (partitioned monthly) + logs.role_transitions

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-21

The events table uses native PostgreSQL declarative range partitioning on
``ts_utc``. Per-month child partitions are managed by the manager's
APScheduler (see ``pct_manager/partitions.py``); this migration only
creates the parent + the partition for the month it runs in, so the
manager can write rows immediately after upgrade.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE logs.events (
            id          BIGSERIAL,
            ts_utc      TIMESTAMPTZ NOT NULL,
            agent_id    INTEGER     NOT NULL
                        REFERENCES pct.agents(id) ON DELETE CASCADE,
            source      TEXT        NOT NULL,
            severity    TEXT        NOT NULL,
            raw         TEXT        NOT NULL,
            parsed      JSONB,
            PRIMARY KEY (id, ts_utc)
        ) PARTITION BY RANGE (ts_utc);
        """
    )
    op.execute(
        "CREATE INDEX ix_logs_events_agent_ts "
        "ON logs.events (agent_id, ts_utc DESC)"
    )
    op.execute(
        "CREATE INDEX ix_logs_events_parsed_gin "
        "ON logs.events USING GIN (parsed)"
    )

    # Bootstrap the current month so the very first ingest doesn't fail
    # before APScheduler has a chance to run partition maintenance.
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    next_year = year + (1 if month == 12 else 0)
    next_month = 1 if month == 12 else month + 1
    name = f"events_{year:04d}_{month:02d}"
    op.execute(
        f"""
        CREATE TABLE logs.{name}
            PARTITION OF logs.events
            FOR VALUES FROM ('{year:04d}-{month:02d}-01')
                       TO   ('{next_year:04d}-{next_month:02d}-01');
        """
    )

    op.execute(
        """
        CREATE TABLE logs.role_transitions (
            id          BIGSERIAL PRIMARY KEY,
            ts_utc      TIMESTAMPTZ NOT NULL,
            agent_id    INTEGER     NOT NULL
                        REFERENCES pct.agents(id) ON DELETE CASCADE,
            from_role   TEXT,
            to_role     TEXT        NOT NULL,
            source      TEXT        NOT NULL
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_logs_role_transitions_ts "
        "ON logs.role_transitions (ts_utc DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS logs.role_transitions")
    # Dropping the parent cascades to all per-month partitions.
    op.execute("DROP TABLE IF EXISTS logs.events CASCADE")
