from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


ANDROID_LINE = re.compile(
    r"^(?P<date>\d{1,2}[./]\d{1,2}[./]\d{2,4}),\s*"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp]\.?[Mm]\.?)?)\s+-\s+"
    r"(?P<body>.*)$"
)
IPHONE_LINE = re.compile(
    r"^\[(?P<date>\d{1,2}[./]\d{1,2}[./]\d{2,4}),\s*"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp]\.?[Mm]\.?)?)\]\s*"
    r"(?P<body>.*)$"
)
INVISIBLE_MARKS = str.maketrans("", "", "\u200e\u200f\u202a\u202b\u202c\u202d\u202e")
OWNER_NAME = "Liora Milbaum"


@dataclass
class WhatsAppChat:
    messages: list[dict[str, object]]
    participants: list[str]

    @classmethod
    def from_file(cls, filename: str, content: bytes) -> Optional["WhatsAppChat"]:
        text = cls._extract_text(filename, content)
        if text is None:
            return None
        messages = cls._parse_messages(text)
        if not messages:
            return None
        participants = list(
            dict.fromkeys(
                str(message["sender"])
                for message in messages
                if message.get("sender")
            )
        )
        return cls(messages=messages, participants=participants)

    @classmethod
    def from_path(cls, filename: str, path: Path) -> Optional["WhatsAppChat"]:
        suffix = Path(filename).suffix.lower()
        if suffix == ".txt":
            if path.stat().st_size > 10 * 1024 * 1024:
                return None
            return cls.from_file(filename, path.read_bytes())
        if suffix != ".zip":
            return None
        try:
            with zipfile.ZipFile(path) as archive:
                candidates = [
                    item
                    for item in archive.infolist()
                    if item.filename.lower().endswith(".txt")
                    and item.file_size <= 10 * 1024 * 1024
                ]
                if not candidates:
                    return None
                chat_file = min(candidates, key=lambda item: item.file_size)
                text = cls._decode(archive.read(chat_file))
        except (OSError, zipfile.BadZipFile):
            return None
        if text is None:
            return None
        messages = cls._parse_messages(text)
        if not messages:
            return None
        participants = list(
            dict.fromkeys(
                str(message["sender"])
                for message in messages
                if message.get("sender")
            )
        )
        return cls(messages=messages, participants=participants)

    def to_dict(self) -> dict[str, object]:
        return {
            "messages": self.messages,
            "participants": self.participants,
            "me": OWNER_NAME,
        }

    @staticmethod
    def _extract_text(filename: str, content: bytes) -> Optional[str]:
        suffix = Path(filename).suffix.lower()
        if suffix == ".txt":
            return WhatsAppChat._decode(content)
        if suffix != ".zip":
            return None
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                candidates = [
                    item
                    for item in archive.infolist()
                    if item.filename.lower().endswith(".txt")
                    and item.file_size <= 10 * 1024 * 1024
                ]
                if not candidates:
                    return None
                chat_file = min(candidates, key=lambda item: item.file_size)
                return WhatsAppChat._decode(archive.read(chat_file))
        except (OSError, zipfile.BadZipFile):
            return None

    @staticmethod
    def _decode(content: bytes) -> Optional[str]:
        for encoding in ("utf-8-sig", "utf-16", "cp1255"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        return None

    @staticmethod
    def _parse_messages(text: str) -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        for raw_line in text.splitlines():
            line = raw_line.translate(INVISIBLE_MARKS)
            match = ANDROID_LINE.match(line) or IPHONE_LINE.match(line)
            if not match:
                if messages:
                    messages[-1]["content"] = f'{messages[-1]["content"]}\n{raw_line}'
                continue

            body = match.group("body")
            sender, separator, content = body.partition(": ")
            is_system = not separator
            messages.append(
                {
                    "date": match.group("date"),
                    "time": match.group("time"),
                    "sender": "" if is_system else sender,
                    "content": body if is_system else content,
                    "system": is_system,
                }
            )
        return messages
