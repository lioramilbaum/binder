"""Plain-text extraction from uploaded attachments, used for full-text search.

Extraction runs synchronously during upload, so it is bounded: large files and
unsupported types are skipped, and any failure yields ``""`` instead of an error.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path
from typing import BinaryIO
from xml.etree import ElementTree

from pypdf import PdfReader

from .whatsapp import WhatsAppChat


logger = logging.getLogger(__name__)

MAX_EXTRACT_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_CHARS = 200_000

_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".log"}
_TEXT_TYPES = {"text/plain"}
_DOCX_SUFFIX = ".docx"
_WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_WHITESPACE = re.compile(r"\s+")


def is_extractable(filename: str, content_type: str, size: int) -> bool:
    """Return whether ``extract_text`` would attempt this file (without reading it)."""

    if size > MAX_EXTRACT_BYTES:
        return False
    suffix = Path(filename).suffix.lower()
    return (
        suffix in _TEXT_SUFFIXES
        or content_type in _TEXT_TYPES
        or suffix in (".zip", ".pdf", _DOCX_SUFFIX)
        or content_type == "application/pdf"
    )


def extract_text(filename: str, content_type: str, source: bytes | BinaryIO, size: int) -> str:
    """Return searchable text for an upload, or ``""`` when unsupported or unreadable.

    Streams are rewound before and after extraction so the caller can store them.
    """

    if size > MAX_EXTRACT_BYTES:
        return ""
    suffix = Path(filename).suffix.lower()
    _rewind(source)
    try:
        text = _extract(filename, suffix, content_type, source)
    except Exception:
        logger.warning("Text extraction failed for %s", filename, exc_info=True)
        text = ""
    finally:
        _rewind(source)
    return _normalize(text)


def _extract(filename: str, suffix: str, content_type: str, source: bytes | BinaryIO) -> str:
    if suffix in _TEXT_SUFFIXES or content_type in _TEXT_TYPES:
        return _decode_text(_read_all(source))
    if suffix == ".zip":
        return WhatsAppChat._extract_text(filename, _read_all(source)) or ""
    if suffix == ".pdf" or content_type == "application/pdf":
        return _extract_pdf(source)
    if suffix == _DOCX_SUFFIX:
        return _extract_docx(source)
    return ""


def _extract_pdf(source: bytes | BinaryIO) -> str:
    stream = io.BytesIO(source) if isinstance(source, bytes) else source
    reader = PdfReader(stream)
    if reader.is_encrypted:
        reader.decrypt("")
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx(source: bytes | BinaryIO) -> str:
    stream = io.BytesIO(source) if isinstance(source, bytes) else source
    with zipfile.ZipFile(stream) as archive:
        xml_bytes = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml_bytes)
    paragraphs = (
        "".join(node.text or "" for node in paragraph.iter(f"{_WORD_NAMESPACE}t"))
        for paragraph in root.iter(f"{_WORD_NAMESPACE}p")
    )
    return "\n".join(paragraphs)


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    # UTF-16 is only trusted with a byte-order mark: without one, nearly any
    # even-length cp1255 text decodes as UTF-16 without error, into garbage.
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    return data.decode("cp1255", errors="replace")


def _read_all(source: bytes | BinaryIO) -> bytes:
    if isinstance(source, bytes):
        return source
    source.seek(0)
    return source.read()


def _rewind(source: bytes | BinaryIO) -> None:
    if isinstance(source, bytes):
        return
    try:
        source.seek(0)
    except (OSError, ValueError):
        pass


def _normalize(text: str) -> str:
    text = text.replace("\x00", "")
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:MAX_EXTRACTED_CHARS]
