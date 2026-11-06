"""user_notification_prefs table for per-user build email preferences

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_notification_prefs",
        sa.Column("username", sa.String(255), primary_key=True),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("on_success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("on_failure", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("on_cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_notification_prefs")
