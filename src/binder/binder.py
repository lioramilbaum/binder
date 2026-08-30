import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from .config import STATIC_DIR
from .folders import Folders


class Binder(BaseHTTPRequestHandler):
    store = Folders()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
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
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/folders":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._send_json({"error": "בקשה לא תקינה"}, HTTPStatus.BAD_REQUEST)
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
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")
