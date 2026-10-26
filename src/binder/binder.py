import hashlib
import json
import re
import secrets
import threading
import time as clock
from datetime import date, time
from email import policy
from email.message import Message
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from tempfile import TemporaryFile
from typing import BinaryIO
from urllib.parse import parse_qs, quote, urlparse

from .ask import AskDisabled
from .chatgpt_share import ShareImportError, SharePreview, import_share_url
from .config import (
    ASK_DEFAULT_DOCUMENTS,
    ASK_MAX_DOCUMENTS,
    ASK_MAX_QUESTION_LENGTH,
    GOOGLE_API_KEY,
    GOOGLE_CLIENT_ID,
    GOOGLE_DRIVE_IMPORT_ENABLED,
    LOCALE,
    MAX_UPLOAD_SIZE,
    STATIC_DIR,
)
from .embeddings import ChatUnavailable
from .folder_repository import (
    DocumentRuleError,
    LinkError,
    add_document_link,
    create_link_type,
    remove_document_link,
)
from .folders import Folders
from .i18n import Translator
from .models import (
    DOCUMENT_TYPE_AI,
    DOCUMENT_TYPE_REGULAR,
    DOCUMENT_TYPES,
    MAX_LINK_TYPE_LENGTH,
    MAX_SUMMARY_LENGTH,
)
from .search import MAX_QUERY_LENGTH
from .tags import MAX_TAG_LENGTH, MAX_TAGS_PER_DOCUMENT, normalize_tag, normalize_tags

SEARCH_MODES = {"hybrid", "keyword", "semantic"}
ASK_KEYS = {"question", "folder_id", "limit"}


class _SharePreviewCache:
    """Small process-local, one-use cache of exact previews awaiting confirmation."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, str, SharePreview]] = {}

    def put(self, folder_id: str, preview: SharePreview) -> str:
        now = clock.monotonic()
        with self._lock:
            self._entries = {key: item for key, item in self._entries.items() if item[0] > now}
            if len(self._entries) >= 16:
                oldest = min(self._entries, key=lambda key: self._entries[key][0])
                del self._entries[oldest]
            token = secrets.token_urlsafe(32)
            self._entries[token] = (now + 900, folder_id, preview)
            return token

    def take(self, folder_id: str, token: str) -> SharePreview | None:
        with self._lock:
            item = self._entries.get(token)
            if item is None or item[0] <= clock.monotonic() or item[1] != folder_id:
                return None
            del self._entries[token]
            return item[2]


class Binder(BaseHTTPRequestHandler):
    store = Folders()
    share_previews = _SharePreviewCache()
    max_upload_size = MAX_UPLOAD_SIZE

    @staticmethod
    def _parse_tags(value: object) -> tuple[list[str] | None, str | None]:
        """Parse and validate a list of tags.

        Returns (tags, error_message). On success, error_message is None.
        """
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return None, "בקשה לא תקינה"

        tags = normalize_tags(value)

        if len(tags) > MAX_TAGS_PER_DOCUMENT:
            return None, "ניתן להוסיף עד 20 תגיות"

        for tag in tags:
            if len(tag) > MAX_TAG_LENGTH:
                return None, "הטקסט ארוך מדי"

        return tags, None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        attachment_parts = self._attachment_parts(path)
        if attachment_parts is not None:
            self._send_attachment(*attachment_parts)
            return
        if path == "/api/config":
            enabled = GOOGLE_DRIVE_IMPORT_ENABLED
            self._send_json(
                {
                    "googleClientId": GOOGLE_CLIENT_ID if enabled else "",
                    "googleApiKey": GOOGLE_API_KEY if enabled else "",
                }
            )
            return
        if path == "/api/duplicates":
            self._send_json(self.store.duplicates())
            return
        if path == "/api/tags":
            self._send_json(self.store.tags())
            return
        if path == "/api/link-types":
            self._send_json(self.store.link_types())
            return
        if path == "/api/search":
            self._search(parse_qs(urlparse(self.path).query))
            return
        if path == "/api/folders":
            self._send_json(self.store.list())
            return
        if path.startswith("/api/folders/"):
            folder_id = path.removeprefix("/api/folders/")
            folder = self.store.get(folder_id)
            if folder is None:
                self._send_json({"error": "התיקייה לא נמצאה"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json(folder)
            return
        if path == "/":
            page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            self._send_body(
                Translator(LOCALE).fill_html(page).encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if path == "/app.js":
            script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
            self._send_body(
                Translator(LOCALE).fill_script(script).encode("utf-8"),
                "text/javascript; charset=utf-8",
            )
            return
        if path == "/styles.css":
            self._send_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
            return
        if path == "/favicon.svg":
            self._send_file(STATIC_DIR / "favicon.svg", "image/svg+xml")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _search(self, params: dict[str, list[str]]) -> None:
        query = params.get("q", [""])[0].strip()
        if not query or len(query) > MAX_QUERY_LENGTH:
            self._send_json({"error": "invalid_query"}, HTTPStatus.BAD_REQUEST)
            return
        folder_id = params.get("folder_id", [""])[0].strip() or None
        if folder_id is not None and len(folder_id) > 100:
            self._send_json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            limit = int(params.get("limit", ["50"])[0])
        except ValueError:
            limit = 0
        if not 1 <= limit <= 500:
            self._send_json({"error": "invalid_limit"}, HTTPStatus.BAD_REQUEST)
            return
        mode = params.get("mode", ["hybrid"])[0]
        if mode not in SEARCH_MODES:
            self._send_json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(self.store.search(query, folder_id=folder_id, limit=limit, mode=mode))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        upload_parts = self._upload_parts(path)
        if upload_parts is not None:
            self._upload_files(*upload_parts)
            return
        is_folder_request = path == "/api/folders"
        is_document_request = path.startswith("/api/folders/") and path.endswith(
            "/documents"
        )
        is_link_types_request = path == "/api/link-types"
        link_match = re.fullmatch(r"/api/folders/([^/]+)/documents/([^/]+)/links", path)
        # Return 404 for deprecated /ai-threads endpoint
        if bool(re.fullmatch(r"/api/folders/[^/]+/ai-threads", path)):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        share_match = re.fullmatch(r"/api/folders/([^/]+)/documents/share-(preview|confirm)", path)
        is_ask_request = path == "/api/ask"
        if (
            not is_folder_request
            and not is_document_request
            and not is_link_types_request
            and not link_match
            and not share_match
            and not is_ask_request
        ):
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if share_match and (length <= 0 or length > 1_000_000):
                self._send_json({"error": "בקשה לא תקינה"}, HTTPStatus.BAD_REQUEST)
                return
            if is_ask_request and (length <= 0 or length > 20_000):
                self._send_json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
                return
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            if is_ask_request:
                self._send_json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            else:
                self._send_json({"error": "בקשה לא תקינה"}, HTTPStatus.BAD_REQUEST)
            return
        if is_ask_request:
            self._ask(payload)
            return
        if not isinstance(payload, dict):
            self._send_json({"error": "בקשה לא תקינה"}, HTTPStatus.BAD_REQUEST)
            return

        if is_document_request:
            self._create_document(path, payload)
            return
        if is_link_types_request:
            self._create_link_type(payload)
            return
        if link_match:
            self._create_link(link_match.group(1), link_match.group(2), payload)
            return
        if share_match:
            if share_match.group(2) == "preview":
                self._preview_shared_thread(share_match.group(1), payload)
            else:
                self._confirm_shared_thread(share_match.group(1), payload)
            return

        name = str(payload.get("name", "")).strip()
        description = str(payload.get("description", "")).strip()
        if not name:
            self._send_json({"error": "יש להזין שם לתיקייה"}, HTTPStatus.BAD_REQUEST)
            return
        if len(name) > 100 or len(description) > 500:
            self._send_json({"error": "הטקסט ארוך מדי"}, HTTPStatus.BAD_REQUEST)
            return

        self._send_json(self.store.create(name, description), HTTPStatus.CREATED)

    def _ask(self, payload: object) -> None:
        if not isinstance(payload, dict) or not set(payload) <= ASK_KEYS:
            self._send_json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return
        question = payload.get("question")
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= ASK_MAX_QUESTION_LENGTH:
            self._send_json({"error": "invalid_question"}, HTTPStatus.BAD_REQUEST)
            return
        folder_id = payload.get("folder_id")
        if "folder_id" in payload:
            if not isinstance(folder_id, str) or not folder_id.strip() or len(folder_id.strip()) > 100:
                self._send_json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
                return
            folder_id = folder_id.strip()
        else:
            folder_id = None
        limit = payload.get("limit", ASK_DEFAULT_DOCUMENTS)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= ASK_MAX_DOCUMENTS:
            self._send_json({"error": "invalid_limit"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            result = self.store.ask(question.strip(), folder_id=folder_id, limit=limit)
        except AskDisabled:
            self._send_json({"error": "ask_disabled"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        except ChatUnavailable:
            self._send_json({"error": "generation_unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self._send_json(result)

    def _preview_shared_thread(self, folder_id: str, payload: dict[str, object]) -> None:
        if set(payload) != {"url"} or not isinstance(payload["url"], str):
            self._send_json({"error": "invalid_link"}, HTTPStatus.BAD_REQUEST)
            return
        if self.store.get(folder_id) is None:
            self._send_json({"error": "folder_not_found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            preview = import_share_url(payload["url"])
        except ShareImportError as exc:
            status = HTTPStatus.BAD_REQUEST if exc.code == "invalid_link" else HTTPStatus.UNPROCESSABLE_ENTITY
            self._send_json({"error": exc.code}, status)
            return
        token = self.share_previews.put(folder_id, preview)
        self._send_json({
            "preview_id": token,
            "source_url": preview.source_url,
            "title": preview.title,
            "transcript": preview.transcript,
            "message_count": preview.message_count,
            "warnings": list(preview.warnings),
            "source_first_message_at": preview.source_first_message_at,
        })

    def _confirm_shared_thread(self, folder_id: str, payload: dict[str, object]) -> None:
        if set(payload) - {"preview_id", "conversation_date", "conversation_time", "summary"} or any(
            not isinstance(payload.get(key, ""), str)
            for key in ("preview_id", "conversation_date", "conversation_time", "summary")
        ):
            self._send_json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return
        token = payload.get("preview_id", "")
        conversation_date = payload.get("conversation_date", "")
        conversation_time = payload.get("conversation_time", "")
        summary = payload.get("summary", "")
        if not token or len(token) > 100 or len(summary) > 10_000:
            self._send_json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", conversation_date):
                raise ValueError
            date.fromisoformat(conversation_date)
        except ValueError:
            self._send_json({"error": "invalid_date"}, HTTPStatus.BAD_REQUEST)
            return
        if conversation_time and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", conversation_time):
            self._send_json({"error": "invalid_time"}, HTTPStatus.BAD_REQUEST)
            return
        if self.store.get(folder_id) is None:
            self._send_json({"error": "folder_not_found"}, HTTPStatus.NOT_FOUND)
            return
        preview = self.share_previews.take(folder_id, token)
        if preview is None:
            self._send_json({"error": "preview_unavailable"}, HTTPStatus.GONE)
            return
        document = self.store.create_document(
            folder_id, conversation_date, preview.title,
            description="",
            content=preview.transcript,
            time=conversation_time or "",
            summary=summary,
            tags=[],
            document_type="ai",
            provider="ChatGPT",
            source_url=preview.source_url,
            transcript_sha256=hashlib.sha256(preview.transcript.encode("utf-8")).hexdigest(),
            source_first_message_at=preview.source_first_message_at,
        )
        if document is None:
            self._send_json({"error": "folder_not_found"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json(document, HTTPStatus.CREATED)

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if not (
            len(parts) == 5
            and parts[:2] == ["api", "folders"]
            and parts[3] == "documents"
        ):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._send_json({"error": "בקשה לא תקינה"}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(payload, dict):
            self._send_json({"error": "בקשה לא תקינה"}, HTTPStatus.BAD_REQUEST)
            return

        # Reject type field (immutable after creation)
        if "type" in payload:
            self._send_json({"error": "בקשה לא תקינה"}, HTTPStatus.BAD_REQUEST)
            return

        allowed_keys = {"name", "date", "time", "description", "content", "summary", "tags", "provider"}
        updates = {k: v for k, v in payload.items() if k in allowed_keys}

        if not updates:
            self._send_json(
                {"error": "לא נמצאו שינויים לשמירה"}, HTTPStatus.BAD_REQUEST
            )
            return

        # Validate tags if present
        if "tags" in updates:
            tags, tag_error = self._parse_tags(updates["tags"])
            if tag_error:
                self._send_json({"error": tag_error}, HTTPStatus.BAD_REQUEST)
                return
            updates["tags"] = tags

        if any(not isinstance(value, (str, list)) for value in updates.values()):
            self._send_json({"error": "בקשה לא תקינה"}, HTTPStatus.BAD_REQUEST)
            return
        updates = {
            key: value.strip() if isinstance(value, str) else value
            for key, value in updates.items()
        }
        if "name" in updates and not updates["name"]:
            self._send_json(
                {"error": "יש להזין שם ותאריך למסמך"}, HTTPStatus.BAD_REQUEST
            )
            return
        if (
            len(updates.get("name", "")) > 200
            or len(updates.get("description", "")) > 1000
        ):
            self._send_json({"error": "הטקסט ארוך מדי"}, HTTPStatus.BAD_REQUEST)
            return

        if "date" in updates:
            try:
                date.fromisoformat(str(updates["date"]))
            except ValueError:
                self._send_json({"error": "התאריך אינו תקין"}, HTTPStatus.BAD_REQUEST)
                return
        if updates.get("time"):
            try:
                time.fromisoformat(str(updates["time"]))
            except ValueError:
                self._send_json({"error": "השעה אינה תקינה"}, HTTPStatus.BAD_REQUEST)
                return

        try:
            document = self.store.update_document(parts[2], parts[4], updates)
        except DocumentRuleError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if document is None:
            self._send_json({"error": "המסמך לא נמצא"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json(document)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")

        # Check if this is a link deletion (7 parts: api/folders/{folder_id}/documents/{document_id}/links/{link_id})
        if len(parts) == 7 and parts[:2] == ["api", "folders"] and parts[3] == "documents" and parts[5] == "links":
            folder_id = parts[2]
            document_id = parts[4]
            link_id = parts[6]
            document = remove_document_link(self.store.factory, folder_id, document_id, link_id)
            if document is None:
                self._send_json({"error": "link_not_found"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json(document, HTTPStatus.OK)
            return

        # Otherwise, handle as file deletion
        attachment_parts = self._attachment_parts(path)
        if attachment_parts is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        folder_id, document_id, file_id = attachment_parts
        document = self.store.remove_file(folder_id, document_id, file_id)
        if document is None:
            self._send_json({"error": "הקובץ לא נמצא"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json(document)

    def _create_document(self, path: str, payload: dict[str, object]) -> None:
        folder_id = path.removeprefix("/api/folders/").removesuffix("/documents")
        name = str(payload.get("name", "")).strip()
        document_date = str(payload.get("date", "")).strip()
        document_time = str(payload.get("time", "")).strip()
        description = str(payload.get("description", "")).strip()
        summary = str(payload.get("summary", "")).strip()
        document_type = str(payload.get("type", DOCUMENT_TYPE_REGULAR)).strip()
        provider = str(payload.get("provider", "")).strip()

        # Apply type-aware content processing
        raw_content = payload.get("content", "")
        if not isinstance(raw_content, str):
            raw_content = ""
        content = raw_content if document_type == DOCUMENT_TYPE_AI else raw_content.strip()

        # Validate document type
        if document_type not in DOCUMENT_TYPES:
            self._send_json({"error": "בקשה לא תקינה"}, HTTPStatus.BAD_REQUEST)
            return

        tags, tag_error = self._parse_tags(payload.get("tags", []))
        if tag_error:
            self._send_json({"error": tag_error}, HTTPStatus.BAD_REQUEST)
            return

        if not name or not document_date:
            self._send_json(
                {"error": "יש להזין שם ותאריך למסמך"}, HTTPStatus.BAD_REQUEST
            )
            return
        try:
            date.fromisoformat(document_date)
        except ValueError:
            self._send_json({"error": "התאריך אינו תקין"}, HTTPStatus.BAD_REQUEST)
            return
        if document_time:
            try:
                time.fromisoformat(document_time)
            except ValueError:
                self._send_json({"error": "השעה אינה תקינה"}, HTTPStatus.BAD_REQUEST)
                return
        if len(name) > 200 or len(description) > 1000 or len(summary) > MAX_SUMMARY_LENGTH[document_type]:
            self._send_json({"error": "הטקסט ארוך מדי"}, HTTPStatus.BAD_REQUEST)
            return

        # Validate provider: only allowed on AI documents
        if provider and document_type != DOCUMENT_TYPE_AI:
            self._send_json({"error": "בקשה לא תקינה"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            document = self.store.create_document(
                folder_id,
                document_date,
                name,
                description,
                content,
                document_time,
                summary=summary,
                tags=tags,
                document_type=document_type,
                provider=provider,
            )
        except DocumentRuleError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if document is None:
            self._send_json({"error": "התיקייה לא נמצאה"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json(document, HTTPStatus.CREATED)

    def _create_link_type(self, payload: dict[str, object]) -> None:
        """Create a link type. Returns 201 if created, 200 if existing."""
        if not isinstance(payload.get("name"), str):
            self._send_json({"error": "invalid_link_type"}, HTTPStatus.BAD_REQUEST)
            return

        name = str(payload.get("name", "")).strip()
        normalized = normalize_tag(name)

        if not normalized or len(normalized) > MAX_LINK_TYPE_LENGTH:
            self._send_json({"error": "invalid_link_type"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            link_type, created = create_link_type(self.store.factory, name)
        except ValueError:
            self._send_json({"error": "invalid_link_type"}, HTTPStatus.BAD_REQUEST)
            return

        status = HTTPStatus.CREATED if created else HTTPStatus.OK
        self._send_json(link_type, status)

    def _create_link(self, folder_id: str, document_id: str, payload: dict[str, object]) -> None:
        """Create a document link."""
        target_document_id = payload.get("target_document_id", "")
        link_type_id = payload.get("link_type_id", "")

        if not isinstance(target_document_id, str) or not target_document_id:
            self._send_json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(link_type_id, str) or not link_type_id:
            self._send_json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            document = add_document_link(
                self.store.factory, folder_id, document_id, target_document_id, link_type_id
            )
        except LinkError as exc:
            if exc.code == "duplicate_link":
                self._send_json({"error": exc.code}, HTTPStatus.CONFLICT)
            else:
                self._send_json({"error": exc.code}, HTTPStatus.BAD_REQUEST)
            return

        if document is None:
            self._send_json({"error": "document_not_found"}, HTTPStatus.NOT_FOUND)
            return

        self._send_json(document, HTTPStatus.CREATED)

    def _upload_files(self, folder_id: str, document_id: str) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > self.max_upload_size:
            limit_mb = self.max_upload_size // (1024 * 1024)
            self._send_json(
                {"error": f"הקבצים חורגים ממגבלת {limit_mb}MB"},
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
            return
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            self._send_json({"error": "בקשת העלאה לא תקינה"}, HTTPStatus.BAD_REQUEST)
            return

        uploads = self._read_multipart_uploads(content_type, length)
        if not uploads:
            self._send_json({"error": "לא נבחרו קבצים"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            saved = self.store.add_files(folder_id, document_id, uploads)
        finally:
            for _, _, upload in uploads:
                upload.close()
        if saved is None:
            self._send_json({"error": "המסמך לא נמצא"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json(saved, HTTPStatus.CREATED)

    def _read_multipart_uploads(
        self, content_type: str, length: int
    ) -> list[tuple[str, str, BinaryIO]]:
        content_type_header = Message()
        content_type_header["content-type"] = content_type
        boundary = content_type_header.get_param("boundary", header="content-type")
        if not boundary:
            return []

        marker = b"--" + str(boundary).encode("utf-8")
        closing_marker = marker + b"--"
        remaining = length

        def read_line(limit: int = 1024 * 1024) -> bytes:
            nonlocal remaining
            if remaining <= 0:
                return b""
            chunk = self.rfile.readline(min(limit, remaining))
            remaining -= len(chunk)
            return chunk

        if read_line().rstrip(b"\r\n") != marker:
            return []

        uploads = []
        while remaining > 0:
            header_lines = []
            header_size = 0
            while True:
                line = read_line(64 * 1024)
                if not line or line in (b"\r\n", b"\n"):
                    break
                header_size += len(line)
                if header_size > 64 * 1024:
                    for _, _, upload in uploads:
                        upload.close()
                    return []
                header_lines.append(line)

            headers = BytesParser(policy=policy.default).parsebytes(
                b"".join(header_lines) + b"\r\n"
            )
            filename = headers.get_filename()
            upload = TemporaryFile() if filename else None  # noqa: SIM115 closed below, after the part is read
            previous = b""
            boundary_line = b""
            while remaining > 0:
                line = read_line()
                stripped = line.rstrip(b"\r\n")
                if stripped in (marker, closing_marker):
                    if upload is not None:
                        upload.write(previous.removesuffix(b"\r\n"))
                        upload.seek(0)
                    boundary_line = stripped
                    break
                if upload is not None:
                    upload.write(previous)
                previous = line

            if filename and upload is not None:
                uploads.append(
                    (
                        filename,
                        headers.get_content_type(),
                        upload,
                    )
                )
            elif upload is not None:
                upload.close()
            if boundary_line == closing_marker:
                break
            if not boundary_line:
                for _, _, pending_upload in uploads:
                    pending_upload.close()
                return []
        return uploads

    def _send_attachment(
        self, folder_id: str, document_id: str, file_id: str
    ) -> None:
        result = self.store.get_file(folder_id, document_id, file_id)
        if result is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body, content_type, name = result
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Disposition", f"inline; filename*=UTF-8''{quote(name, safe='')}"
        )
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _upload_parts(path: str) -> tuple[str, str] | None:
        parts = path.strip("/").split("/")
        if len(parts) == 6 and parts[:2] == ["api", "folders"] and parts[3] == "documents" and parts[5] == "files":
            return parts[2], parts[4]
        return None

    @staticmethod
    def _attachment_parts(path: str) -> tuple[str, str, str] | None:
        parts = path.strip("/").split("/")
        if len(parts) == 7 and parts[:2] == ["api", "folders"] and parts[3] == "documents" and parts[5] == "files":
            return parts[2], parts[4], parts[6]
        return None

    def _send_json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        self._send_body(path.read_bytes(), content_type)

    def _send_body(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")
