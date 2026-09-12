from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Optional, Union
from uuid import uuid4

from .config import STORE_PATH
from .document import Document
from .whatsapp import WhatsAppChat


class Folders:
    def __init__(self, path: Path = STORE_PATH):
        self.path = path

    def list(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return sorted(data, key=lambda folder: folder["updated_at"], reverse=True)

    def get(self, folder_id: str) -> Optional[dict[str, object]]:
        for folder in self.list():
            if folder["id"] == folder_id:
                folder.setdefault("documents", [])
                folder["documents"] = [
                    self._read_document(folder_id, document)
                    for document in folder["documents"]
                ]
                folder["documents"].sort(
                    key=lambda document: document.get("created_at", ""),
                    reverse=True,
                )
                return folder
        return None

    def create(self, name: str, description: str = "") -> dict[str, object]:
        folders = self.list()
        now = datetime.now(timezone.utc).isoformat()
        folder = {
            "id": str(uuid4()),
            "name": name.strip(),
            "description": description.strip(),
            "created_at": now,
            "updated_at": now,
            "documents": [],
        }
        folders.append(folder)
        self._write(folders)
        return folder

    def create_document(
        self,
        folder_id: str,
        date: str,
        name: str,
        description: str = "",
        content: str = "",
        time: str = "",
    ) -> Optional[dict[str, object]]:
        folders = self.list()
        for folder in folders:
            if folder["id"] != folder_id:
                continue
            document = Document.create(
                date, name, description, content, time=time
            ).to_dict()
            self._write_document(folder_id, document)
            document_summary = {
                key: value for key, value in document.items() if key != "content"
            }
            folder.setdefault("documents", []).append(document_summary)
            folder["updated_at"] = document["updated_at"]
            self._write(folders)
            return document
        return None

    def add_files(
        self,
        folder_id: str,
        document_id: str,
        uploads: list[tuple[str, str, Union[bytes, BinaryIO]]],
    ) -> Optional[list[dict[str, object]]]:
        folder = self.get(folder_id)
        if folder is None:
            return None
        document = next(
            (
                item
                for item in folder["documents"]
                if item["id"] == document_id
            ),
            None,
        )
        if document is None:
            return None

        saved_files = []
        imported_chat = None
        for filename, content_type, content in uploads:
            file_id = str(uuid4())
            suffix = Path(filename).suffix[:16]
            stored_name = f"{file_id}{suffix}"
            attachment_path = self._attachments_path(folder_id, document_id) / stored_name
            attachment_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                attachment_path.write_bytes(content)
            else:
                content.seek(0)
                with attachment_path.open("wb") as destination:
                    shutil.copyfileobj(content, destination, length=1024 * 1024)
            size = attachment_path.stat().st_size
            saved_files.append(
                {
                    "id": file_id,
                    "name": Path(filename).name,
                    "content_type": content_type or "application/octet-stream",
                    "size": size,
                    "stored_name": stored_name,
                }
            )
            if imported_chat is None:
                imported_chat = WhatsAppChat.from_path(filename, attachment_path)

        document.setdefault("files", []).extend(saved_files)
        if imported_chat is not None:
            document["whatsapp_chat"] = imported_chat.to_dict()
        document["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_document(folder_id, document)
        return saved_files

    def update_document(
        self, folder_id: str, document_id: str, updates: dict[str, str]
    ) -> Optional[dict[str, object]]:
        folders = self.list()
        for folder in folders:
            if folder["id"] != folder_id:
                continue
            documents = folder.setdefault("documents", [])
            for index, document_summary in enumerate(documents):
                if document_summary["id"] != document_id:
                    continue
                document = self._read_document(folder_id, document_summary)
                document.setdefault("time", "")
                document.setdefault("files", [])

                for key in ["name", "date", "time", "description", "content"]:
                    if key in updates:
                        document[key] = str(updates[key]).strip()

                document["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._write_document(folder_id, document)
                documents[index] = {
                    key: value for key, value in document.items() if key != "content"
                }
                folder["updated_at"] = document["updated_at"]
                self._write(folders)
                return document
        return None

    def get_file(
        self, folder_id: str, document_id: str, file_id: str
    ) -> Optional[tuple[Path, dict[str, object]]]:
        folder = self.get(folder_id)
        if folder is None:
            return None
        document = next(
            (item for item in folder["documents"] if item["id"] == document_id), None
        )
        if document is None:
            return None
        attachment = next(
            (item for item in document.get("files", []) if item["id"] == file_id), None
        )
        if attachment is None:
            return None
        path = self._attachments_path(folder_id, document_id) / attachment["stored_name"]
        if not path.is_file():
            return None
        return path, attachment

    def _read_document(
        self, folder_id: str, document_summary: dict[str, str]
    ) -> dict[str, str]:
        if "content" in document_summary:
            return document_summary
        document_path = self._document_path(folder_id, document_summary["id"])
        try:
            return json.loads(document_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {**document_summary, "content": ""}

    def _write_document(self, folder_id: str, document: dict[str, str]) -> None:
        document_path = self._document_path(folder_id, document["id"])
        document_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = document_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(document_path)

    def _document_path(self, folder_id: str, document_id: str) -> Path:
        return self.path.parent / "documents" / folder_id / f"{document_id}.json"

    def _attachments_path(self, folder_id: str, document_id: str) -> Path:
        return self.path.parent / "documents" / folder_id / f"{document_id}_files"

    def _write(self, folders: list[dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(folders, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(self.path)
