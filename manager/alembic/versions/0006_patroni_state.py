"""pct.patroni_state — per-agent Patroni REST snapshots.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-22

The agent's Patroni collector POSTs one row per tick. The latest row
per agent powers the new "Patroni" panel on the cluster dashboard;
historical rows live alongside ``logs.role_transitions`` for leader-
churn forensics.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "patroni_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "agent_id",
            sa.Integer(),
            sa.ForeignKey("pct.agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("member_name", sa.String(), nullable=False),
        sa.Column(
            "patroni_role",
            sa.String(),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("timeline", sa.Integer(), nullable=True),
        sa.Column("lag_bytes", sa.BigInteger(), nullable=True),
        sa.Column("leader_member", sa.String(), nullable=True),
        sa.Column(
            "members",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="pct",
    )
    op.create_index(
        "ix_patroni_state_agent_captured",
        "patroni_state",
        ["agent_id", "captured_at"],
        schema="pct",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_patroni_state_agent_captured",
        table_name="patroni_state",
        schema="pct",
    )
    op.drop_table("patroni_state", schema="pct")
