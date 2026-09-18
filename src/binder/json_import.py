"""Import legacy JSON-file folders/documents/attachments into the database."""

from __future__ import annotations

import argparse
import json
import mimetypes
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from .database import create_session_factory, session_scope
from .large_objects import create_large_object
from .models import Document, DocumentFile, Folder


def _load_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _import_folder(session: Session, folder_data: dict[str, object], data_dir: Path) -> bool:
    folder_id = str(folder_data["id"])
    if session.get(Folder, folder_id) is not None:
        return False
    folder = Folder(
        id=folder_id,
        name=str(folder_data.get("name", "")),
        description=str(folder_data.get("description", "")),
        created_at=str(folder_data.get("created_at", "")),
        updated_at=str(folder_data.get("updated_at", "")),
    )
    session.add(folder)
    for document_summary in folder_data.get("documents", []):
        _import_document(session, folder_id, document_summary, data_dir)
    return True


def _import_document(
    session: Session, folder_id: str, document_summary: dict[str, object], data_dir: Path
) -> None:
    document_id = str(document_summary["id"])
    document_path = data_dir / "documents" / folder_id / f"{document_id}.json"
    document_data = _load_json(document_path)
    if not isinstance(document_data, dict):
        document_data = document_summary

    document = Document(
        id=document_id,
        folder_id=folder_id,
        date=str(document_data.get("date", "")),
        time=str(document_data.get("time", "")),
        name=str(document_data.get("name", "")),
        description=str(document_data.get("description", "")),
        content=str(document_data.get("content", "")),
        summary=str(document_data.get("summary", "")),
        created_at=str(document_data.get("created_at", "")),
        updated_at=str(document_data.get("updated_at", "")),
        whatsapp_chat=document_data.get("whatsapp_chat"),
        whatsapp_chat_file_id=document_data.get("whatsapp_chat_file_id"),
    )
    session.add(document)

    attachments_dir = data_dir / "documents" / folder_id / f"{document_id}_files"
    for attachment in document_data.get("files", []):
        stored_name = attachment.get("stored_name")
        if not stored_name:
            continue
        attachment_path = attachments_dir / str(stored_name)
        if not attachment_path.is_file():
            continue
        content_type = str(
            attachment.get("content_type")
            or mimetypes.guess_type(str(attachment.get("name", "")))[0]
            or "application/octet-stream"
        )
        size = int(attachment.get("size", attachment_path.stat().st_size))
        with attachment_path.open("rb") as stream:
            oid = create_large_object(session, stream)
        session.add(
            DocumentFile(
                id=str(attachment["id"]),
                document_id=document_id,
                name=str(attachment.get("name", stored_name)),
                content_type=content_type,
                size=size,
                data_oid=oid,
                created_at=str(document_data.get("created_at", "")),
            )
        )
        # Flush each attachment individually: batching several large
        # rows into one multi-row INSERT can break the Postgres wire
        # protocol.
        session.flush()


def import_json_data(factory: sessionmaker[Session], data_dir: Path) -> int:
    """Import folders.json and its documents into the database, skipping existing folders."""

    folders_data = _load_json(data_dir / "folders.json")
    if not isinstance(folders_data, list):
        return 0

    imported = 0
    for folder_data in folders_data:
        # One transaction per folder: keeps each commit's batched inserts
        # (which include attachment blobs) small, and makes a crash partway
        # through a large import resumable, since already-committed folders
        # are skipped on retry.
        with session_scope(factory) as session:
            if _import_folder(session, folder_data, data_dir):
                imported += 1
    return imported


def main() -> None:
    """Import an existing data/ directory of JSON folders into Postgres."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    imported = import_json_data(create_session_factory(), args.data_dir)
    print(f"Imported {imported} folder(s) from {args.data_dir}.")


if __name__ == "__main__":
    main()
