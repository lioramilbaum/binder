"""Create the folders, documents, and document_files tables.

Revision ID: 20260918_02
Revises: 20260918_01
Create Date: 2026-09-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260918_02"
down_revision = "20260918_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create storage for folders, their documents, and document attachments."""

    # Attachment bytes live in large objects (see document_files.data_oid),
    # since a plain column value is capped at 1 GiB. lo_manage keeps large
    # objects in sync with the row that references them: it deletes the old
    # large object whenever data_oid changes or the row is deleted.
    op.execute("CREATE EXTENSION IF NOT EXISTS lo")

    op.create_table(
        "folders",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_folders_updated_at", "folders", ["updated_at"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("folder_id", sa.Text(), nullable=False),
        sa.Column("date", sa.String(length=32), nullable=False),
        sa.Column("time", sa.String(length=16), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
        sa.Column("whatsapp_chat", sa.JSON(), nullable=True),
        sa.Column("whatsapp_chat_file_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["folder_id"], ["folders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documents_folder_id", "documents", ["folder_id"])
    op.create_index("ix_documents_date", "documents", ["date"])

    op.create_table(
        "document_files",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("data_oid", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_files_document_id", "document_files", ["document_id"])
    op.execute(
        "CREATE TRIGGER document_files_lo_trigger BEFORE UPDATE OR DELETE ON document_files "
        "FOR EACH ROW EXECUTE FUNCTION lo_manage(data_oid)"
    )


def downgrade() -> None:
    """Remove folder, document, and attachment storage."""

    op.execute("DROP TRIGGER IF EXISTS document_files_lo_trigger ON document_files")
    op.drop_index("ix_document_files_document_id", table_name="document_files")
    op.drop_table("document_files")
    op.drop_index("ix_documents_date", table_name="documents")
    op.drop_index("ix_documents_folder_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_folders_updated_at", table_name="folders")
    op.drop_table("folders")
    op.execute("DROP EXTENSION IF EXISTS lo")
