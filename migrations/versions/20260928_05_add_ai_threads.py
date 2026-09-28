"""Add manually archived AI conversations.

Revision ID: 20260928_05
Revises: 20260927_04
Create Date: 2026-09-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260928_05"
down_revision = "20260927_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_threads",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("folder_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("conversation_date", sa.String(length=10), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["folder_id"], ["folders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_threads_folder_id", "ai_threads", ["folder_id"])
    op.create_index("ix_ai_threads_conversation_date", "ai_threads", ["conversation_date"])
    op.create_index(
        "ix_ai_threads_folder_chronology",
        "ai_threads",
        ["folder_id", "conversation_date", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_threads_folder_chronology", table_name="ai_threads")
    op.drop_index("ix_ai_threads_conversation_date", table_name="ai_threads")
    op.drop_index("ix_ai_threads_folder_id", table_name="ai_threads")
    op.drop_table("ai_threads")
