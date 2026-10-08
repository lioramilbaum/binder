"""Add document links and link types tables.

Revision ID: 20261008_09
Revises: 20260930_08
Create Date: 2026-10-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20261008_09"
down_revision = "20260930_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create link_types and document_links tables."""

    # Create link_types table
    op.create_table(
        "link_types",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )

    # Create document_links table
    op.create_table(
        "document_links",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("source_document_id", sa.Text(), nullable=False),
        sa.Column("target_document_id", sa.Text(), nullable=False),
        sa.Column("link_type_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "source_document_id <> target_document_id",
            name="ck_document_links_not_self",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["link_type_id"],
            ["link_types.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_document_id",
            "target_document_id",
            "link_type_id",
            name="uq_document_links_source_target_type",
        ),
    )

    # Create indexes
    op.create_index("ix_document_links_source", "document_links", ["source_document_id"])
    op.create_index("ix_document_links_target", "document_links", ["target_document_id"])
    op.create_index("ix_document_links_type", "document_links", ["link_type_id"])


def downgrade() -> None:
    """Drop document_links and link_types tables."""

    # Drop indexes
    op.drop_index("ix_document_links_type", table_name="document_links")
    op.drop_index("ix_document_links_target", table_name="document_links")
    op.drop_index("ix_document_links_source", table_name="document_links")

    # Drop tables
    op.drop_table("document_links")
    op.drop_table("link_types")
