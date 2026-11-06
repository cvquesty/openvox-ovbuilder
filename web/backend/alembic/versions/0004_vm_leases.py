"""vm_leases table for VM lifecycle leases and reclaim tracking

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-06
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vm_leases",
        sa.Column("vm_name", sa.String(255), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("explicit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("powered_off_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_action", sa.String(32), nullable=True),
        sa.Column("last_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("vm_leases")
