"""Add user conversation time and source timestamp to archived AI threads.

Revision ID: 20260929_07
Revises: 20260928_06
Create Date: 2026-09-29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260929_07"
down_revision = "20260928_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_threads", sa.Column("conversation_time", sa.String(length=5), nullable=True))
    op.add_column("ai_threads", sa.Column("source_first_message_at", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_threads", "source_first_message_at")
    op.drop_column("ai_threads", "conversation_time")
