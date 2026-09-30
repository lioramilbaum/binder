"""Unify ai_threads into documents with a type field.

Revision ID: 20260930_08
Revises: 20260929_07
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260930_08"
down_revision = "20260929_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Migrate ai_threads into documents with type field."""

    # Add new columns to documents
    op.add_column("documents", sa.Column("type", sa.String(length=16), server_default="regular", nullable=False))
    op.add_column("documents", sa.Column("provider", sa.Text(), server_default="", nullable=False))
    op.add_column("documents", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("imported_at", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("transcript_sha256", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("source_first_message_at", sa.String(length=64), nullable=True))

    # Check if we're using PostgreSQL
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Create check constraint on PostgreSQL
        op.create_check_constraint(
            "ck_documents_type",
            "documents",
            "type IN ('regular', 'ai')"
        )

    # Copy ai_threads rows into documents as type='ai'
    op.execute("""
        INSERT INTO documents (
            id, folder_id, type, date, time, name, description, content, summary,
            created_at, updated_at, provider, source_url, imported_at, transcript_sha256,
            source_first_message_at
        )
        SELECT
            id, folder_id, 'ai', conversation_date, COALESCE(conversation_time, ''),
            title, '', transcript, summary, created_at, created_at, provider,
            source_url, imported_at, transcript_sha256, source_first_message_at
        FROM ai_threads
    """)

    # Drop ai_threads indexes and table
    op.drop_index("ix_ai_threads_folder_chronology", table_name="ai_threads")
    op.drop_index("ix_ai_threads_conversation_date", table_name="ai_threads")
    op.drop_index("ix_ai_threads_folder_id", table_name="ai_threads")
    op.drop_table("ai_threads")


def downgrade() -> None:
    """Restore ai_threads from documents with type='ai'."""

    # Check if any type='ai' document has rows in document_files
    bind = op.get_bind()
    result = bind.execute(sa.text("""
        SELECT COUNT(*) as cnt
        FROM document_files df
        JOIN documents d ON df.document_id = d.id
        WHERE d.type = 'ai'
    """))
    count = result.scalar()
    if count:
        raise RuntimeError(
            f"Cannot downgrade: {count} AI document(s) have file attachments. "
            "Remove attachments before downgrading."
        )

    # Recreate ai_threads table
    op.create_table(
        "ai_threads",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("folder_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("conversation_date", sa.String(length=10), nullable=False),
        sa.Column("conversation_time", sa.String(length=5), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.String(length=64), nullable=True),
        sa.Column("transcript_sha256", sa.String(length=64), nullable=True),
        sa.Column("source_first_message_at", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["folder_id"], ["folders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Copy AI documents back to ai_threads
    op.execute("""
        INSERT INTO ai_threads (
            id, folder_id, title, provider, conversation_date, conversation_time,
            transcript, summary, created_at, source_url, imported_at, transcript_sha256,
            source_first_message_at
        )
        SELECT
            id, folder_id, name, provider, date, NULLIF(time, ''),
            content, summary, created_at, source_url, imported_at, transcript_sha256,
            source_first_message_at
        FROM documents
        WHERE type = 'ai'
    """)

    # Recreate indexes
    op.create_index("ix_ai_threads_folder_id", "ai_threads", ["folder_id"])
    op.create_index("ix_ai_threads_conversation_date", "ai_threads", ["conversation_date"])
    op.create_index(
        "ix_ai_threads_folder_chronology",
        "ai_threads",
        ["folder_id", "conversation_date", "created_at"],
    )

    # Drop check constraint and columns from documents
    if bind.dialect.name == "postgresql":
        op.drop_constraint("ck_documents_type", "documents", type_="check")

    # Delete AI documents from documents table
    op.execute("DELETE FROM documents WHERE type = 'ai'")

    # Drop new columns
    op.drop_column("documents", "source_first_message_at")
    op.drop_column("documents", "transcript_sha256")
    op.drop_column("documents", "imported_at")
    op.drop_column("documents", "source_url")
    op.drop_column("documents", "provider")
    op.drop_column("documents", "type")
