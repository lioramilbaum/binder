"""Document search: PostgreSQL full-text keyword matching fused with semantic matches.

Keyword results come from ``documents.search_vector`` (kept current by triggers).
Semantic results come from pgvector embeddings. The two rankings are merged with
reciprocal rank fusion, and keyword search keeps working when Ollama is down.

Run ``binder-search`` to backfill attachment text, rebuild search vectors, and embed
documents.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from .config import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, OLLAMA_BASE_URL
from .database import create_session_factory, session_scope
from .embeddings import (
    EmbeddingUnavailable,
    OllamaClient,
    embed_pending,
    pending_document_ids,
    vector_literal,
)
from .large_objects import read_large_object
from .models import DocumentFile
from .text_extraction import extract_text, is_extractable


MAX_QUERY_LENGTH = 200
MAX_QUERY_TERMS = 16
MIN_SEMANTIC_QUERY_LENGTH = 3
CANDIDATE_LIMIT = 100

# Kept in step with migration 20261019_10 for Postgres test setup. Colons inside
# the POSIX character classes are escaped so ``sqlalchemy.text`` leaves them alone.
SEARCH_SCHEMA_SQL: list[str] = [
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS search_vector tsvector",
    r"""
CREATE OR REPLACE FUNCTION binder_search_text(value text) RETURNS tsvector
LANGUAGE sql IMMUTABLE AS $$
SELECT to_tsvector('simple', coalesce(value, ''))
  || to_tsvector('simple', regexp_replace(coalesce(value, ''), '(^|[^[\:alnum\:]])[ובהלמשכ]([[\:alpha\:]]{2,})', '\1\2', 'g'))
$$
""",
    r"""
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
""",
    r"""
CREATE OR REPLACE FUNCTION documents_search_vector_set() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_vector := binder_document_search_vector(NEW.id, NEW.name, NEW.description, NEW.summary, NEW.content);
  RETURN NEW;
END;
$$
""",
    r"""
CREATE OR REPLACE FUNCTION documents_search_vector_refresh(doc_id text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE documents
    SET search_vector = binder_document_search_vector(id, name, description, summary, content)
    WHERE id = doc_id;
END;
$$
""",
    r"""
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
""",
    """
CREATE OR REPLACE TRIGGER documents_search_vector_set_trigger
BEFORE INSERT OR UPDATE OF name, description, summary, content ON documents
FOR EACH ROW EXECUTE FUNCTION documents_search_vector_set()
""",
    """
CREATE OR REPLACE TRIGGER document_tags_search_refresh_trigger
AFTER INSERT OR DELETE ON document_tags
FOR EACH ROW EXECUTE FUNCTION document_children_search_refresh()
""",
    """
CREATE OR REPLACE TRIGGER document_files_search_refresh_trigger
AFTER INSERT OR DELETE OR UPDATE OF text_content, document_id ON document_files
FOR EACH ROW EXECUTE FUNCTION document_children_search_refresh()
""",
    "CREATE INDEX IF NOT EXISTS ix_documents_search_vector ON documents USING gin (search_vector)",
]


def build_tsquery(raw: str) -> str | None:
    """Turn free text into a prefix-matching tsquery, or ``None`` if it has no words.

    Only word characters are kept, so user input cannot inject tsquery operators.
    """

    terms = re.findall(r"[^\W_]+", raw)[:MAX_QUERY_TERMS]
    if not terms:
        return None
    return " & ".join(f"{term}:*" for term in terms)


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Merge ranked id lists by summing ``1 / (k + rank)`` for each list an id appears in.

    Ties are broken by first appearance (earlier lists first), then by id.
    """

    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    position = 0
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
            if item_id not in first_seen:
                first_seen[item_id] = position
            position += 1
    return sorted(scores.items(), key=lambda pair: (-pair[1], first_seen[pair[0]], pair[0]))


def semantic_candidates(
    session: Session,
    folder_id: str | None,
    query_vector: list[float],
    *,
    model: str,
    dims: int,
    limit: int,
    min_similarity: float,
) -> list[tuple[str, float]]:
    """Return (id, cosine similarity) for documents embedded with ``model`` at ``dims``.

    The dimension and model filters run in the inner query, so the distance
    operator never sees vectors of a different size.
    """

    folder_clause = " AND d.folder_id = :folder_id" if folder_id is not None else ""
    params: dict[str, object] = {
        "q": vector_literal(query_vector),
        "model": model,
        "dims": dims,
        "min_sim": min_similarity,
        "limit": limit,
    }
    if folder_id is not None:
        params["folder_id"] = folder_id
    rows = session.execute(
        text(
            "SELECT candidates.id, candidates.similarity FROM ("
            "  SELECT d.id, 1 - (d.search_embedding <=> CAST(:q AS vector)) AS similarity"
            "  FROM documents d"
            "  WHERE d.search_embedding_model = :model"
            "    AND vector_dims(d.search_embedding) = :dims"
            f"{folder_clause}"
            ") AS candidates "
            "WHERE candidates.similarity >= :min_sim "
            "ORDER BY candidates.similarity DESC LIMIT :limit"
        ),
        params,
    )
    return [(row[0], float(row[1])) for row in rows]


def backfill_attachment_text(factory: sessionmaker[Session]) -> int:
    """Extract text for stored attachments that lack it. Returns how many objects were processed.

    Each large object gets its own transaction. Unsupported or oversized files
    are marked with ``""`` so they are not retried.
    """

    with factory() as session:
        oids = [
            row[0]
            for row in session.query(DocumentFile.data_oid)
            .filter(DocumentFile.text_content.is_(None))
            .distinct()
            .all()
        ]

    processed = 0
    for oid in oids:
        with session_scope(factory) as session:
            row = (
                session.query(DocumentFile.name, DocumentFile.content_type, DocumentFile.size)
                .filter(DocumentFile.data_oid == oid)
                .first()
            )
            if row is None:
                continue
            name, content_type, size = row
            if is_extractable(name, content_type, size):
                data = read_large_object(session, oid)
                value = extract_text(name, content_type, data, len(data))
            else:
                value = ""
            session.query(DocumentFile).filter(
                DocumentFile.data_oid == oid, DocumentFile.text_content.is_(None)
            ).update({DocumentFile.text_content: value}, synchronize_session=False)
        processed += 1
    return processed


def _rebuild_search_vectors(factory: sessionmaker[Session]) -> int:
    with session_scope(factory) as session:
        result = session.execute(
            text(
                "UPDATE documents SET search_vector = "
                "binder_document_search_vector(id, name, description, summary, content)"
            )
        )
        return result.rowcount


def _print_status(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        documents, indexed, embedded = session.execute(
            text(
                "SELECT count(*), count(search_vector), count(search_embedding) FROM documents"
            )
        ).one()
        files_pending = session.execute(
            text("SELECT count(*) FILTER (WHERE text_content IS NULL) FROM document_files")
        ).scalar_one()
        pending = len(pending_document_ids(session, EMBEDDING_MODEL))
    print(f"Documents: {documents} ({indexed} keyword-indexed)")
    print(f"Attachments awaiting text extraction: {files_pending}")
    print(f"Embeddings ({EMBEDDING_MODEL}): {embedded} stored, {pending} pending")


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point: backfill, embed, or show index status."""

    parser = argparse.ArgumentParser(prog="binder-search", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    backfill = commands.add_parser(
        "backfill", help="extract attachment text, rebuild search vectors, and embed documents"
    )
    backfill.add_argument(
        "--skip-embeddings", action="store_true", help="do not call Ollama"
    )

    embed = commands.add_parser("embed", help="embed documents that are missing or stale")
    embed.add_argument("--all", action="store_true", help="re-embed every document")

    commands.add_parser("status", help="show keyword index and embedding coverage")

    args = parser.parse_args(argv)
    factory = create_session_factory()

    if args.command == "status":
        _print_status(factory)
        return 0

    if args.command == "backfill":
        print(f"Extracted text for {backfill_attachment_text(factory)} attachment object(s).")
        print(f"Rebuilt search vectors for {_rebuild_search_vectors(factory)} document(s).")
        if args.skip_embeddings:
            return 0

    client = OllamaClient(OLLAMA_BASE_URL, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS)
    try:
        embedded, skipped = embed_pending(factory, client, force=args.command == "embed" and getattr(args, "all", False))
    except EmbeddingUnavailable as error:
        print(f"Embedding failed: {error}", file=sys.stderr)
        return 1
    print(f"Embedded {embedded} document(s); skipped {skipped}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
