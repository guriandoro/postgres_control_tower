"""initial schema: pct + logs schemas, clusters/agents/users tables

Revision ID: 0001
Revises:
Create Date: 2026-04-21

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS pct")
    op.execute("CREATE SCHEMA IF NOT EXISTS logs")

    op.create_table(
        "clusters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default="standalone"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("name", name="uq_clusters_name"),
        schema="pct",
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("hostname", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.String(), nullable=True),
        sa.Column("clock_skew_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["cluster_id"], ["pct.clusters.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("token_hash", name="uq_agents_token_hash"),
        sa.UniqueConstraint(
            "cluster_id", "hostname", name="uq_agents_cluster_hostname"
        ),
        schema="pct",
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="viewer"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
        schema="pct",
    )


def downgrade() -> None:
    op.drop_table("users", schema="pct")
    op.drop_table("agents", schema="pct")
    op.drop_table("clusters", schema="pct")
    op.execute("DROP SCHEMA IF EXISTS logs")
    op.execute("DROP SCHEMA IF EXISTS pct")
