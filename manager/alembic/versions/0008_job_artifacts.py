"""pct.job_artifacts — binary blobs produced by jobs.

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-22

Stores metadata for files uploaded by agents at the end of a job (e.g.
pt-stalk's PostgreSQL collect bundle). The actual bytes live on the
manager filesystem under ``settings.artifacts_dir``; the manager DB
only holds the pointer + integrity info so the table stays tiny even
if individual artifacts run to hundreds of MiB.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("pct.jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column(
            "content_type",
            sa.String(length=127),
            nullable=False,
            server_default=sa.text("'application/gzip'"),
        ),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="pct",
    )
    op.create_index(
        "ix_job_artifacts_job",
        "job_artifacts",
        ["job_id"],
        schema="pct",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_artifacts_job",
        table_name="job_artifacts",
        schema="pct",
    )
    op.drop_table("job_artifacts", schema="pct")
