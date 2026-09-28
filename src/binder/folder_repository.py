"""Database persistence for folders, documents, and their attachments."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Sequence, Union
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .database import session_scope
from .large_objects import create_large_object, hash_stream, read_large_object
from .models import AiThread, Document, DocumentFile, Folder, Tag
from .tags import normalize_tags, tag_key
from .whatsapp import WhatsAppChat


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


def _file_to_dict(file: DocumentFile) -> dict[str, object]:
    return {
        "id": file.id,
        "name": file.name,
        "content_type": file.content_type,
        "size": file.size,
    }


def _document_summary(document: Document) -> dict[str, object]:
    return {
        "id": document.id,
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
    return result


def _document_sort_key(document: Document) -> tuple[str, str, str]:
    return (document.date or "", document.time or "", document.created_at or "")


def _ai_thread_to_dict(thread: AiThread) -> dict[str, object]:
    return {
        "id": thread.id,
        "folder_id": thread.folder_id,
        "title": thread.title,
        "provider": thread.provider,
        "conversation_date": thread.conversation_date,
        "transcript": thread.transcript,
        "summary": thread.summary,
        "created_at": thread.created_at,
    }


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
            .options(selectinload(Folder.ai_threads))
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
            "ai_threads": [
                _ai_thread_to_dict(thread)
                for thread in sorted(
                    folder.ai_threads,
                    key=lambda thread: (thread.conversation_date, thread.created_at, thread.id),
                    reverse=True,
                )
            ],
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
        "ai_threads": [],
    }


def create_ai_thread(
    factory: sessionmaker[Session],
    folder_id: str,
    title: str,
    provider: str,
    conversation_date: str,
    transcript: str,
    summary: str,
) -> dict[str, object] | None:
    """Save an archived conversation, or return None for an absent folder."""

    with session_scope(factory) as session:
        folder = session.get(Folder, folder_id)
        if folder is None:
            return None
        now = _now()
        thread = AiThread(
            id=str(uuid4()),
            folder_id=folder_id,
            title=title,
            provider=provider,
            conversation_date=conversation_date,
            transcript=transcript,
            summary=summary,
            created_at=now,
        )
        session.add(thread)
        folder.updated_at = now
        session.flush()
        return _ai_thread_to_dict(thread)


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
) -> dict[str, object] | None:
    """Create a document in a folder, or return ``None`` if the folder is missing."""

    now = _now()
    with session_scope(factory) as session:
        folder = session.get(Folder, folder_id)
        if folder is None:
            return None
        document = Document(
            id=str(uuid4()),
            folder_id=folder_id,
            date=date,
            time=time,
            name=name.strip(),
            description=description.strip(),
            content=content.strip(),
            summary=summary.strip(),
            created_at=now,
            updated_at=now,
        )
        session.add(document)
        document.tags = _resolve_tags(session, list(tags))
        folder.updated_at = now
        session.flush()
        return _document_to_dict(document)


def update_document(
    factory: sessionmaker[Session],
    folder_id: str,
    document_id: str,
    updates: dict[str, str | list[str]],
) -> dict[str, object] | None:
    """Apply field updates to a document, or return ``None`` if it doesn't exist."""

    with session_scope(factory) as session:
        document = (
            session.query(Document)
            .options(selectinload(Document.files))
            .options(selectinload(Document.tags))
            .filter(Document.id == document_id, Document.folder_id == folder_id)
            .one_or_none()
        )
        if document is None:
            return None
        for key in ("name", "date", "time", "description", "content", "summary"):
            if key in updates:
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

            file = DocumentFile(
                id=str(uuid4()),
                document_id=document_id,
                name=Path(filename).name,
                content_type=content_type or "application/octet-stream",
                size=size,
                data_oid=oid,
                sha256=digest,
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
        return read_large_object(session, file.data_oid), file.content_type, file.name


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
