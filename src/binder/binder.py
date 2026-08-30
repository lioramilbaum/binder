import json
from datetime import date, time
from email import policy
from email.message import Message
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from tempfile import TemporaryFile
from typing import BinaryIO, Optional
from urllib.parse import urlparse

from .config import MAX_UPLOAD_SIZE, STATIC_DIR
from .folders import Folders


class Binder(BaseHTTPRequestHandler):
    store = Folders()
    max_upload_size = MAX_UPLOAD_SIZE

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        attachment_parts = self._attachment_parts(path)
        if attachment_parts is not None:
            self._send_attachment(*attachment_parts)
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
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if path == "/app.js":
            self._send_file(STATIC_DIR / "app.js", "text/javascript; charset=utf-8")
            return
        if path == "/styles.css":
            self._send_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
            return
        if path == "/favicon.svg":
            self._send_file(STATIC_DIR / "favicon.svg", "image/svg+xml")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

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
        if not is_folder_request and not is_document_request:
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

        if is_document_request:
            self._create_document(path, payload)
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
        if "content" in payload:
            document = self.store.update_document_content(
                parts[2], parts[4], str(payload["content"])
            )
        else:
            self._send_json({"error": "לא נמצאו שינויים לשמירה"}, HTTPStatus.BAD_REQUEST)
            return
        if document is None:
            self._send_json({"error": "המסמך לא נמצא"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json(document)

    def _create_document(self, path: str, payload: dict[str, object]) -> None:
        folder_id = path.removeprefix("/api/folders/").removesuffix("/documents")
        name = str(payload.get("name", "")).strip()
        document_date = str(payload.get("date", "")).strip()
        document_time = str(payload.get("time", "")).strip()
        description = str(payload.get("description", "")).strip()
        content = str(payload.get("content", "")).strip()

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
        if len(name) > 200 or len(description) > 1000:
            self._send_json({"error": "הטקסט ארוך מדי"}, HTTPStatus.BAD_REQUEST)
            return

        document = self.store.create_document(
            folder_id, document_date, name, description, content, document_time
        )
        if document is None:
            self._send_json({"error": "התיקייה לא נמצאה"}, HTTPStatus.NOT_FOUND)
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
            upload = TemporaryFile() if filename else None
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
        path, attachment = result
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", str(attachment["content_type"]))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _upload_parts(path: str) -> Optional[tuple[str, str]]:
        parts = path.strip("/").split("/")
        if len(parts) == 6 and parts[:2] == ["api", "folders"]:
            if parts[3] == "documents" and parts[5] == "files":
                return parts[2], parts[4]
        return None

    @staticmethod
    def _attachment_parts(path: str) -> Optional[tuple[str, str, str]]:
        parts = path.strip("/").split("/")
        if len(parts) == 7 and parts[:2] == ["api", "folders"]:
            if parts[3] == "documents" and parts[5] == "files":
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
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")
