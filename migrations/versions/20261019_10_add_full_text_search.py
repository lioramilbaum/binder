"""Add PostgreSQL full-text search over documents and attachment text.

Revision ID: 20261019_10
Revises: 20261008_09
Create Date: 2026-10-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20261019_10"
down_revision = "20261008_09"
branch_labels = None
depends_on = None


# Colons inside the POSIX classes are escaped (``\:``) so SQLAlchemy's text()
# does not read them as bind parameters.
BINDER_SEARCH_TEXT_SQL = r"""
CREATE OR REPLACE FUNCTION binder_search_text(value text) RETURNS tsvector
LANGUAGE sql IMMUTABLE AS $$
SELECT to_tsvector('simple', coalesce(value, ''))
  || to_tsvector('simple', regexp_replace(coalesce(value, ''), '(^|[^[\:alnum\:]])[ובהלמשכ]([[\:alpha\:]]{2,})', '\1\2', 'g'))
$$
"""

BINDER_DOCUMENT_SEARCH_VECTOR_SQL = r"""
CREATE OR REPLACE FUNCTION binder_document_search_vector(doc_id text, doc_name text, doc_description text, doc_summary text, doc_content text) RETURNS tsvector
LANGUAGE plpgsql STABLE AS $$
DECLARE
  tag_names text;
  attachment_text text;
  base tsvector;
BEGIN
  SELECT coalesce(string_agg(tags.name, ' '), '') INTO tag_names
    FROM document_tags
    JOIN tags ON tags.id = document_tags.tag_id
    WHERE document_tags.document_id = doc_id;

  SELECT coalesce(string_agg(document_files.text_content, E'\n' ORDER BY document_files.created_at), '') INTO attachment_text
    FROM document_files
    WHERE document_files.document_id = doc_id AND document_files.text_content <> '';

  base := setweight(binder_search_text(doc_name), 'A')
    || setweight(binder_search_text(tag_names), 'A')
    || setweight(binder_search_text(doc_summary), 'B')
    || setweight(binder_search_text(doc_description), 'B');

  BEGIN
    RETURN base
      || setweight(binder_search_text(left(doc_content, 200000)), 'C')
      || setweight(binder_search_text(left(attachment_text, 400000)), 'D');
  EXCEPTION WHEN program_limit_exceeded THEN
    RETURN base;
  END;
END;
$$
"""

DOCUMENTS_SEARCH_VECTOR_SET_SQL = r"""
CREATE OR REPLACE FUNCTION documents_search_vector_set() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_vector := binder_document_search_vector(NEW.id, NEW.name, NEW.description, NEW.summary, NEW.content);
  RETURN NEW;
END;
$$
"""

DOCUMENTS_SEARCH_VECTOR_REFRESH_SQL = r"""
CREATE OR REPLACE FUNCTION documents_search_vector_refresh(doc_id text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE documents
    SET search_vector = binder_document_search_vector(id, name, description, summary, content)
    WHERE id = doc_id;
END;
$$
"""

DOCUMENT_CHILDREN_SEARCH_REFRESH_SQL = r"""
CREATE OR REPLACE FUNCTION document_children_search_refresh() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    PERFORM documents_search_vector_refresh(NEW.document_id);
  ELSE
    PERFORM documents_search_vector_refresh(OLD.document_id);
    IF TG_OP = 'UPDATE' AND NEW.document_id IS DISTINCT FROM OLD.document_id THEN
      PERFORM documents_search_vector_refresh(NEW.document_id);
    END IF;
  END IF;
  RETURN NULL;
END;
$$
"""


def _sql(statement: str) -> sa.TextClause:
    return sa.text(statement)


def upgrade() -> None:
    """Add search columns, trigger-maintained search vectors, and a GIN index."""

    op.add_column("document_files", sa.Column("text_content", sa.Text(), nullable=True))
    op.execute(_sql("ALTER TABLE documents ADD COLUMN search_vector tsvector"))

    op.execute(_sql(BINDER_SEARCH_TEXT_SQL))
    op.execute(_sql(BINDER_DOCUMENT_SEARCH_VECTOR_SQL))
    op.execute(_sql(DOCUMENTS_SEARCH_VECTOR_SET_SQL))
    op.execute(_sql(DOCUMENTS_SEARCH_VECTOR_REFRESH_SQL))
    op.execute(_sql(DOCUMENT_CHILDREN_SEARCH_REFRESH_SQL))

    op.execute(
        _sql(
            """
CREATE OR REPLACE TRIGGER documents_search_vector_set_trigger
BEFORE INSERT OR UPDATE OF name, description, summary, content ON documents
FOR EACH ROW EXECUTE FUNCTION documents_search_vector_set()
"""
        )
    )
    op.execute(
        _sql(
            """
CREATE OR REPLACE TRIGGER document_tags_search_refresh_trigger
AFTER INSERT OR DELETE ON document_tags
FOR EACH ROW EXECUTE FUNCTION document_children_search_refresh()
"""
        )
    )
    op.execute(
        _sql(
            """
CREATE OR REPLACE TRIGGER document_files_search_refresh_trigger
AFTER INSERT OR DELETE OR UPDATE OF text_content, document_id ON document_files
FOR EACH ROW EXECUTE FUNCTION document_children_search_refresh()
"""
        )
    )

    op.execute(_sql("CREATE INDEX ix_documents_search_vector ON documents USING gin (search_vector)"))
    op.execute(
        _sql(
            "UPDATE documents SET search_vector = "
            "binder_document_search_vector(id, name, description, summary, content)"
        )
    )


def downgrade() -> None:
    """Drop search triggers, functions, the index, and the search columns."""

    op.execute(_sql("DROP TRIGGER IF EXISTS document_files_search_refresh_trigger ON document_files"))
    op.execute(_sql("DROP TRIGGER IF EXISTS document_tags_search_refresh_trigger ON document_tags"))
    op.execute(_sql("DROP TRIGGER IF EXISTS documents_search_vector_set_trigger ON documents"))
    op.execute(_sql("DROP FUNCTION IF EXISTS document_children_search_refresh()"))
    op.execute(_sql("DROP FUNCTION IF EXISTS documents_search_vector_refresh(text)"))
    op.execute(_sql("DROP FUNCTION IF EXISTS documents_search_vector_set()"))
    op.execute(_sql("DROP FUNCTION IF EXISTS binder_document_search_vector(text, text, text, text, text)"))
    op.execute(_sql("DROP FUNCTION IF EXISTS binder_search_text(text)"))
    op.execute(_sql("DROP INDEX IF EXISTS ix_documents_search_vector"))
    op.drop_column("documents", "search_vector")
    op.drop_column("document_files", "text_content")
