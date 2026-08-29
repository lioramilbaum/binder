from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4


PROJECT_ROOT = Path(__file__).parents[2]
DATA_DIR = Path(os.environ.get("BINDER_DATA_DIR", PROJECT_ROOT / "data"))
STORE_PATH = DATA_DIR / "folders.json"
STATIC_DIR = PROJECT_ROOT / "static"


class FolderStore:
    def __init__(self, path: Path = STORE_PATH):
        self.path = path

    def list(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return sorted(data, key=lambda folder: folder["updated_at"], reverse=True)

    def create(self, name: str, description: str = "") -> dict[str, str]:
        folders = self.list()
        now = datetime.now(timezone.utc).isoformat()
        folder = {
            "id": str(uuid4()),
            "name": name.strip(),
            "description": description.strip(),
            "created_at": now,
            "updated_at": now,
        }
        folders.append(folder)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(folders, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(self.path)
        return folder


class BinderHandler(BaseHTTPRequestHandler):
    store = FolderStore()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/folders":
            self._send_json(self.store.list())
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


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), BinderHandler)
    print(f"Binder running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
