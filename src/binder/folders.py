from __future__ import annotations

from typing import BinaryIO, Optional, Sequence, Union

from sqlalchemy.orm import Session, sessionmaker

from . import folder_repository
from .database import create_session_factory


class Folders:
    def __init__(self, factory: Optional[sessionmaker[Session]] = None):
        self._factory = factory
        self.embedder = None
        self.embedding_worker = None

    @property
    def factory(self) -> sessionmaker[Session]:
        if self._factory is None:
            self._factory = create_session_factory()
        return self._factory

    def enable_semantic_search(self, embedder, worker) -> None:
        self.embedder = embedder
        self.embedding_worker = worker

    def _changed(self) -> None:
        if self.embedding_worker is not None:
            self.embedding_worker.notify()

    def search(
        self,
        query: str,
        folder_id: Optional[str] = None,
        limit: int = 50,
        embedder=None,
        mode: str = "hybrid",
    ) -> dict[str, object]:
        if embedder is None:
            embedder = self.embedder
        return folder_repository.search_documents(
            self.factory, query, folder_id, embedder=embedder, mode=mode, limit=limit
        )

    def list(self) -> list[dict[str, object]]:
        return folder_repository.list_folders(self.factory)

    def get(self, folder_id: str) -> Optional[dict[str, object]]:
        return folder_repository.get_folder(self.factory, folder_id)

    def create(self, name: str, description: str = "") -> dict[str, object]:
        return folder_repository.create_folder(self.factory, name, description)

    def create_document(
        self,
        folder_id: str,
        date: str,
        name: str,
        description: str = "",
        content: str = "",
        time: str = "",
        summary: str = "",
        tags: Sequence[str] = (),
        document_type: str = "regular",
        provider: str = "",
        source_url: str | None = None,
        transcript_sha256: str | None = None,
        source_first_message_at: str | None = None,
    ) -> Optional[dict[str, object]]:
        document = folder_repository.create_document(
            self.factory, folder_id, date, name, description, content, time, summary, tags=tags,
            document_type=document_type, provider=provider, source_url=source_url,
            transcript_sha256=transcript_sha256, source_first_message_at=source_first_message_at,
        )
        if document is not None:
            self._changed()
        return document

    def add_files(
        self,
        folder_id: str,
        document_id: str,
        uploads: list[tuple[str, str, Union[bytes, BinaryIO]]],
    ) -> Optional[list[dict[str, object]]]:
        files = folder_repository.add_files(self.factory, folder_id, document_id, uploads)
        if files is not None:
            self._changed()
        return files

    def remove_file(
        self, folder_id: str, document_id: str, file_id: str
    ) -> Optional[dict[str, object]]:
        document = folder_repository.remove_file(self.factory, folder_id, document_id, file_id)
        if document is not None:
            self._changed()
        return document

    def update_document(
        self, folder_id: str, document_id: str, updates: dict[str, str]
    ) -> Optional[dict[str, object]]:
        document = folder_repository.update_document(self.factory, folder_id, document_id, updates)
        if document is not None:
            self._changed()
        return document

    def get_file(
        self, folder_id: str, document_id: str, file_id: str
    ) -> Optional[tuple[bytes, str, str]]:
        return folder_repository.get_file(self.factory, folder_id, document_id, file_id)

    def duplicates(self) -> dict[str, object]:
        return folder_repository.find_duplicates(self.factory)

    def tags(self) -> list[dict[str, object]]:
        return folder_repository.list_tags(self.factory)

    def link_types(self) -> list[dict[str, object]]:
        return folder_repository.list_link_types(self.factory)

    def create_link_type(self, name: str) -> dict[str, object]:
        link_type, _ = folder_repository.create_link_type(self.factory, name)
        return link_type

    def add_link(
        self, folder_id: str, document_id: str, target_document_id: str, link_type_id: str
    ) -> Optional[dict[str, object]]:
        return folder_repository.add_document_link(
            self.factory, folder_id, document_id, target_document_id, link_type_id
        )

    def remove_link(self, folder_id: str, document_id: str, link_id: str) -> Optional[dict[str, object]]:
        return folder_repository.remove_document_link(self.factory, folder_id, document_id, link_id)
