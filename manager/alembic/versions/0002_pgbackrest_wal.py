"""pgbackrest_info + wal_health tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-21

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pgbackrest_info",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["pct.agents.id"], ondelete="CASCADE"
        ),
        schema="pct",
    )
    op.create_index(
        "ix_pgbackrest_info_agent_captured",
        "pgbackrest_info",
        ["agent_id", "captured_at"],
        schema="pct",
    )

    op.create_table(
        "wal_health",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_archived_wal", sa.String(), nullable=True),
        sa.Column("archive_lag_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "gap_detected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["pct.agents.id"], ondelete="CASCADE"
        ),
        schema="pct",
    )
    op.create_index(
        "ix_wal_health_agent_captured",
        "wal_health",
        ["agent_id", "captured_at"],
        schema="pct",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wal_health_agent_captured", table_name="wal_health", schema="pct"
    )
    op.drop_table("wal_health", schema="pct")
    op.drop_index(
        "ix_pgbackrest_info_agent_captured",
        table_name="pgbackrest_info",
        schema="pct",
    )
    op.drop_table("pgbackrest_info", schema="pct")
