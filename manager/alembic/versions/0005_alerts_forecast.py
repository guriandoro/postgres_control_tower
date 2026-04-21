"""pct.alerts + pct.storage_forecast — P7 Alerting + storage runway.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-21

Two new tables:
- ``pct.alerts``: open/resolved alerts raised by the rule engine.
- ``pct.storage_forecast``: latest "Storage Runway" linear-regression
  forecast per cluster, refreshed on a schedule.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column(
            "severity",
            sa.String(),
            nullable=False,
            server_default=sa.text("'warning'"),
        ),
        sa.Column(
            "cluster_id",
            sa.Integer(),
            sa.ForeignKey("pct.clusters.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "dedup_key",
            sa.String(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "acknowledged_by",
            sa.Integer(),
            sa.ForeignKey("pct.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="pct",
    )
    op.create_index(
        "ix_alerts_kind_cluster_open",
        "alerts",
        ["kind", "cluster_id", "resolved_at"],
        schema="pct",
    )
    op.create_index(
        "ix_alerts_opened_at",
        "alerts",
        ["opened_at"],
        schema="pct",
    )

    op.create_table(
        "storage_forecast",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "cluster_id",
            sa.Integer(),
            sa.ForeignKey("pct.clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "sample_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "daily_growth_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "current_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("target_bytes", sa.BigInteger(), nullable=True),
        sa.Column("days_to_target", sa.Float(asdecimal=False), nullable=True),
        sa.UniqueConstraint("cluster_id", name="uq_storage_forecast_cluster"),
        schema="pct",
    )


def downgrade() -> None:
    op.drop_table("storage_forecast", schema="pct")
    op.drop_index("ix_alerts_opened_at", table_name="alerts", schema="pct")
    op.drop_index("ix_alerts_kind_cluster_open", table_name="alerts", schema="pct")
    op.drop_table("alerts", schema="pct")
