"""Record imported AI thread source and transcript hash.

Revision ID: 20260928_06
Revises: 20260928_05
Create Date: 2026-09-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260928_06"
down_revision = "20260928_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_threads", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column("ai_threads", sa.Column("imported_at", sa.String(length=64), nullable=True))
    op.add_column("ai_threads", sa.Column("transcript_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_threads", "transcript_sha256")
    op.drop_column("ai_threads", "imported_at")
    op.drop_column("ai_threads", "source_url")
