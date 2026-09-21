"""Attachment bytes stored as PostgreSQL large objects (no 1 GiB field limit)."""

from __future__ import annotations

import hashlib
import io
from typing import BinaryIO, Union

from sqlalchemy import text
from sqlalchemy.orm import Session

# Large objects are read/written in chunks well under Postgres's 1 GiB
# per-value limit, which applies to each lowrite/loread call too.
_CHUNK_SIZE = 4 * 1024 * 1024
_INV_WRITE = 0x20000
_INV_READ = 0x40000


# Large objects are shared between attachments with identical bytes, so the
# per-row ``lo_manage`` trigger (which unlinks on every delete) would destroy
# data still in use. This AFTER trigger unlinks an object only once no row
# references it any more. AFTER (not BEFORE) so that rows removed by the same
# statement are already invisible to the check.
RELEASE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION document_files_release_lo() RETURNS trigger AS $$
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
RELEASE_TRIGGER_SQL = (
    "CREATE TRIGGER document_files_lo_trigger AFTER UPDATE OR DELETE ON document_files "
    "FOR EACH ROW EXECUTE FUNCTION document_files_release_lo()"
)


def hash_stream(source: Union[bytes, BinaryIO]) -> tuple[str, int]:
    """Return the SHA-256 hex digest and size of bytes or a seekable stream.

    Streams are read in chunks and rewound afterwards.
    """

    if isinstance(source, bytes):
        return hashlib.sha256(source).hexdigest(), len(source)
    source.seek(0)
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = source.read(_CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    source.seek(0)
    return digest.hexdigest(), size


def create_large_object(session: Session, source: Union[bytes, BinaryIO]) -> int:
    """Store bytes as a new large object, streamed in chunks, returning its oid."""

    stream = io.BytesIO(source) if isinstance(source, bytes) else source
    stream.seek(0)
    oid = session.execute(text("SELECT lo_create(0)")).scalar_one()
    fd = session.execute(
        text("SELECT lo_open(:oid, :mode)"), {"oid": oid, "mode": _INV_WRITE}
    ).scalar_one()
    while True:
        chunk = stream.read(_CHUNK_SIZE)
        if not chunk:
            break
        session.execute(text("SELECT lowrite(:fd, :chunk)"), {"fd": fd, "chunk": chunk})
    session.execute(text("SELECT lo_close(:fd)"), {"fd": fd})
    return oid


def read_large_object(session: Session, oid: int) -> bytes:
    """Return the full bytes of a large object, streamed in chunks."""

    fd = session.execute(
        text("SELECT lo_open(:oid, :mode)"), {"oid": oid, "mode": _INV_READ}
    ).scalar_one()
    chunks: list[bytes] = []
    while True:
        chunk = session.execute(
            text("SELECT loread(:fd, :size)"), {"fd": fd, "size": _CHUNK_SIZE}
        ).scalar_one()
        if not chunk:
            break
        chunks.append(bytes(chunk))
    session.execute(text("SELECT lo_close(:fd)"), {"fd": fd})
    return b"".join(chunks)


def hash_large_object(session: Session, oid: int) -> str:
    """Return the SHA-256 hex digest of a large object without holding it all in memory."""

    fd = session.execute(
        text("SELECT lo_open(:oid, :mode)"), {"oid": oid, "mode": _INV_READ}
    ).scalar_one()
    digest = hashlib.sha256()
    while True:
        chunk = session.execute(
            text("SELECT loread(:fd, :size)"), {"fd": fd, "size": _CHUNK_SIZE}
        ).scalar_one()
        if not chunk:
            break
        digest.update(bytes(chunk))
    session.execute(text("SELECT lo_close(:fd)"), {"fd": fd})
    return digest.hexdigest()


def delete_large_object(session: Session, oid: int) -> None:
    """Remove a large object outright (the release trigger normally does this)."""

    session.execute(text("SELECT lo_unlink(:oid)"), {"oid": oid})
