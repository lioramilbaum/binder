"""Add content hashes and let identical attachments share a large object.

Revision ID: 20260921_03
Revises: 20260918_02
Create Date: 2026-09-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260921_03"
down_revision = "20260918_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add sha256, and replace lo_manage with a reference-aware release trigger."""

    op.add_column("document_files", sa.Column("sha256", sa.String(length=64), nullable=True))
    op.create_index("ix_document_files_sha256", "document_files", ["sha256"])
    op.create_index("ix_document_files_data_oid", "document_files", ["data_oid"])

    # lo_manage unlinks the large object on every delete, which is wrong once
    # several rows can share one. Unlink only when no row references it.
    op.execute("DROP TRIGGER IF EXISTS document_files_lo_trigger ON document_files")
    op.execute(
        """
        CREATE FUNCTION document_files_release_lo() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR OLD.data_oid IS DISTINCT FROM NEW.data_oid THEN
            IF NOT EXISTS (SELECT 1 FROM document_files WHERE data_oid = OLD.data_oid) THEN
              PERFORM lo_unlink(OLD.data_oid);
            END IF;
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER document_files_lo_trigger AFTER UPDATE OR DELETE ON document_files "
        "FOR EACH ROW EXECUTE FUNCTION document_files_release_lo()"
    )


def downgrade() -> None:
    """Restore lo_manage, which is only safe while no large object is shared."""

    shared = op.get_bind().execute(
        sa.text("SELECT 1 FROM document_files GROUP BY data_oid HAVING count(*) > 1 LIMIT 1")
    ).first()
    if shared is not None:
        raise RuntimeError(
            "Attachments share large objects; downgrading would delete data still in use."
        )
    op.execute("DROP TRIGGER IF EXISTS document_files_lo_trigger ON document_files")
    op.execute("DROP FUNCTION IF EXISTS document_files_release_lo()")
    op.execute(
        "CREATE TRIGGER document_files_lo_trigger BEFORE UPDATE OR DELETE ON document_files "
        "FOR EACH ROW EXECUTE FUNCTION lo_manage(data_oid)"
    )
    op.drop_index("ix_document_files_data_oid", table_name="document_files")
    op.drop_index("ix_document_files_sha256", table_name="document_files")
    op.drop_column("document_files", "sha256")
