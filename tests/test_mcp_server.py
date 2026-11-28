import http.client
import io
import json
import os
import queue
import subprocess
import sys
import threading
import unittest
import urllib.request
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError, URLError

from mcp import Client

from binder.mcp_server import (
    BinderApiError,
    BinderClient,
    BinderConfigError,
    _is_loopback,
    _opener_for,
    create_server,
    main,
)
from binder.text_extraction import extract_text

TOOL_NAMES = {
    "search_documents",
    "get_document",
    "list_folders",
    "list_documents",
    "list_tags",
    "save_document",
}
READ_ONLY_TOOLS = TOOL_NAMES - {"save_document"}
BASE_URL = "http://127.0.0.1:8000"


def _json_response(body):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(body).encode("utf-8")
    return response


class BinderClientTests(unittest.TestCase):
    def setUp(self):
        self.opener = Mock()
        self.open = self.opener.open
        self.client = BinderClient(BASE_URL, opener=self.opener)
        self.open.return_value = _json_response({"results": []})

    def requested_url(self):
        return self.open.call_args[0][0].full_url

    def test_search_builds_query_string_with_percent_encoded_hebrew(self):
        self.client.search("שכירות")

        self.assertEqual(
            self.requested_url(),
            f"{BASE_URL}/api/search?q=%D7%A9%D7%9B%D7%99%D7%A8%D7%95%D7%AA&limit=10&mode=hybrid",
        )

    def test_search_adds_folder_only_when_given(self):
        self.client.search("lease", folder_id="f1", limit=5, mode="keyword")

        self.assertEqual(
            self.requested_url(),
            f"{BASE_URL}/api/search?q=lease&limit=5&mode=keyword&folder_id=f1",
        )

    def test_get_document_hits_document_path(self):
        self.open.return_value = _json_response({"id": "d1"})

        self.assertEqual(self.client.get_document("f1", "d1"), {"id": "d1"})
        self.assertEqual(self.requested_url(), f"{BASE_URL}/api/folders/f1/documents/d1")

    def test_ids_are_path_quoted(self):
        self.client.get_document("f/1", "d1")

        self.assertEqual(self.requested_url(), f"{BASE_URL}/api/folders/f%2F1/documents/d1")

    def test_create_document_posts_utf8_json(self):
        self.open.return_value = _json_response({"id": "new"})
        payload = {"name": "סיכום", "date": "2026-11-24", "type": "ai"}

        self.client.create_document("f1", payload)

        request = self.open.call_args[0][0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, f"{BASE_URL}/api/folders/f1/documents")
        self.assertEqual(json.loads(request.data.decode("utf-8")), payload)
        self.assertIn("סיכום".encode(), request.data)

    def test_http_error_raises_binder_api_error_with_code(self):
        self.open.side_effect = HTTPError(
            f"{BASE_URL}/api/folders/f1/documents/d1",
            404,
            "Not Found",
            hdrs=None,
            fp=io.BytesIO(b'{"error": "document_not_found"}'),
        )

        with self.assertRaises(BinderApiError) as raised:
            self.client.get_document("f1", "d1")

        self.assertEqual(raised.exception.code, "document_not_found")
        self.assertEqual(raised.exception.status, 404)

    def test_non_json_error_body_falls_back_to_the_reason(self):
        self.open.side_effect = HTTPError(
            f"{BASE_URL}/api/search",
            500,
            "Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(b"<html>oops</html>"),
        )

        with self.assertRaises(BinderApiError) as raised:
            self.client.search("lease")

        self.assertEqual(raised.exception.status, 500)
        self.assertEqual(raised.exception.code, "Internal Server Error")

    def test_unreachable_app_raises_message_with_base_url(self):
        self.open.side_effect = URLError("connection refused")

        with self.assertRaises(BinderApiError) as raised:
            self.client.folders()

        self.assertEqual(raised.exception.code, "unreachable")
        self.assertIn(BASE_URL, str(raised.exception))

    def test_timeout_is_reported_as_unreachable(self):
        self.open.side_effect = TimeoutError("timed out")

        with self.assertRaises(BinderApiError) as raised:
            self.client.folders()

        self.assertEqual(raised.exception.code, "unreachable")
        self.assertIn("Cannot reach Binder", str(raised.exception))

    def test_non_json_success_body_raises_bad_response(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"<html>not json</html>"
        self.open.return_value = response

        with self.assertRaises(BinderApiError) as raised:
            self.client.folders()

        self.assertEqual(raised.exception.code, "bad_response")
        self.assertIsNone(raised.exception.status)
        self.assertEqual(str(raised.exception), f"Unexpected response from Binder at {BASE_URL}")

    def test_connection_dropped_mid_body_raises_bad_response(self):
        for error in (http.client.IncompleteRead(b'{"results"'), ConnectionResetError("reset")):
            with self.subTest(error=type(error).__name__):
                response = MagicMock()
                response.__enter__.return_value.read.side_effect = error
                self.open.return_value = response

                with self.assertRaises(BinderApiError) as raised:
                    self.client.search("lease")

                self.assertEqual(raised.exception.code, "bad_response")


class ProxyBypassTests(unittest.TestCase):
    def test_loopback_hosts_are_detected(self):
        for url in (
            "http://127.0.0.1:8000",
            "http://127.0.0.2:8000",
            "http://localhost:8000",
            "http://LOCALHOST:8000",
            "http://[::1]:8000",
        ):
            with self.subTest(url=url):
                self.assertTrue(_is_loopback(url))

    def test_other_hosts_are_not_loopback(self):
        for url in ("http://binder.example:8000", "http://192.168.1.10:8000", "http://localhost.example"):
            with self.subTest(url=url):
                self.assertFalse(_is_loopback(url))

    @staticmethod
    def proxy_maps(opener):
        return [
            handler.proxies
            for handler in opener.handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        ]

    def test_loopback_opener_ignores_proxy_environment(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.example:3128"}):
            opener = _opener_for(BASE_URL)

        # An empty ProxyHandler registers no openers, so no proxy handler is active.
        # LoopbackRequestTests checks the behaviour end to end.
        self.assertEqual(self.proxy_maps(opener), [])

    def test_other_hosts_keep_proxy_environment(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.example:3128"}):
            opener = _opener_for("http://binder.example:8000")

        self.assertEqual(self.proxy_maps(opener)[0].get("http"), "http://proxy.example:3128")


class _FoldersHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class LoopbackRequestTests(unittest.TestCase):
    def test_request_to_loopback_succeeds_despite_proxy_environment(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FoldersHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        # Nothing listens on port 9 here: a request routed through this proxy would fail.
        proxy = {"HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9"}

        with patch.dict(os.environ, proxy):
            folders = BinderClient(f"http://127.0.0.1:{port}").folders()

        self.assertEqual(folders, [])


class McpToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.binder = Mock(spec=BinderClient)
        self.server = create_server(client=self.binder)

    async def call(self, name, arguments=None):
        async with Client(self.server) as client:
            return await client.call_tool(name, arguments or {})

    async def test_lists_exactly_the_six_tools_with_no_delete_upload_or_edit(self):
        async with Client(self.server) as client:
            tools = (await client.list_tools()).tools

        self.assertEqual({tool.name for tool in tools}, TOOL_NAMES)
        for tool in tools:
            for word in ("delete", "upload", "update", "remove"):
                self.assertNotIn(word, tool.name)

    async def test_read_only_hints_are_set_for_read_tools_only(self):
        async with Client(self.server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}

        for name in READ_ONLY_TOOLS:
            self.assertTrue(tools[name].annotations.read_only_hint, name)
            self.assertFalse(tools[name].annotations.open_world_hint, name)
        self.assertFalse(tools["save_document"].annotations.read_only_hint)
        self.assertFalse(tools["save_document"].annotations.destructive_hint)
        self.assertFalse(tools["save_document"].annotations.open_world_hint)

    async def test_search_uses_defaults_and_trims_results(self):
        self.binder.search.return_value = {
            "query": "lease",
            "semantic": "ok",
            "embeddings_pending": 0,
            "results": [
                {
                    "id": "d1",
                    "folder_id": "f1",
                    "folder_name": "Case",
                    "name": "Lease",
                    "date": "2026-03-01",
                    "type": "regular",
                    "rank": 0.1,
                    "matched_by": ["keyword"],
                    "score": 0.03,
                }
            ],
        }

        result = await self.call("search_documents", {"query": "lease"})

        self.binder.search.assert_called_once_with("lease", folder_id=None, limit=10, mode="hybrid")
        self.assertEqual(
            result.structured_content,
            {
                "results": [
                    {
                        "id": "d1",
                        "folder_id": "f1",
                        "folder_name": "Case",
                        "name": "Lease",
                        "date": "2026-03-01",
                        "type": "regular",
                        "matched_by": ["keyword"],
                    }
                ],
                "semantic": "ok",
            },
        )

    async def test_search_against_outdated_app_reports_rebuild(self):
        self.binder.search.return_value = {
            "results": [{"id": "d1", "folder_id": "f1", "folder_name": "Case", "matched_by": ["keyword"]}],
            "semantic": "ok",
        }

        result = await self.call("search_documents", {"query": "lease"})

        self.assertTrue(result.is_error)
        self.assertIn("out of date", result.content[0].text)
        self.assertIn("make up", result.content[0].text)

    async def test_search_validates_query_and_limit(self):
        cases = [
            {"query": "   "},
            {"query": "a" * 201},
            {"query": "lease", "limit": 0},
            {"query": "lease", "limit": 51},
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = await self.call("search_documents", arguments)

                self.assertTrue(result.is_error)
        self.binder.search.assert_not_called()

    async def test_get_document_pages_long_body(self):
        self.binder.get_document.return_value = {
            "id": "d1",
            "name": "Long",
            "type": "regular",
            "date": "2026-03-01",
            "time": "",
            "tags": [],
            "description": "",
            "summary": "",
            "content": "x" * 50_000,
            "files": [],
            "attachments_text": [],
        }

        first = await self.call("get_document", {"folder_id": "f1", "document_id": "d1", "max_chars": 20000})
        tail = await self.call(
            "get_document",
            {"folder_id": "f1", "document_id": "d1", "offset": 40000, "max_chars": 20000},
        )

        self.assertEqual(len(first.structured_content["text"]), 20000)
        self.assertEqual(first.structured_content["total_chars"], 50000)
        self.assertEqual(first.structured_content["next_offset"], 20000)
        self.assertEqual(first.structured_content["folder_id"], "f1")
        self.assertEqual(len(tail.structured_content["text"]), 10000)
        self.assertIsNone(tail.structured_content["next_offset"])

    async def test_get_document_rejects_bad_offset_and_max_chars(self):
        cases = [{"offset": -1}, {"max_chars": 0}, {"max_chars": 100_001}]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = await self.call(
                    "get_document", {"folder_id": "f1", "document_id": "d1", **arguments}
                )

                self.assertTrue(result.is_error)
        self.binder.get_document.assert_not_called()

    async def test_get_document_appends_attachment_text_after_body(self):
        self.binder.get_document.return_value = {
            "id": "d1",
            "name": "Scan",
            "type": "regular",
            "date": "2026-03-01",
            "time": "",
            "tags": [],
            "description": "",
            "summary": "",
            "content": "Cover note",
            "files": [{"id": "x1", "name": "minutes.txt", "content_type": "text/plain", "size": 20}],
            "attachments_text": [{"file_id": "x1", "name": "minutes.txt", "text": "Parking minutes"}],
        }

        result = await self.call("get_document", {"folder_id": "f1", "document_id": "d1"})

        self.assertEqual(
            result.structured_content["text"],
            "Cover note\n\nAttachment minutes.txt:\nParking minutes",
        )
        self.assertEqual(result.structured_content["files"], ["minutes.txt"])

    async def test_whatsapp_export_text_reaches_the_model_through_attachments(self):
        # Run the sample through the real upload extraction, so the text matches what is stored.
        raw = b"[1/3/26, 10:00] Liora Milbaum: Shall we meet?\n[1/3/26, 10:05] Dana: Yes"
        chat = extract_text("WhatsApp Chat.txt", "text/plain", raw, len(raw))
        self.assertEqual(chat, "[1/3/26, 10:00] Liora Milbaum: Shall we meet? [1/3/26, 10:05] Dana: Yes")
        self.binder.get_document.return_value = {
            "id": "d1",
            "name": "Chat",
            "type": "regular",
            "date": "2026-03-01",
            "time": "",
            "tags": [],
            "description": "",
            "summary": "",
            "content": "",
            "files": [{"id": "x1", "name": "WhatsApp Chat.txt", "content_type": "text/plain", "size": 90}],
            "whatsapp_chat": {"messages": [], "participants": [], "me": "Liora Milbaum"},
            "attachments_text": [{"file_id": "x1", "name": "WhatsApp Chat.txt", "text": chat}],
        }

        result = await self.call("get_document", {"folder_id": "f1", "document_id": "d1"})

        self.assertEqual(result.structured_content["text"], f"Attachment WhatsApp Chat.txt:\n{chat}")

    async def test_missing_document_is_reported_as_not_found(self):
        self.binder.get_document.side_effect = BinderApiError(
            "Binder returned HTTP 404: document_not_found", status=404, code="document_not_found"
        )

        result = await self.call("get_document", {"folder_id": "f1", "document_id": "d1"})

        self.assertTrue(result.is_error)
        self.assertTrue(result.content[0].text.endswith("Document not found in that folder."))

    async def test_old_app_404_is_reported_as_possibly_outdated(self):
        self.binder.get_document.side_effect = BinderApiError(
            "Binder returned HTTP 404: התיקייה לא נמצאה", status=404, code="התיקייה לא נמצאה"
        )

        result = await self.call("get_document", {"folder_id": "f1", "document_id": "d1"})

        self.assertTrue(result.is_error)
        self.assertIn("may be out of date", result.content[0].text)

    async def test_other_backend_errors_are_passed_through(self):
        self.binder.get_document.side_effect = BinderApiError(
            "Binder returned HTTP 500: boom", status=500, code="boom"
        )

        result = await self.call("get_document", {"folder_id": "f1", "document_id": "d1"})

        self.assertTrue(result.is_error)
        self.assertIn("boom", result.content[0].text)

    async def test_list_folders_trims_to_counts(self):
        self.binder.folders.return_value = [
            {
                "id": "f1",
                "name": "Case",
                "description": "Notes",
                "created_at": "2026-01-01",
                "updated_at": "2026-02-01",
                "documents": [{"id": "d1", "summary": "long"}, {"id": "d2", "summary": "x"}],
            }
        ]

        result = await self.call("list_folders")

        self.assertEqual(
            result.structured_content,
            {
                "result": [
                    {
                        "id": "f1",
                        "name": "Case",
                        "description": "Notes",
                        "document_count": 2,
                        "updated_at": "2026-02-01",
                    }
                ]
            },
        )

    async def test_list_documents_truncates_summaries(self):
        self.binder.folders.return_value = [
            {
                "id": "f1",
                "documents": [
                    {
                        "id": "d1",
                        "name": "Long",
                        "date": "2026-03-01",
                        "type": "regular",
                        "tags": ["t"],
                        "summary": "s" * 600,
                        "description": "not returned",
                    }
                ],
            }
        ]

        result = await self.call("list_documents", {"folder_id": "f1"})

        [document] = result.structured_content["result"]
        self.assertEqual(len(document["summary"]), 500)
        self.assertNotIn("description", document)
        self.assertEqual(document["tags"], ["t"])

    async def test_list_documents_unknown_folder_is_an_error(self):
        self.binder.folders.return_value = [{"id": "f1", "documents": []}]

        result = await self.call("list_documents", {"folder_id": "missing"})

        self.assertTrue(result.is_error)

    async def test_list_tags_passes_through(self):
        tags = [{"name": "zebra", "document_count": 2}]
        self.binder.tags.return_value = tags

        result = await self.call("list_tags")

        self.assertEqual(result.structured_content, {"result": tags})

    async def test_save_document_defaults_to_today_ai_claude(self):
        self.binder.create_document.return_value = {
            "id": "new",
            "name": "Chat",
            "date": date.today().isoformat(),
            "type": "ai",
        }

        result = await self.call("save_document", {"folder_id": "f1", "name": "Chat", "content": "Hello"})

        folder_id, payload = self.binder.create_document.call_args[0]
        self.assertEqual(folder_id, "f1")
        self.assertEqual(payload["date"], date.today().isoformat())
        self.assertEqual(payload["type"], "ai")
        self.assertEqual(payload["provider"], "Claude")
        self.assertEqual(result.structured_content["id"], "new")
        self.assertEqual(result.structured_content["folder_id"], "f1")

    async def test_regular_document_without_provider_is_saved_without_one(self):
        self.binder.create_document.return_value = {
            "id": "new",
            "name": "Note",
            "date": "2026-03-01",
            "type": "regular",
        }

        result = await self.call(
            "save_document",
            {"folder_id": "f1", "name": "Note", "content": "x", "document_type": "regular", "date": "2026-03-01"},
        )

        _, payload = self.binder.create_document.call_args[0]
        self.assertFalse(result.is_error)
        self.assertEqual(payload["type"], "regular")
        self.assertNotIn("provider", payload)
        self.assertEqual(result.structured_content["type"], "regular")

    async def test_whitespace_provider_on_ai_document_falls_back_to_claude(self):
        self.binder.create_document.return_value = {"id": "new", "name": "Chat", "date": "2026-03-01", "type": "ai"}

        await self.call(
            "save_document",
            {"folder_id": "f1", "name": "Chat", "content": "x", "date": "2026-03-01", "provider": "   "},
        )

        _, payload = self.binder.create_document.call_args[0]
        self.assertEqual(payload["provider"], "Claude")

    async def test_whitespace_provider_on_regular_document_is_not_a_provider(self):
        self.binder.create_document.return_value = {"id": "new", "name": "Note", "date": "2026-03-01", "type": "regular"}

        result = await self.call(
            "save_document",
            {
                "folder_id": "f1",
                "name": "Note",
                "content": "x",
                "date": "2026-03-01",
                "document_type": "regular",
                "provider": "  ",
            },
        )

        _, payload = self.binder.create_document.call_args[0]
        self.assertFalse(result.is_error)
        self.assertNotIn("provider", payload)

    async def test_save_regular_document_with_provider_is_rejected_without_calling_client(self):
        result = await self.call(
            "save_document",
            {"folder_id": "f1", "name": "Note", "content": "x", "document_type": "regular", "provider": "Claude"},
        )

        self.assertTrue(result.is_error)
        self.binder.create_document.assert_not_called()

    async def test_save_document_rejections_do_not_call_client(self):
        base = {"folder_id": "f1", "name": "Note", "content": "x", "date": "2026-03-01"}
        cases = {
            "invalid date": {"date": "2026-13-01"},
            "name too long": {"name": "n" * 201},
            "too many tags": {"tags": [f"t{i}" for i in range(21)]},
            "tag too long": {"tags": ["x" * 51]},
            "regular summary over 1000": {"document_type": "regular", "summary": "s" * 1001},
            "ai summary over 10000": {"document_type": "ai", "summary": "s" * 10_001},
        }
        for label, overrides in cases.items():
            with self.subTest(label):
                result = await self.call("save_document", {**base, **overrides})

                self.assertTrue(result.is_error)
        self.binder.create_document.assert_not_called()

    async def test_invalid_input_is_rejected_without_calling_client(self):
        empty = await self.call("search_documents", {"query": ""})
        no_results = await self.call("search_documents", {"query": "lease", "limit": 0})

        self.assertTrue(empty.is_error)
        self.assertTrue(no_results.is_error)
        self.binder.search.assert_not_called()


def _pump(stream, lines):
    """Copy stdout lines to a queue from a helper thread; ``None`` marks end of stream."""
    for line in stream:
        lines.put(line)
    lines.put(None)


class BinderMcpEntryPointTests(unittest.TestCase):
    """Runs the real ``binder-mcp`` entry point as a subprocess over stdio."""

    def setUp(self):
        # Closed port: listing tools must not need the app to be running.
        env = {**os.environ, "BINDER_URL": "http://127.0.0.1:9"}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "binder.mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
        )
        self.lines = queue.Queue()
        self.reader = threading.Thread(target=_pump, args=(self.proc.stdout, self.lines), daemon=True)
        self.reader.start()
        self.addCleanup(self.stop)

    def stop(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.reader.join(timeout=10)
        self.proc.stdout.close()

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def next_line(self):
        try:
            return self.lines.get(timeout=60)
        except queue.Empty:
            self.fail("binder-mcp sent nothing for 60 seconds")

    def next_frame(self):
        line = self.next_line()
        if line is None:
            self.fail("binder-mcp closed stdout before replying")
        # Any non-JSON line on stdout fails here: stdout must carry only protocol frames.
        frame = json.loads(line)
        self.assertEqual(frame.get("jsonrpc"), "2.0", line)
        return frame

    def test_stdout_carries_only_json_rpc_frames(self):
        # Wait for each reply before sending the next request: closing stdin early
        # makes the server cancel requests it has not answered yet.
        self.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"}}})
        initialized = self.next_frame()
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = self.next_frame()

        self.proc.stdin.close()
        trailing = []
        while (line := self.next_line()) is not None:
            trailing.append(line)
        self.proc.wait(timeout=60)

        self.assertEqual(initialized["id"], 1)
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "binder")
        self.assertEqual(tools["id"], 2)
        self.assertEqual({tool["name"] for tool in tools["result"]["tools"]}, TOOL_NAMES)
        for line in trailing:
            self.assertEqual(json.loads(line).get("jsonrpc"), "2.0", line)


class BinderConfigTests(unittest.TestCase):
    def test_rejects_base_urls_without_http_scheme_and_host(self):
        for url in ("localhost:8000", "ftp://127.0.0.1:8000", "http://", ""):
            with self.subTest(url=url):
                with self.assertRaises(BinderConfigError) as raised:
                    BinderClient(url)

                self.assertIn("BINDER_URL", str(raised.exception))
                self.assertIn("http://127.0.0.1:8000", str(raised.exception))

    def test_accepts_http_and_https_and_drops_trailing_slash(self):
        self.assertEqual(BinderClient("http://127.0.0.1:8000/").base_url, "http://127.0.0.1:8000")
        self.assertEqual(BinderClient("https://binder.example").base_url, "https://binder.example")

    def test_main_fails_at_startup_with_message_on_stderr(self):
        with (
            patch.dict(os.environ, {"BINDER_URL": "localhost:8000"}),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
            self.assertRaises(SystemExit) as exited,
        ):
            main()

        self.assertEqual(exited.exception.code, 2)
        self.assertIn("BINDER_URL", stderr.getvalue())
        self.assertIn("http://127.0.0.1:8000", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
