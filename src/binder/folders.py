from __future__ import annotations

from typing import BinaryIO, Optional, Sequence, Union

from sqlalchemy.orm import Session, sessionmaker

from . import folder_repository
from .database import create_session_factory


class Folders:
    def __init__(self, factory: Optional[sessionmaker[Session]] = None):
        self._factory = factory

    @property
    def factory(self) -> sessionmaker[Session]:
        if self._factory is None:
            self._factory = create_session_factory()
        return self._factory

    def list(self) -> list[dict[str, object]]:
        return folder_repository.list_folders(self.factory)

    def get(self, folder_id: str) -> Optional[dict[str, object]]:
        return folder_repository.get_folder(self.factory, folder_id)

    def create(self, name: str, description: str = "") -> dict[str, object]:
        return folder_repository.create_folder(self.factory, name, description)

    def create_ai_thread(
        self,
        folder_id: str,
        title: str,
        provider: str,
        conversation_date: str,
        transcript: str,
        summary: str,
        *,
        conversation_time: str | None = None,
        source_url: str | None = None,
        transcript_sha256: str | None = None,
        source_first_message_at: str | None = None,
    ) -> Optional[dict[str, object]]:
        return folder_repository.create_ai_thread(
            self.factory, folder_id, title, provider, conversation_date, transcript, summary,
            conversation_time=conversation_time, source_url=source_url,
            transcript_sha256=transcript_sha256,
            source_first_message_at=source_first_message_at,
        )

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
    ) -> Optional[dict[str, object]]:
        return folder_repository.create_document(
            self.factory, folder_id, date, name, description, content, time, summary, tags=tags
        )

    def add_files(
        self,
        folder_id: str,
        document_id: str,
        uploads: list[tuple[str, str, Union[bytes, BinaryIO]]],
    ) -> Optional[list[dict[str, object]]]:
        return folder_repository.add_files(self.factory, folder_id, document_id, uploads)

    def remove_file(
        self, folder_id: str, document_id: str, file_id: str
    ) -> Optional[dict[str, object]]:
        return folder_repository.remove_file(self.factory, folder_id, document_id, file_id)

    def update_document(
        self, folder_id: str, document_id: str, updates: dict[str, str]
    ) -> Optional[dict[str, object]]:
        return folder_repository.update_document(self.factory, folder_id, document_id, updates)

    def get_file(
        self, folder_id: str, document_id: str, file_id: str
    ) -> Optional[tuple[bytes, str, str]]:
        return folder_repository.get_file(self.factory, folder_id, document_id, file_id)

    def duplicates(self) -> dict[str, object]:
        return folder_repository.find_duplicates(self.factory)

    def tags(self) -> list[dict[str, object]]:
        return folder_repository.list_tags(self.factory)
