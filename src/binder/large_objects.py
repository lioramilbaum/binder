"""Attachment bytes stored as PostgreSQL large objects (no 1 GiB field limit)."""

from __future__ import annotations

import io
from typing import BinaryIO, Union

from sqlalchemy import text
from sqlalchemy.orm import Session

# Large objects are read/written in chunks well under Postgres's 1 GiB
# per-value limit, which applies to each lowrite/loread call too.
_CHUNK_SIZE = 4 * 1024 * 1024
_INV_WRITE = 0x20000
_INV_READ = 0x40000


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


def delete_large_object(session: Session, oid: int) -> None:
    """Remove a large object outright (the ``lo_manage`` trigger normally does this)."""

    session.execute(text("SELECT lo_unlink(:oid)"), {"oid": oid})
