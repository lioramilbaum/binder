"""Document embeddings from a local Ollama model, stored in PostgreSQL with pgvector.

Embedding is best-effort: when Ollama is down, searches fall back to keyword
matching and pending documents are retried by a background worker.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.error
import urllib.request
from fnmatch import fnmatch
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .config import EMBEDDING_MAX_CHARS, OLLAMA_DOCUMENT_TIMEOUT
from .database import session_scope
from .models import Document, DocumentFile


logger = logging.getLogger(__name__)

# Kept in step with migration 20261019_11 for Postgres test setup.
SEMANTIC_SCHEMA_SQL = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS search_embedding vector",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS search_embedding_model text",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS search_embedding_source_updated_at varchar(64)",
]

_CIRCUIT_COOLDOWN_SECONDS = 30

# Task prefixes some models expect, keyed by model name pattern.
_MODEL_PREFIXES: dict[str, tuple[str, str]] = {
    "bge-m3*": ("", ""),
    "nomic-embed-text*": ("search_query: ", "search_document: "),
}


class EmbeddingUnavailable(Exception):
    """Raised when embeddings cannot be computed right now."""


def _prefixes_for(model: str) -> tuple[str, str]:
    for pattern, prefixes in _MODEL_PREFIXES.items():
        if fnmatch(model, pattern):
            return prefixes
    return ("", "")


class OllamaClient:
    """Minimal client for Ollama's ``/api/embed`` endpoint with a short circuit breaker."""

    def __init__(self, base_url: str, model: str, dimensions: int):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self._lock = threading.Lock()
        self._down_until = 0.0

    def embed(self, texts: list[str], *, kind: str = "doc", timeout: int) -> list[list[float]]:
        with self._lock:
            if time.monotonic() < self._down_until:
                raise EmbeddingUnavailable("Ollama was unreachable recently; skipping")

        query_prefix, doc_prefix = _prefixes_for(self.model)
        prefix = query_prefix if kind == "query" else doc_prefix
        body = json.dumps(
            {"model": self.model, "input": [prefix + item for item in texts], "truncate": True}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return self._parse(payload, len(texts))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as error:
            with self._lock:
                self._down_until = time.monotonic() + _CIRCUIT_COOLDOWN_SECONDS
            raise EmbeddingUnavailable(f"Ollama embedding request failed: {error}") from error

    def _parse(self, payload: object, count: int) -> list[list[float]]:
        embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != count:
            raise ValueError("unexpected embedding count")
        result: list[list[float]] = []
        for vector in embeddings:
            if not isinstance(vector, list) or len(vector) != self.dimensions:
                raise ValueError("unexpected embedding dimensions")
            try:
                values = [float(value) for value in vector]
            except (TypeError, ValueError) as error:
                raise ValueError("non-numeric embedding value") from error
            if not all(math.isfinite(value) for value in values):
                raise ValueError("non-finite embedding value")
            result.append(values)
        return result


def vector_literal(values: Sequence[float]) -> str:
    """Render floats as a pgvector literal such as ``[0.1,0.2]``."""

    return "[" + ",".join(repr(float(value)) for value in values) + "]"


def embedding_text(document: dict[str, object], attachment_texts: Sequence[str]) -> str:
    """Build the text embedded for a document, most important fields first.

    The name, description, summary and tags come before the body, so truncation
    to ``EMBEDDING_MAX_CHARS`` never drops them.
    """

    tags = document.get("tags") or []
    parts = [
        str(document.get("name") or ""),
        str(document.get("description") or ""),
        str(document.get("summary") or ""),
        ", ".join(str(tag) for tag in tags),
        str(document.get("content") or ""),
        *attachment_texts,
    ]
    return "\n".join(part for part in parts if part)[:EMBEDDING_MAX_CHARS]


def pending_document_ids(session: Session, model: str) -> list[str]:
    """Return ids of documents whose embedding is missing, from another model, or stale."""

    rows = session.execute(
        text(
            """
            SELECT id FROM documents
            WHERE search_embedding IS NULL
               OR search_embedding_model IS DISTINCT FROM :model
               OR search_embedding_source_updated_at IS DISTINCT FROM updated_at
            ORDER BY updated_at DESC, id
            """
        ),
        {"model": model},
    )
    return [row[0] for row in rows]


def embed_document(factory: sessionmaker[Session], client: OllamaClient, document_id: str) -> bool:
    """Embed one document. Returns False if it vanished or changed while embedding."""

    with factory() as session:
        document = (
            session.query(Document)
            .options(selectinload(Document.tags))
            .filter(Document.id == document_id)
            .one_or_none()
        )
        if document is None:
            return False
        attachment_texts = [
            row[0]
            for row in session.query(DocumentFile.text_content)
            .filter(
                DocumentFile.document_id == document_id,
                DocumentFile.text_content.is_not(None),
                DocumentFile.text_content != "",
            )
            .order_by(DocumentFile.created_at)
            .all()
        ]
        source = {
            "name": document.name,
            "description": document.description,
            "summary": document.summary,
            "tags": [tag.name for tag in document.tags],
            "content": document.content,
        }
        updated_at = document.updated_at

    vector = client.embed(
        [embedding_text(source, attachment_texts)], kind="doc", timeout=OLLAMA_DOCUMENT_TIMEOUT
    )[0]

    with session_scope(factory) as session:
        result = session.execute(
            text(
                """
                UPDATE documents
                SET search_embedding = CAST(:vec AS vector),
                    search_embedding_model = :model,
                    search_embedding_source_updated_at = :updated_at
                WHERE id = :id AND updated_at = :updated_at
                """
            ),
            {"vec": vector_literal(vector), "model": client.model, "updated_at": updated_at, "id": document_id},
        )
        return result.rowcount == 1


def embed_pending(
    factory: sessionmaker[Session],
    client: OllamaClient,
    *,
    limit: int | None = None,
    force: bool = False,
) -> tuple[int, int]:
    """Embed pending documents (all of them when ``force``). Returns (embedded, skipped).

    ``EmbeddingUnavailable`` propagates so callers can stop early.
    """

    with factory() as session:
        if force:
            ids = [row[0] for row in session.execute(text("SELECT id FROM documents ORDER BY updated_at DESC, id"))]
        else:
            ids = pending_document_ids(session, client.model)
    if limit is not None:
        ids = ids[:limit]

    embedded = 0
    skipped = 0
    for document_id in ids:
        if embed_document(factory, client, document_id):
            embedded += 1
        else:
            skipped += 1
    return embedded, skipped


class EmbeddingWorker(threading.Thread):
    """Background thread that embeds pending documents after changes and periodically."""

    # Seconds to let a burst of edits settle before embedding.
    debounce_seconds = 2

    def __init__(self, factory: sessionmaker[Session], client: OllamaClient):
        super().__init__(name="binder-embeddings", daemon=True)
        self._factory = factory
        self._client = client
        self._event = threading.Event()
        # Set at start so the backlog left over from before this process runs.
        self._event.set()
        # Not named ``_stop``: threading.Thread uses that name internally.
        self._stop_requested = False
        self._outage_logged = False

    def notify(self) -> None:
        self._event.set()

    def stop(self) -> None:
        self._stop_requested = True
        self.notify()
        self.join()

    def run(self) -> None:
        while not self._stop_requested:
            self._event.wait(timeout=60)
            self._event.clear()
            if self._stop_requested:
                break
            time.sleep(self.debounce_seconds)
            try:
                embedded, skipped = embed_pending(self._factory, self._client)
                self._outage_logged = False
                if embedded or skipped:
                    logger.info("Embedded %d document(s), skipped %d", embedded, skipped)
            except EmbeddingUnavailable as error:
                if not self._outage_logged:
                    logger.warning("Embeddings paused: %s", error)
                    self._outage_logged = True
            except Exception:
                logger.exception("Embedding worker failed; will retry")
