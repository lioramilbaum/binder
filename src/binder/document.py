from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


@dataclass
class Document:
    id: str
    date: str
    time: str
    name: str
    description: str
    content: str
    created_at: str
    updated_at: str
    files: list[dict[str, object]] = field(default_factory=list)
    summary: str = ""

    @classmethod
    def create(
        cls,
        date: str,
        name: str,
        description: str = "",
        content: str = "",
        time: str = "",
        summary: str = "",
    ) -> "Document":
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            id=str(uuid4()),
            date=date,
            time=time,
            name=name.strip(),
            description=description.strip(),
            content=content.strip(),
            created_at=now,
            updated_at=now,
            summary=summary.strip(),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
