from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from .config import STORE_PATH


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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(folders, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(self.path)
        return folder
