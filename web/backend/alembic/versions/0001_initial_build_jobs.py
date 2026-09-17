"""initial build_jobs table

Revision ID: 0001
Revises:
Create Date: 2026-09-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "build_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("celery_task_id", sa.String(255), nullable=True, index=True),
        sa.Column("log_tail", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("vm_ip", sa.String(64), nullable=True),
        sa.Column("vm_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("build_jobs")
