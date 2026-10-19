"""Add pgvector embedding columns for semantic document search.

Revision ID: 20261019_11
Revises: 20261019_10
Create Date: 2026-10-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20261019_11"
down_revision = "20261019_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Enable pgvector and add the embedding columns to documents."""

    available = op.get_bind().execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
    ).first()
    if available is None:
        raise RuntimeError("PostgreSQL lacks pgvector; rebuild the postgres image (make up)")

    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    op.execute(sa.text("ALTER TABLE documents ADD COLUMN search_embedding vector NULL"))
    op.execute(sa.text("ALTER TABLE documents ADD COLUMN search_embedding_model text NULL"))
    op.execute(
        sa.text("ALTER TABLE documents ADD COLUMN search_embedding_source_updated_at varchar(64) NULL")
    )


def downgrade() -> None:
    """Drop the embedding columns and the vector extension."""

    op.execute(sa.text("ALTER TABLE documents DROP COLUMN IF EXISTS search_embedding_source_updated_at"))
    op.execute(sa.text("ALTER TABLE documents DROP COLUMN IF EXISTS search_embedding_model"))
    op.execute(sa.text("ALTER TABLE documents DROP COLUMN IF EXISTS search_embedding"))
    op.execute(sa.text("DROP EXTENSION IF EXISTS vector"))
