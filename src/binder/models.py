"""Folder, document, and attachment models."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Column, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


document_tags = Table(
    "document_tags",
    Base.metadata,
    Column("document_id", Text, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Text, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class Folder(Base):
    """A top-level collection of documents."""

    __tablename__ = "folders"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[str] = mapped_column(String(64), index=True)

    documents: Mapped[list["Document"]] = relationship(
        back_populates="folder", cascade="all, delete-orphan"
    )
    ai_threads: Mapped[list["AiThread"]] = relationship(
        back_populates="folder", cascade="all, delete-orphan"
    )


class AiThread(Base):
    """An archived historical AI conversation."""

    __tablename__ = "ai_threads"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    folder_id: Mapped[str] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, default="", nullable=False)
    conversation_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transcript_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    folder: Mapped[Folder] = relationship(back_populates="ai_threads")


class Document(Base):
    """A single document stored in a folder."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    folder_id: Mapped[str] = mapped_column(ForeignKey("folders.id"), index=True)
    date: Mapped[str] = mapped_column(String(32), index=True)
    time: Mapped[str] = mapped_column(String(16), default="")
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[str] = mapped_column(String(64))
    whatsapp_chat: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    whatsapp_chat_file_id: Mapped[str | None] = mapped_column(Text, default=None)

    folder: Mapped[Folder] = relationship(back_populates="documents")
    files: Mapped[list["DocumentFile"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentFile.created_at",
    )
    tags: Mapped[list["Tag"]] = relationship(
        secondary=document_tags, back_populates="documents", order_by="Tag.key"
    )


class DocumentFile(Base):
    """An attachment's metadata, owned by a document.

    The bytes themselves live in a PostgreSQL large object (referenced by
    ``data_oid``), not in this row: a plain column value is capped at 1 GiB,
    and attachments here can exceed that. Rows with the same ``sha256`` share
    one large object.
    """

    __tablename__ = "document_files"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    name: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    # Identical attachments may share one large object, so several rows can
    # point at the same oid; the database releases it once the last one goes.
    data_oid: Mapped[int] = mapped_column(Integer, index=True)
    # SHA-256 of the bytes; ``None`` for rows created before hashing existed
    # (``binder-dedupe backfill`` fills them in).
    sha256: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    created_at: Mapped[str] = mapped_column(String(64))

    document: Mapped[Document] = relationship(back_populates="files")


class Tag(Base):
    """A global tag that can be attached to documents."""

    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)

    documents: Mapped[list["Document"]] = relationship(
        secondary=document_tags, back_populates="tags"
    )
