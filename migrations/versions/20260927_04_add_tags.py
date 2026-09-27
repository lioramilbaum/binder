"""Add support for tagging documents.

Revision ID: 20260927_04
Revises: 20260921_03
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260927_04"
down_revision = "20260921_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create tags and document_tags tables."""

    op.create_table(
        "tags",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="ix_tags_key"),
    )
    op.create_table(
        "document_tags",
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("tag_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("document_id", "tag_id"),
    )
    op.create_index("ix_document_tags_tag_id", "document_tags", ["tag_id"])


def downgrade() -> None:
    """Drop tags and document_tags tables."""

    op.drop_index("ix_document_tags_tag_id", table_name="document_tags")
    op.drop_table("document_tags")
    op.drop_table("tags")
