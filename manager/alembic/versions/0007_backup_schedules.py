"""pct.backup_schedules — recurring backup definitions.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-22

A backup schedule pairs a cluster with a cron expression (UTC) and a
backup kind. The manager's APScheduler tick reads enabled rows and
inserts ``pct.jobs`` rows when ``next_run_at <= now()``. Schedules are
intentionally limited to the ``backup_*`` allowlist; ``check`` /
``stanza_create`` stay one-off (they don't need a calendar — see
``docs/safety-and-rbac.md``).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backup_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "cluster_id",
            sa.Integer(),
            sa.ForeignKey("pct.clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("cron_expression", sa.String(length=128), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("pct.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_job_id",
            sa.Integer(),
            sa.ForeignKey("pct.jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        schema="pct",
    )
    op.create_index(
        "ix_backup_schedules_cluster",
        "backup_schedules",
        ["cluster_id"],
        schema="pct",
    )
    # Hot path for the tick job: WHERE enabled = true AND next_run_at <= now().
    op.create_index(
        "ix_backup_schedules_due",
        "backup_schedules",
        ["enabled", "next_run_at"],
        schema="pct",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_backup_schedules_due",
        table_name="backup_schedules",
        schema="pct",
    )
    op.drop_index(
        "ix_backup_schedules_cluster",
        table_name="backup_schedules",
        schema="pct",
    )
    op.drop_table("backup_schedules", schema="pct")
