"""Database persistence for folders, documents, and their attachments."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Sequence, Union
from uuid import uuid4

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .config import OLLAMA_QUERY_TIMEOUT, SEMANTIC_MIN_SIMILARITY
from .database import session_scope
from .embeddings import EmbeddingUnavailable, OllamaClient, pending_document_ids
from .large_objects import create_large_object, hash_stream, read_large_object
from .search import (
    CANDIDATE_LIMIT,
    MIN_SEMANTIC_QUERY_LENGTH,
    build_tsquery,
    reciprocal_rank_fusion,
    semantic_candidates,
)
from .models import (
    DOCUMENT_TYPE_AI,
    DOCUMENT_TYPE_REGULAR,
    MAX_SUMMARY_LENGTH,
    MAX_TRANSCRIPT_LENGTH,
    Document,
    DocumentFile,
    DocumentLink,
    Folder,
    LinkType,
    Tag,
)
from .tags import normalize_tags, tag_key
from .text_extraction import extract_text
from .whatsapp import WhatsAppChat


class DocumentRuleError(ValueError):
    """Raised when a document operation violates business rules."""
    pass


class LinkError(ValueError):
    """Raised when a document link operation fails."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_tags(session: Session, names: list[str]) -> list[Tag]:
    """Get or create tags by name, returning them in input order.

    Names are normalized first. Tags are queried by their case-insensitive key.
    Missing tags are created and added to the session.

    Handles concurrent tag creation via savepoints: if another transaction
    creates the same tag before we commit, we catch the constraint violation
    and fetch the tag that was created by the other transaction.
    """
    normalized = normalize_tags(names)
    if not normalized:
        return []

    # Query existing tags by key
    keys = [tag_key(name) for name in normalized]
    existing = {
        tag.key: tag
        for tag in session.query(Tag).filter(Tag.key.in_(keys)).all()
    }

    # Create missing tags, handling concurrent inserts via savepoints
    now = _now()
    for name in normalized:
        key = tag_key(name)
        if key not in existing:
            try:
                # Use a savepoint so we can catch and recover from conflicts
                # without rolling back the entire transaction (e.g., the document
                # being created in the caller)
                with session.begin_nested():
                    tag = Tag(
                        id=str(uuid4()),
                        name=name,
                        key=key,
                        created_at=now,
                    )
                    session.add(tag)
                existing[key] = tag
            except IntegrityError:
                # Another concurrent request created this tag; fetch it
                tag = session.query(Tag).filter_by(key=key).one()
                existing[key] = tag

    return [existing[k] for k in keys]


def effective_content_type(content_type: str, name: str) -> str:
    """PDFs uploaded as generic binary are still served as PDFs so browsers can show them inline."""
    if content_type == "application/octet-stream" and Path(name).suffix.lower() == ".pdf":
        return "application/pdf"
    return content_type


def _file_to_dict(file: DocumentFile) -> dict[str, object]:
    return {
        "id": file.id,
        "name": file.name,
        "content_type": effective_content_type(file.content_type, file.name),
        "size": file.size,
    }


def _document_summary(document: Document) -> dict[str, object]:
    return {
        "id": document.id,
        "type": document.type,
        "date": document.date,
        "time": document.time,
        "name": document.name,
        "description": document.description,
        "summary": document.summary,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "tags": [tag.name for tag in document.tags],
    }


def _document_to_dict(document: Document) -> dict[str, object]:
    result = _document_summary(document)
    result["content"] = document.content
    result["files"] = [_file_to_dict(file) for file in document.files]
    if document.whatsapp_chat is not None:
        result["whatsapp_chat"] = document.whatsapp_chat
    if document.whatsapp_chat_file_id is not None:
        result["whatsapp_chat_file_id"] = document.whatsapp_chat_file_id

    # Add AI-specific fields for AI documents
    if document.type == DOCUMENT_TYPE_AI:
        if document.provider:
            result["provider"] = document.provider
        if document.source_url:
            result["source_url"] = document.source_url
        if document.imported_at:
            result["imported_at"] = document.imported_at
        if document.transcript_sha256:
            result["transcript_sha256"] = document.transcript_sha256
        if document.source_first_message_at:
            result["source_first_message_at"] = document.source_first_message_at

    # Add links
    links = []
    for link in document.outgoing_links:
        links.append(_link_to_dict(link, "outgoing"))
    for link in document.incoming_links:
        links.append(_link_to_dict(link, "incoming"))
    if links:
        result["links"] = links

    return result


def _document_sort_key(document: Document) -> tuple[str, str, str]:
    return (document.date or "", document.time or "", document.created_at or "")


def list_folders(factory: sessionmaker[Session]) -> list[dict[str, object]]:
    """Return all folders, newest-updated first, with lightweight document summaries."""

    with factory() as session:
        folders = (
            session.query(Folder)
            .options(selectinload(Folder.documents).selectinload(Document.tags))
            .order_by(Folder.updated_at.desc())
            .all()
        )
        return [
            {
                "id": folder.id,
                "name": folder.name,
                "description": folder.description,
                "created_at": folder.created_at,
                "updated_at": folder.updated_at,
                "documents": [_document_summary(document) for document in folder.documents],
            }
            for folder in folders
        ]


def get_folder(factory: sessionmaker[Session], folder_id: str) -> dict[str, object] | None:
    """Return a folder with its documents fully populated, sorted newest first."""

    with factory() as session:
        folder = (
            session.query(Folder)
            .options(
                selectinload(Folder.documents)
                .selectinload(Document.files)
            )
            .options(
                selectinload(Folder.documents)
                .selectinload(Document.tags)
            )
            .options(
                selectinload(Folder.documents)
                .selectinload(Document.outgoing_links)
                .selectinload(DocumentLink.target)
                .selectinload(Document.folder)
            )
            .options(
                selectinload(Folder.documents)
                .selectinload(Document.outgoing_links)
                .selectinload(DocumentLink.link_type)
            )
            .options(
                selectinload(Folder.documents)
                .selectinload(Document.incoming_links)
                .selectinload(DocumentLink.source)
                .selectinload(Document.folder)
            )
            .options(
                selectinload(Folder.documents)
                .selectinload(Document.incoming_links)
                .selectinload(DocumentLink.link_type)
            )
            .filter(Folder.id == folder_id)
            .one_or_none()
        )
        if folder is None:
            return None
        documents = sorted(folder.documents, key=_document_sort_key, reverse=True)
        return {
            "id": folder.id,
            "name": folder.name,
            "description": folder.description,
            "created_at": folder.created_at,
            "updated_at": folder.updated_at,
            "documents": [_document_to_dict(document) for document in documents],
        }


def create_folder(
    factory: sessionmaker[Session], name: str, description: str = ""
) -> dict[str, object]:
    """Create a new, empty folder."""

    now = _now()
    folder = Folder(
        id=str(uuid4()),
        name=name.strip(),
        description=description.strip(),
        created_at=now,
        updated_at=now,
    )
    with session_scope(factory) as session:
        session.add(folder)
    return {
        "id": folder.id,
        "name": folder.name,
        "description": folder.description,
        "created_at": folder.created_at,
        "updated_at": folder.updated_at,
        "documents": [],
    }


def create_document(
    factory: sessionmaker[Session],
    folder_id: str,
    date: str,
    name: str,
    description: str = "",
    content: str = "",
    time: str = "",
    summary: str = "",
    tags: Sequence[str] = (),
    document_type: str = DOCUMENT_TYPE_REGULAR,
    provider: str = "",
    source_url: str | None = None,
    transcript_sha256: str | None = None,
    source_first_message_at: str | None = None,
) -> dict[str, object] | None:
    """Create a document in a folder, or return ``None`` if the folder is missing."""

    now = _now()
    with session_scope(factory) as session:
        folder = session.get(Folder, folder_id)
        if folder is None:
            return None

        # For AI documents, keep content verbatim; for regular, strip whitespace
        processed_content = content if document_type == DOCUMENT_TYPE_AI else content.strip()

        document = Document(
            id=str(uuid4()),
            folder_id=folder_id,
            type=document_type,
            date=date,
            time=time,
            name=name.strip(),
            description=description.strip(),
            content=processed_content,
            summary=summary.strip(),
            created_at=now,
            updated_at=now,
            provider=provider,
            source_url=source_url,
            imported_at=now if source_url else None,
            transcript_sha256=transcript_sha256,
            source_first_message_at=source_first_message_at,
        )
        session.add(document)
        document.tags = _resolve_tags(session, list(tags))
        folder.updated_at = now
        session.flush()
        # Reload document with eager-loaded relationships to avoid N+1 queries
        document = (
            session.query(Document)
            .options(selectinload(Document.folder))
            .options(
                selectinload(Document.outgoing_links)
                .selectinload(DocumentLink.target)
                .selectinload(Document.folder)
            )
            .options(
                selectinload(Document.outgoing_links)
                .selectinload(DocumentLink.link_type)
            )
            .options(
                selectinload(Document.incoming_links)
                .selectinload(DocumentLink.source)
                .selectinload(Document.folder)
            )
            .options(
                selectinload(Document.incoming_links)
                .selectinload(DocumentLink.link_type)
            )
            .options(selectinload(Document.files))
            .options(selectinload(Document.tags))
            .filter(Document.id == document.id)
            .one()
        )
        return _document_to_dict(document)


def update_document(
    factory: sessionmaker[Session],
    folder_id: str,
    document_id: str,
    updates: dict[str, str | list[str]],
) -> dict[str, object] | None:
    """Apply field updates to a document, or return ``None`` if it doesn't exist.

    Raises DocumentRuleError for constraint violations.
    """

    with session_scope(factory) as session:
        document = (
            session.query(Document)
            .options(selectinload(Document.folder))
            .options(selectinload(Document.files))
            .options(selectinload(Document.tags))
            .options(
                selectinload(Document.outgoing_links)
                .selectinload(DocumentLink.target)
                .selectinload(Document.folder)
            )
            .options(
                selectinload(Document.outgoing_links)
                .selectinload(DocumentLink.link_type)
            )
            .options(
                selectinload(Document.incoming_links)
                .selectinload(DocumentLink.source)
                .selectinload(Document.folder)
            )
            .options(
                selectinload(Document.incoming_links)
                .selectinload(DocumentLink.link_type)
            )
            .filter(Document.id == document_id, Document.folder_id == folder_id)
            .one_or_none()
        )
        if document is None:
            return None

        # Check business rules before applying updates
        if "provider" in updates:
            new_provider = str(updates["provider"]).strip() if updates["provider"] else ""
            # Non-empty provider only allowed on AI documents
            if new_provider and document.type == DOCUMENT_TYPE_REGULAR:
                raise DocumentRuleError("Provider can only be set on AI documents")

        # Check if trying to change imported transcript (source_url indicates imported)
        if "content" in updates and document.source_url:
            raise DocumentRuleError("Cannot modify transcript of imported AI document")

        # Validate AI document content
        if document.type == DOCUMENT_TYPE_AI and "content" in updates:
            new_content = str(updates["content"])
            if not new_content.strip():
                raise DocumentRuleError("AI document transcript cannot be empty")
            if len(new_content) > MAX_TRANSCRIPT_LENGTH:
                raise DocumentRuleError(f"AI document transcript exceeds maximum length of {MAX_TRANSCRIPT_LENGTH}")

        # Validate summary length
        if "summary" in updates:
            new_summary = str(updates["summary"]).strip()
            max_len = MAX_SUMMARY_LENGTH.get(document.type, 1000)
            if len(new_summary) > max_len:
                raise DocumentRuleError(f"Summary exceeds maximum length of {max_len} for {document.type} documents")

        for key in ("name", "date", "time", "description", "content", "summary", "provider"):
            if key in updates:
                # For content in AI docs, keep verbatim; otherwise strip
                if key == "content" and document.type == DOCUMENT_TYPE_AI:
                    setattr(document, key, str(updates[key]))
                else:
                    setattr(document, key, str(updates[key]).strip())

        if "tags" in updates:
            document.tags = _resolve_tags(session, updates["tags"])

        now = _now()
        document.updated_at = now
        document.folder.updated_at = now
        session.flush()
        return _document_to_dict(document)


def _read_upload_bytes(content: Union[bytes, BinaryIO]) -> bytes:
    if isinstance(content, bytes):
        return content
    content.seek(0)
    return content.read()


_WHATSAPP_CANDIDATE_SUFFIXES = (".txt", ".zip")


def add_files(
    factory: sessionmaker[Session],
    folder_id: str,
    document_id: str,
    uploads: list[tuple[str, str, Union[bytes, BinaryIO]]],
) -> list[dict[str, object]] | None:
    """Store attachments for a document, returning their metadata."""

    with session_scope(factory) as session:
        document = (
            session.query(Document)
            .options(selectinload(Document.files))
            .filter(Document.id == document_id, Document.folder_id == folder_id)
            .one_or_none()
        )
        if document is None:
            return None

        saved_files: list[dict[str, object]] = []
        imported_chat = None
        imported_chat_file_id = None
        for filename, content_type, content in uploads:
            # Only buffer the file fully in memory when it could be a
            # WhatsApp export (small by nature); anything else is streamed
            # straight into a large object, since attachments here can be
            # gigabytes in size.
            is_chat_candidate = (
                imported_chat is None
                and Path(filename).suffix.lower() in _WHATSAPP_CANDIDATE_SUFFIXES
            )
            if is_chat_candidate:
                data = _read_upload_bytes(content)
                content = data
            digest, size = hash_stream(content)
            # An identical attachment already stored is shared, not copied.
            oid = (
                session.query(DocumentFile.data_oid)
                .filter(DocumentFile.sha256 == digest, DocumentFile.size == size)
                .limit(1)
                .scalar()
            )
            if oid is None:
                oid = create_large_object(session, content)
            # Identical bytes share extracted text instead of parsing them again.
            text_content = (
                session.query(DocumentFile.text_content)
                .filter(DocumentFile.data_oid == oid, DocumentFile.text_content.is_not(None))
                .limit(1)
                .scalar()
            )
            stored_content_type = content_type or "application/octet-stream"
            if text_content is None:
                text_content = extract_text(filename, stored_content_type, content, size)

            file = DocumentFile(
                id=str(uuid4()),
                document_id=document_id,
                name=Path(filename).name,
                content_type=stored_content_type,
                size=size,
                data_oid=oid,
                sha256=digest,
                text_content=text_content,
                created_at=_now(),
            )
            session.add(file)
            # Flush each attachment individually: batching several large
            # rows into one multi-row INSERT can break the Postgres wire
            # protocol (observed as "invalid message length").
            session.flush()
            saved_files.append(_file_to_dict(file))
            if is_chat_candidate:
                candidate = WhatsAppChat.from_file(filename, data)
                if candidate is not None:
                    imported_chat = candidate
                    imported_chat_file_id = file.id

        if imported_chat is not None:
            same_day_chat = imported_chat.for_date(document.date)
            if same_day_chat.messages:
                document.whatsapp_chat = same_day_chat.to_dict()
                document.whatsapp_chat_file_id = imported_chat_file_id
        document.updated_at = _now()
        session.flush()
        return saved_files


def remove_file(
    factory: sessionmaker[Session], folder_id: str, document_id: str, file_id: str
) -> dict[str, object] | None:
    """Delete an attachment, returning the updated document, or ``None`` if not found."""

    with session_scope(factory) as session:
        document = (
            session.query(Document)
            .options(selectinload(Document.files))
            .filter(Document.id == document_id, Document.folder_id == folder_id)
            .one_or_none()
        )
        if document is None:
            return None
        file = next((item for item in document.files if item.id == file_id), None)
        if file is None:
            return None
        document.files.remove(file)
        session.delete(file)
        if document.whatsapp_chat_file_id == file_id:
            document.whatsapp_chat = None
            document.whatsapp_chat_file_id = None
        document.updated_at = _now()
        session.flush()
        return _document_to_dict(document)


def get_file(
    factory: sessionmaker[Session], folder_id: str, document_id: str, file_id: str
) -> tuple[bytes, str, str] | None:
    """Return an attachment's bytes, content type, and name."""

    with factory() as session:
        file = (
            session.query(DocumentFile)
            .join(Document)
            .filter(
                DocumentFile.id == file_id,
                DocumentFile.document_id == document_id,
                Document.folder_id == folder_id,
            )
            .one_or_none()
        )
        if file is None:
            return None
        return (
            read_large_object(session, file.data_oid),
            effective_content_type(file.content_type, file.name),
            file.name,
        )


def find_duplicates(factory: sessionmaker[Session]) -> dict[str, object]:
    """Group attachments with identical content, across all documents and folders.

    Each group lists where the file appears and how much space sharing one
    stored copy would save (``reclaimable_bytes``). ``unhashed`` counts files
    that cannot be compared yet because they predate content hashing.
    """

    with factory() as session:
        unhashed = (
            session.query(DocumentFile).filter(DocumentFile.sha256.is_(None)).count()
        )
        rows = (
            session.query(DocumentFile, Document, Folder)
            .join(Document, DocumentFile.document_id == Document.id)
            .join(Folder, Document.folder_id == Folder.id)
            .filter(DocumentFile.sha256.is_not(None))
            .order_by(DocumentFile.created_at)
            .all()
        )
        grouped: dict[tuple[str, int], list[tuple[DocumentFile, Document, Folder]]] = {}
        for row in rows:
            grouped.setdefault((row[0].sha256, row[0].size), []).append(row)

        groups = []
        for (digest, size), members in grouped.items():
            if len(members) < 2:
                continue
            stored_copies = len({file.data_oid for file, _, _ in members})
            groups.append(
                {
                    "sha256": digest,
                    "size": size,
                    "stored_copies": stored_copies,
                    "reclaimable_bytes": size * (stored_copies - 1),
                    "files": [
                        {
                            "id": file.id,
                            "name": file.name,
                            "document_id": document.id,
                            "document_name": document.name,
                            "folder_id": folder.id,
                            "folder_name": folder.name,
                        }
                        for file, document, folder in members
                    ],
                }
            )
        groups.sort(key=lambda group: (-group["reclaimable_bytes"], -group["size"]))
        return {
            "groups": groups,
            "unhashed": unhashed,
            "reclaimable_bytes": sum(group["reclaimable_bytes"] for group in groups),
        }


def list_tags(factory: sessionmaker[Session]) -> list[dict[str, object]]:
    """Return all tags with their document counts, sorted by name."""

    with factory() as session:
        rows = (
            session.query(Tag.name, func.count(Document.id).label("document_count"))
            .select_from(Tag)
            .outerjoin(Tag.documents)
            .group_by(Tag.id, Tag.name, Tag.key)
            .order_by(Tag.key)
            .all()
        )
        return [
            {"name": row[0], "document_count": row[1]}
            for row in rows
        ]


def _link_to_dict(link: DocumentLink, direction: str) -> dict[str, object]:
    """Convert a document link to a dict, including the document at the other end."""
    # Determine which document is at the other end
    if direction == "outgoing":
        other_doc = link.target
    else:  # direction == "incoming"
        other_doc = link.source

    return {
        "id": link.id,
        "direction": direction,
        "type_id": link.link_type_id,
        "type": link.link_type.name,
        "created_at": link.created_at,
        "document": {
            "id": other_doc.id,
            "name": other_doc.name,
            "date": other_doc.date,
            "type": other_doc.type,
            "folder_id": other_doc.folder_id,
            "folder_name": other_doc.folder.name,
        },
    }


def list_link_types(factory: sessionmaker[Session]) -> list[dict[str, object]]:
    """Return all link types, sorted by key."""

    with factory() as session:
        link_types = session.query(LinkType).order_by(LinkType.key).all()
        return [
            {"id": lt.id, "name": lt.name}
            for lt in link_types
        ]


def create_link_type(factory: sessionmaker[Session], name: str) -> tuple[dict[str, object], bool]:
    """Create a link type or return existing by key.

    Returns (link_type_dict, created) where created is True if new.
    Raises ValueError if the normalized name is empty.
    """
    from .tags import normalize_tag, tag_key

    normalized = normalize_tag(name)
    if not normalized:
        raise ValueError("invalid_link_type")

    key = tag_key(normalized)
    now = _now()

    with session_scope(factory) as session:
        # Try to get existing
        existing = session.query(LinkType).filter_by(key=key).one_or_none()
        if existing is not None:
            return {"id": existing.id, "name": existing.name}, False

        # Create new
        try:
            with session.begin_nested():
                link_type = LinkType(
                    id=str(uuid4()),
                    name=normalized,
                    key=key,
                    created_at=now,
                )
                session.add(link_type)
            session.flush()
            return {"id": link_type.id, "name": link_type.name}, True
        except IntegrityError:
            # Another concurrent request created this type; fetch it
            link_type = session.query(LinkType).filter_by(key=key).one()
            return {"id": link_type.id, "name": link_type.name}, False


def add_document_link(
    factory: sessionmaker[Session],
    folder_id: str,
    document_id: str,
    target_document_id: str,
    link_type_id: str,
) -> dict[str, object] | None:
    """Add a link from a document to another, or return None if source not in folder.

    Raises LinkError for validation failures.
    """

    with session_scope(factory) as session:
        # Load source document scoped to folder
        source = (
            session.query(Document)
            .options(
                selectinload(Document.folder),
                selectinload(Document.outgoing_links).selectinload(DocumentLink.target),
                selectinload(Document.outgoing_links).selectinload(DocumentLink.link_type),
                selectinload(Document.incoming_links).selectinload(DocumentLink.source),
                selectinload(Document.incoming_links).selectinload(DocumentLink.link_type),
                selectinload(Document.files),
                selectinload(Document.tags),
            )
            .filter(Document.id == document_id, Document.folder_id == folder_id)
            .one_or_none()
        )
        if source is None:
            return None

        # Check for self-link
        if document_id == target_document_id:
            raise LinkError("self_link")

        # Check target exists
        target = session.get(Document, target_document_id)
        if target is None:
            raise LinkError("target_not_found")

        # Check link type exists
        link_type = session.get(LinkType, link_type_id)
        if link_type is None:
            raise LinkError("link_type_not_found")

        # Check for duplicate link in either direction
        # Same-direction duplicate
        existing = (
            session.query(DocumentLink)
            .filter(
                DocumentLink.source_document_id == document_id,
                DocumentLink.target_document_id == target_document_id,
                DocumentLink.link_type_id == link_type_id,
            )
            .one_or_none()
        )
        if existing is not None:
            raise LinkError("duplicate_link")

        # Reverse-direction duplicate
        existing = (
            session.query(DocumentLink)
            .filter(
                DocumentLink.source_document_id == target_document_id,
                DocumentLink.target_document_id == document_id,
                DocumentLink.link_type_id == link_type_id,
            )
            .one_or_none()
        )
        if existing is not None:
            raise LinkError("duplicate_link")

        # Create link with user-supplied direction
        try:
            link = DocumentLink(
                id=str(uuid4()),
                source_document_id=document_id,
                target_document_id=target_document_id,
                link_type_id=link_type_id,
                created_at=_now(),
            )
            session.add(link)
            session.flush()
        except IntegrityError:
            raise LinkError("duplicate_link")

        # Reload the source document to get fresh relationship data
        session.expunge(source)
        source = (
            session.query(Document)
            .options(
                selectinload(Document.folder),
                selectinload(Document.outgoing_links).selectinload(DocumentLink.target),
                selectinload(Document.outgoing_links).selectinload(DocumentLink.link_type),
                selectinload(Document.incoming_links).selectinload(DocumentLink.source),
                selectinload(Document.incoming_links).selectinload(DocumentLink.link_type),
                selectinload(Document.files),
                selectinload(Document.tags),
            )
            .filter(Document.id == document_id, Document.folder_id == folder_id)
            .one()
        )

        return _document_to_dict(source)


def remove_document_link(
    factory: sessionmaker[Session],
    folder_id: str,
    document_id: str,
    link_id: str,
) -> dict[str, object] | None:
    """Remove a link from either end. Returns None if document or link not found."""

    with session_scope(factory) as session:
        # Load document scoped to folder
        document = (
            session.query(Document)
            .options(
                selectinload(Document.folder),
                selectinload(Document.outgoing_links).selectinload(DocumentLink.target),
                selectinload(Document.outgoing_links).selectinload(DocumentLink.link_type),
                selectinload(Document.incoming_links).selectinload(DocumentLink.source),
                selectinload(Document.incoming_links).selectinload(DocumentLink.link_type),
                selectinload(Document.files),
                selectinload(Document.tags),
            )
            .filter(Document.id == document_id, Document.folder_id == folder_id)
            .one_or_none()
        )
        if document is None:
            return None

        # Find link where source or target is this document
        link = (
            session.query(DocumentLink)
            .filter(
                DocumentLink.id == link_id,
                (DocumentLink.source_document_id == document_id)
                | (DocumentLink.target_document_id == document_id),
            )
            .one_or_none()
        )
        if link is None:
            return None

        session.delete(link)
        session.flush()

        # Reload the document to get fresh relationship data
        session.expunge(document)
        document = (
            session.query(Document)
            .options(
                selectinload(Document.folder),
                selectinload(Document.outgoing_links).selectinload(DocumentLink.target),
                selectinload(Document.outgoing_links).selectinload(DocumentLink.link_type),
                selectinload(Document.incoming_links).selectinload(DocumentLink.source),
                selectinload(Document.incoming_links).selectinload(DocumentLink.link_type),
                selectinload(Document.files),
                selectinload(Document.tags),
            )
            .filter(Document.id == document_id, Document.folder_id == folder_id)
            .one()
        )

        return _document_to_dict(document)


def _keyword_candidates(
    session: Session, terms: str, folder_id: str | None, limit: int
) -> list[tuple[str, float]]:
    folder_clause = " AND d.folder_id = :folder_id" if folder_id is not None else ""
    params: dict[str, object] = {"terms": terms, "limit": limit}
    if folder_id is not None:
        params["folder_id"] = folder_id
    rows = session.execute(
        text(
            "SELECT d.id, ts_rank(d.search_vector, to_tsquery('simple', :terms)) AS rank "
            "FROM documents d "
            "WHERE d.search_vector @@ to_tsquery('simple', :terms)"
            f"{folder_clause} "
            "ORDER BY rank DESC, d.id LIMIT :limit"
        ),
        params,
    )
    return [(row[0], float(row[1])) for row in rows]


def _search_result(
    query: str, results: list[dict[str, object]], semantic: str, pending: int
) -> dict[str, object]:
    return {
        "query": query,
        "results": results,
        "semantic": semantic,
        "embeddings_pending": pending,
    }


def search_documents(
    factory: sessionmaker[Session],
    query: str,
    folder_id: str | None = None,
    *,
    embedder: OllamaClient | None = None,
    mode: str = "hybrid",
    limit: int = 50,
) -> dict[str, object]:
    """Search documents by keyword and, when available, by meaning.

    ``semantic`` in the response is one of ``ok``, ``unavailable`` (Ollama failed),
    ``disabled`` (no embedder configured), or ``skipped`` (keyword-only mode or a
    query too short to embed meaningfully).
    """

    query = query.strip()

    # Embed before opening a DB session so a slow Ollama call holds no connection.
    query_vector: list[float] | None = None
    if embedder is None:
        semantic = "disabled"
    elif mode == "keyword" or len(query) < MIN_SEMANTIC_QUERY_LENGTH:
        semantic = "skipped"
    else:
        try:
            query_vector = embedder.embed([query], kind="query", timeout=OLLAMA_QUERY_TIMEOUT)[0]
            semantic = "ok"
        except EmbeddingUnavailable:
            semantic = "unavailable"

    terms = build_tsquery(query)
    # Semantic-only mode still falls back to keywords when no vector is available.
    use_keyword = terms is not None and (mode != "semantic" or query_vector is None)
    if not use_keyword and query_vector is None:
        return _search_result(query, [], semantic, 0)

    with factory() as session:
        keyword = (
            _keyword_candidates(session, terms, folder_id, CANDIDATE_LIMIT) if use_keyword else []
        )
        semantic_hits: list[tuple[str, float]] = []
        pending = 0
        if query_vector is not None and embedder is not None:
            semantic_hits = semantic_candidates(
                session,
                folder_id,
                query_vector,
                model=embedder.model,
                dims=len(query_vector),
                limit=CANDIDATE_LIMIT,
                min_similarity=SEMANTIC_MIN_SIMILARITY,
            )
        if embedder is not None:
            pending = len(pending_document_ids(session, embedder.model))

        fused = reciprocal_rank_fusion(
            [[item_id for item_id, _ in keyword], [item_id for item_id, _ in semantic_hits]]
        )[:limit]
        ids = [item_id for item_id, _ in fused]
        if not ids:
            return _search_result(query, [], semantic, pending)

        keyword_ranks = dict(keyword)
        semantic_ids = {item_id for item_id, _ in semantic_hits}
        scores = dict(fused)
        documents = {
            document.id: document
            for document in session.query(Document)
            .options(selectinload(Document.tags), selectinload(Document.folder))
            .filter(Document.id.in_(ids))
            .all()
        }

    results: list[dict[str, object]] = []
    for item_id in ids:
        document = documents.get(item_id)
        if document is None:
            continue
        matched_by = []
        if item_id in keyword_ranks:
            matched_by.append("keyword")
        if item_id in semantic_ids:
            matched_by.append("semantic")
        results.append(
            {
                "id": document.id,
                "folder_id": document.folder_id,
                "folder_name": document.folder.name,
                "rank": keyword_ranks.get(item_id),
                "matched_by": matched_by,
                "score": scores[item_id],
            }
        )
    return _search_result(query, results, semantic, pending)
