"""pct.jobs — Safe Ops queue.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-21

Stores work items dispatched to agents (backups, checks, stanza_create).
Agents long-poll ``/api/v1/agents/jobs/next`` which uses
``SELECT ... FOR UPDATE SKIP LOCKED`` so two agents in the same cluster
can race safely. Restore/stanza-delete are NOT in the allowlist and are
blocked at both manager API and agent runner (defense in depth).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "agent_id",
            sa.Integer(),
            sa.ForeignKey("pct.agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "requested_by",
            sa.Integer(),
            sa.ForeignKey("pct.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout_tail", sa.Text(), nullable=True),
        schema="pct",
    )
    op.create_index(
        "ix_jobs_agent_status",
        "jobs",
        ["agent_id", "status"],
        schema="pct",
    )
    op.create_index(
        "ix_jobs_status_created",
        "jobs",
        ["status", "created_at"],
        schema="pct",
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_status_created", table_name="jobs", schema="pct")
    op.drop_index("ix_jobs_agent_status", table_name="jobs", schema="pct")
    op.drop_table("jobs", schema="pct")
