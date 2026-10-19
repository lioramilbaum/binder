"""Folder, document, and attachment models."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, ForeignKey, Integer, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# Document type constants
DOCUMENT_TYPE_REGULAR = "regular"
DOCUMENT_TYPE_AI = "ai"
DOCUMENT_TYPES = {DOCUMENT_TYPE_REGULAR, DOCUMENT_TYPE_AI}

# Content length limits
MAX_SUMMARY_LENGTH = {"regular": 1000, "ai": 10_000}
MAX_TRANSCRIPT_LENGTH = 200_000
MAX_PROVIDER_LENGTH = 100
MAX_LINK_TYPE_LENGTH = 50


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


class Document(Base):
    """A single document stored in a folder.

    ``documents.search_vector`` and ``documents.search_embedding*`` exist only in
    PostgreSQL (maintained by triggers and the search/embedding code, see
    migrations 10 and 11). They are intentionally unmapped so SQLite
    ``create_all`` keeps working.
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    folder_id: Mapped[str] = mapped_column(ForeignKey("folders.id"), index=True)
    type: Mapped[str] = mapped_column(String(16), default=DOCUMENT_TYPE_REGULAR, server_default=DOCUMENT_TYPE_REGULAR, nullable=False)
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
    provider: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transcript_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_first_message_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (CheckConstraint("type IN ('regular', 'ai')", name="ck_documents_type"),)

    folder: Mapped[Folder] = relationship(back_populates="documents")
    files: Mapped[list["DocumentFile"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentFile.created_at",
    )
    tags: Mapped[list["Tag"]] = relationship(
        secondary=document_tags, back_populates="documents", order_by="Tag.key"
    )
    outgoing_links: Mapped[list["DocumentLink"]] = relationship(
        back_populates="source",
        foreign_keys="DocumentLink.source_document_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentLink.created_at",
    )
    incoming_links: Mapped[list["DocumentLink"]] = relationship(
        back_populates="target",
        foreign_keys="DocumentLink.target_document_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentLink.created_at",
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
    # Text extracted for search at upload time; ``""`` when the type has no
    # extractor, ``None`` when extraction has not run yet. Deferred so listing
    # documents never loads it.
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True, default=None, deferred=True)
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


class LinkType(Base):
    """A global link type that can connect documents."""

    __tablename__ = "link_types"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


class DocumentLink(Base):
    """A link between two documents of a specific type."""

    __tablename__ = "document_links"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    target_document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    link_type_id: Mapped[str] = mapped_column(ForeignKey("link_types.id"), index=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "source_document_id <> target_document_id",
            name="ck_document_links_not_self",
        ),
        UniqueConstraint(
            "source_document_id",
            "target_document_id",
            "link_type_id",
            name="uq_document_links_source_target_type",
        ),
    )

    source: Mapped[Document] = relationship(
        back_populates="outgoing_links", foreign_keys=[source_document_id]
    )
    target: Mapped[Document] = relationship(
        back_populates="incoming_links", foreign_keys=[target_document_id]
    )
    link_type: Mapped[LinkType] = relationship()
