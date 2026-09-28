import gzip
import hashlib
import importlib.util
import io
import json
import socket
import threading
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from binder.binder import Binder, _SharePreviewCache
from binder.chatgpt_share import (
    ShareImportError, SharePreview, fetch_share_page, parse_share_page,
    validate_share_url, _public_addresses, _PinnedHTTPSConnection,
)


LINK = "https://chatgpt.com/share/12345678-1234-4234-8234-123456789abc"


def page(entries, *, title="Synthetic conversation"):
    """Synthetic React Router enqueue payload with a small constant pool."""
    data = {"title": title, "linear_conversation": entries}
    pool = [{}, "route", data]
    chunk = json.dumps(json.dumps(pool, ensure_ascii=False), ensure_ascii=False)
    return f'<html><script>window.__reactRouterContext.streamController.enqueue({chunk});</script></html>'.encode()


def message(role, content):
    return {"message": {"author": {"role": role}, "content": content}}


def message_with(role, content, **fields):
    node = message(role, content)
    node["message"].update(fields)
    return node


class ParserTests(unittest.TestCase):
    def test_order_unicode_code_and_media_warning(self):
        entries = [
            message("user", {"content_type": "text", "parts": ["שלום"]}),
            message("assistant", {"content_type": "code", "language": "python", "text": "print('hi')"}),
            message("user", {"content_type": "multimodal_text", "parts": ["See this", {"content_type": "image_asset_pointer", "asset_pointer": "synthetic"}]}),
        ]
        parsed = parse_share_page(LINK, page(entries))
        self.assertEqual(parsed.message_count, 3)
        self.assertEqual(parsed.title, "Synthetic conversation")
        self.assertEqual(parsed.transcript, "User:\nשלום\n\nAssistant:\n```python\nprint('hi')\n```\n\nUser:\nSee this")
        self.assertEqual(parsed.warnings, ("non_text_omitted",))

    def test_constant_pool_back_reference(self):
        entries = [message("user", {"content_type": "text", "parts": ["Referenced"]})]
        pool = [{}, "route", 4, "other", {"_6": 5, "title": "Reference"}, entries, "linear_conversation"]
        chunk = json.dumps(json.dumps(pool))
        parsed = parse_share_page(LINK, f'<script>streamController.enqueue({chunk});</script>'.encode())
        self.assertEqual(parsed.transcript, "User:\nReferenced")

    def test_complete_conversation_wins_over_flattened_pool_root(self):
        entries = [message("user", {"content_type": "text", "parts": ["Visible"]})]
        complete = {"title": "Complete", "mapping": {}, "linear_conversation": entries}
        pool = [{}, "route", complete, "linear_conversation", entries]
        chunk = json.dumps(json.dumps(pool))
        parsed = parse_share_page(LINK, f'<script>streamController.enqueue({chunk});</script>'.encode())
        self.assertEqual(parsed.title, "Complete")
        self.assertEqual(parsed.transcript, "User:\nVisible")

    def test_visible_multimodal_text_lists_preserve_order(self):
        entries = [message_with("user", {"content_type": "multimodal_text", "parts": [
            {"content_type": "text", "text": ["first", "שני"]},
            "third", {"content_type": "text", "text": ["fourth"]},
        ]}, recipient="")]
        parsed = parse_share_page(LINK, page(entries))
        self.assertEqual(parsed.transcript, "User:\nfirst\n\nשני\n\nthird\n\nfourth")
        self.assertEqual(parsed.warnings, ())

    def test_multimodal_non_string_and_internal_segments_are_flagged(self):
        entries = [message("assistant", {"content_type": "multimodal_text", "parts": [
            {"content_type": "text", "text": ["visible", {"invalid": "item"}]},
            {"content_type": "thoughts", "text": "private"},
        ]})]
        parsed = parse_share_page(LINK, page(entries))
        self.assertEqual(parsed.transcript, "Assistant:\nvisible")
        self.assertEqual(parsed.warnings, ("non_visible_omitted", "non_text_omitted"))

    def test_empty_recipient_is_visible_but_tool_recipient_is_not(self):
        entries = [
            message_with("user", {"content_type": "text", "parts": ["visible"]}, recipient=""),
            message_with("assistant", {"content_type": "text", "parts": ["tool only"]}, recipient="web.run"),
        ]
        parsed = parse_share_page(LINK, page(entries))
        self.assertEqual(parsed.transcript, "User:\nvisible")
        self.assertEqual(parsed.warnings, ("non_visible_omitted",))

    def test_system_and_tool_roles_are_excluded_with_warning(self):
        visible = message("user", {"content_type": "text", "parts": ["Visible"]})
        for role in ("system", "tool"):
            with self.subTest(role=role):
                internal = message(role, {"content_type": "text", "parts": ["Not visible"]})
                parsed = parse_share_page(LINK, page([visible, internal]))
                self.assertEqual(parsed.transcript, "User:\nVisible")
                self.assertEqual(parsed.warnings, ("non_visible_omitted",))

    def test_hidden_tool_directed_and_internal_assistant_content_is_excluded(self):
        visible = message("assistant", {"content_type": "text", "parts": ["Visible answer"]})
        excluded = [
            message_with("assistant", {"content_type": "text", "parts": ["Hidden context"]},
                         metadata={"is_visually_hidden_from_conversation": True}),
            message_with("assistant", {"content_type": "text", "parts": ["Tool call text"]},
                         recipient="web.run"),
            message_with("assistant", {"content_type": "code", "language": "python", "text": "secret()"},
                         recipient="container.exec"),
            message("assistant", {"content_type": "thoughts", "text": "Private reasoning"}),
            message("assistant", {"content_type": "reasoning_recap", "text": "Internal recap"}),
            message("assistant", {"content_type": "model_editable_context", "parts": ["System context"]}),
            message("assistant", {"content_type": "tool_response", "text": "Tool output"}),
            message("assistant", {"content_type": "execution_output", "text": "Execution output"}),
            message("assistant", {"content_type": "multimodal_text", "parts": [
                "Visible prefix", {"content_type": "thoughts", "text": "Nested private text"},
            ]}),
        ]
        for hidden in excluded:
            with self.subTest(hidden=hidden):
                parsed = parse_share_page(LINK, page([visible, hidden]))
                self.assertEqual(parsed.transcript, "Assistant:\nVisible answer" if hidden is not excluded[-1] else "Assistant:\nVisible answer\n\nAssistant:\nVisible prefix")
                self.assertIn("non_visible_omitted", parsed.warnings)

    def test_fails_closed_when_only_non_visible_messages_remain(self):
        hidden = message_with("assistant", {"content_type": "text", "parts": ["Not displayed"]},
                              metadata={"is_visually_hidden_from_conversation": True})
        tool = message_with("assistant", {"content_type": "code", "text": "hidden()"},
                            recipient="container.exec")
        with self.assertRaises(ShareImportError) as error:
            parse_share_page(LINK, page([hidden, tool]))
        self.assertEqual(error.exception.code, "unsupported_format")

    def test_missing_changed_ambiguous_or_incomplete_payload_fails(self):
        cases = [
            b"<html></html>",
            page([]),
            page([{"id": "missing"}]),
            page([message("user", {"content_type": "text"})]),
            page([message("user", {"content_type": "text", "parts": ["yes"]})])
            + page([message("assistant", {"content_type": "text", "parts": ["no"]})]),
        ]
        for html in cases:
            with self.subTest(html=html[:40]), self.assertRaises(ShareImportError):
                parse_share_page(LINK, html)


class FetchTests(unittest.TestCase):
    def test_strict_share_link(self):
        self.assertEqual(validate_share_url(LINK + "/"), LINK)
        for url in [
            LINK.replace("https", "http"), LINK + "?x=1", LINK + "#x",
            LINK.replace("chatgpt.com", "chatgpt.com.evil"),
            LINK.replace("chatgpt.com", "chatgpt.com:443"),
            LINK.replace("https://", "https://user@"),
            LINK.replace("chatgpt.com", "chatgpt.com\t"),
            LINK + "/other", "https://chatgpt.com/c/12345678-1234-4234-8234-123456789abc",
        ]:
            with self.subTest(url=url), self.assertRaises(ShareImportError):
                validate_share_url(url)

    def test_all_dns_answers_must_be_public(self):
        for private in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "192.0.2.1", "::1"):
            with self.subTest(private=private):
                with patch("binder.chatgpt_share.socket.getaddrinfo", return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (private, 443)),
                ]):
                    with self.assertRaises(ShareImportError):
                        _public_addresses("chatgpt.com")

    def test_redirect_revalidated_and_connection_pinned(self):
        class Response:
            status = 302
            def getheader(self, name, default=None):
                return "http://127.0.0.1/private" if name == "Location" else default

        connection = Mock()
        connection.getresponse.return_value = Response()
        with patch("binder.chatgpt_share._public_addresses", return_value=["8.8.8.8"]), patch(
            "binder.chatgpt_share._PinnedHTTPSConnection", return_value=connection
        ) as factory:
            with self.assertRaises(ShareImportError) as error:
                fetch_share_page(LINK)
        self.assertEqual(error.exception.code, "invalid_link")
        self.assertEqual(factory.call_args.args[:2], ("chatgpt.com", "8.8.8.8"))

    def test_pinned_socket_uses_validated_ip_and_tls_hostname(self):
        raw = Mock()
        with patch("binder.chatgpt_share.ssl.create_default_context") as context, patch(
            "binder.chatgpt_share.socket.create_connection", return_value=raw
        ) as connect:
            connection = _PinnedHTTPSConnection("chatgpt.com", "8.8.8.8")
            connection.connect()
        connect.assert_called_once_with(("8.8.8.8", 443), 5)
        context.return_value.wrap_socket.assert_called_once_with(raw, server_hostname="chatgpt.com")

    def test_connection_uses_remaining_total_budget(self):
        raw = Mock()
        deadline = Mock()
        deadline.remaining.return_value = 2
        with patch("binder.chatgpt_share.ssl.create_default_context") as context, patch(
            "binder.chatgpt_share.socket.create_connection", return_value=raw
        ) as connect:
            connection = _PinnedHTTPSConnection("chatgpt.com", "8.8.8.8", deadline)
            connection.connect()
        connect.assert_called_once_with(("8.8.8.8", 443), 2)
        context.return_value.wrap_socket.assert_called_once_with(raw, server_hostname="chatgpt.com")

    def test_dns_lookup_stops_waiting_at_total_deadline(self):
        release = threading.Event()
        def slow_lookup(*args, **kwargs):
            release.wait(1)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        try:
            with patch("binder.chatgpt_share.TOTAL_TIMEOUT", 0.01), patch(
                "binder.chatgpt_share.socket.getaddrinfo", side_effect=slow_lookup
            ):
                with self.assertRaises(ShareImportError) as error:
                    fetch_share_page(LINK)
            self.assertEqual(error.exception.code, "inaccessible_link")
        finally:
            release.set()

    def test_slow_drip_body_exhausts_total_deadline(self):
        state = SimpleNamespace(now=0, reads=0)
        response = Mock()
        response.status = 200
        response.getheader.side_effect = lambda name, default=None: "text/html" if name == "Content-Type" else default
        def read(_):
            state.now += 4  # Each read is below the five-second socket timeout.
            state.reads += 1
            return b"x"
        response.read.side_effect = read
        connection = Mock()
        connection.getresponse.return_value = response
        with patch("binder.chatgpt_share.clock", SimpleNamespace(monotonic=lambda: state.now)), patch(
            "binder.chatgpt_share._public_addresses", return_value=["8.8.8.8"]
        ), patch("binder.chatgpt_share._PinnedHTTPSConnection", return_value=connection):
            with self.assertRaises(ShareImportError) as error:
                fetch_share_page(LINK)
        self.assertEqual(error.exception.code, "inaccessible_link")
        self.assertEqual(state.reads, 3)

    def test_redirects_share_one_total_deadline(self):
        state = SimpleNamespace(now=0)
        response = Mock()
        response.status = 302
        response.getheader.return_value = LINK
        connection = Mock()
        def headers():
            state.now += 4  # Each redirect stays below the socket timeout.
            return response
        connection.getresponse.side_effect = headers
        with patch("binder.chatgpt_share.clock", SimpleNamespace(monotonic=lambda: state.now)), patch(
            "binder.chatgpt_share._public_addresses", return_value=["8.8.8.8"]
        ), patch("binder.chatgpt_share._PinnedHTTPSConnection", return_value=connection):
            with self.assertRaises(ShareImportError) as error:
                fetch_share_page(LINK)
        self.assertEqual(error.exception.code, "inaccessible_link")
        self.assertEqual(connection.getresponse.call_count, 3)

    def test_deadline_aborts_a_blocked_body_read(self):
        aborted = threading.Event()
        response = Mock()
        response.status = 200
        response.getheader.side_effect = lambda name, default=None: "text/html" if name == "Content-Type" else default
        def blocked_read(_):
            if not aborted.wait(1):
                raise AssertionError("deadline did not abort the read")
            raise OSError("socket closed")
        response.read.side_effect = blocked_read
        connection = Mock()
        connection.getresponse.return_value = response
        connection.abort.side_effect = aborted.set
        with patch("binder.chatgpt_share.TOTAL_TIMEOUT", 0.05), patch(
            "binder.chatgpt_share._public_addresses", return_value=["8.8.8.8"]
        ), patch("binder.chatgpt_share._PinnedHTTPSConnection", return_value=connection):
            with self.assertRaises(ShareImportError) as error:
                fetch_share_page(LINK)
        self.assertEqual(error.exception.code, "inaccessible_link")
        self.assertTrue(aborted.is_set())

    def test_redirect_resolves_and_validates_again(self):
        response = Mock()
        response.status = 302
        response.getheader.return_value = LINK
        connection = Mock()
        connection.getresponse.return_value = response
        with patch("binder.chatgpt_share._public_addresses", side_effect=[
            ["8.8.8.8"], ShareImportError("inaccessible_link")
        ]) as resolver, patch("binder.chatgpt_share._PinnedHTTPSConnection", return_value=connection):
            with self.assertRaises(ShareImportError) as error:
                fetch_share_page(LINK)
        self.assertEqual(error.exception.code, "inaccessible_link")
        self.assertEqual(resolver.call_count, 2)

    def test_fetch_falls_back_to_another_validated_address(self):
        failed = Mock()
        failed.request.side_effect = OSError("unreachable address")
        response = Mock()
        response.status = 200
        response.getheader.side_effect = lambda name, default=None: "text/html" if name == "Content-Type" else default
        response.read.side_effect = [b"<html>safe</html>", b""]
        succeeded = Mock()
        succeeded.getresponse.return_value = response
        with patch("binder.chatgpt_share._public_addresses", return_value=["2606:4700::1111", "8.8.8.8"]), patch(
            "binder.chatgpt_share._PinnedHTTPSConnection", side_effect=[failed, succeeded]
        ) as factory:
            source, body = fetch_share_page(LINK)
        self.assertEqual((source, body), (LINK, b"<html>safe</html>"))
        self.assertEqual([call.args[1] for call in factory.call_args_list], ["8.8.8.8", "2606:4700::1111"])

    def test_timeout_non_html_and_oversize(self):
        class Response:
            def __init__(self, status=200, content_type="text/html", body=b"x"):
                self.status, self.content_type, self.body = status, content_type, body
                self.done = False
            def getheader(self, name, default=None):
                return self.content_type if name == "Content-Type" else default
            def read(self, count):
                if self.done:
                    return b""
                self.done = True
                return self.body

        for response, expected in [
            (Response(content_type="application/json"), "unsupported_format"),
            (Response(body=b"x" * 4_000_001), "too_large"),
        ]:
            connection = Mock()
            connection.getresponse.return_value = response
            with patch("binder.chatgpt_share._public_addresses", return_value=["8.8.8.8"]), patch(
                "binder.chatgpt_share._PinnedHTTPSConnection", return_value=connection
            ):
                with self.assertRaises(ShareImportError) as error:
                    fetch_share_page(LINK)
                self.assertEqual(error.exception.code, expected)
        connection = Mock()
        connection.getresponse.side_effect = TimeoutError
        with patch("binder.chatgpt_share._public_addresses", return_value=["8.8.8.8"]), patch(
            "binder.chatgpt_share._PinnedHTTPSConnection", return_value=connection
        ):
            with self.assertRaises(ShareImportError) as error:
                fetch_share_page(LINK)
            self.assertEqual(error.exception.code, "inaccessible_link")

    def test_decompressed_size_is_bounded(self):
        for encoding, compress in (("gzip", gzip.compress), ("deflate", zlib.compress)):
            with self.subTest(encoding=encoding):
                with self.assertRaises(ShareImportError) as error:
                    self.fetch_encoded(encoding, compress(b"x" * 8_000_001))
                self.assertEqual(error.exception.code, "too_large")

    def test_compressed_stream_must_have_exactly_one_complete_member(self):
        html = b"<html>safe</html>"
        for encoding, compress in (("gzip", gzip.compress), ("deflate", zlib.compress)):
            with self.subTest(encoding=encoding):
                self.assertEqual(self.fetch_encoded(encoding, compress(html))[1], html)
                with self.assertRaises(ShareImportError) as trailing:
                    self.fetch_encoded(encoding, compress(html) + b"trailing")
                self.assertEqual(trailing.exception.code, "unsupported_format")
        with self.assertRaises(ShareImportError) as concatenated:
            self.fetch_encoded("gzip", gzip.compress(html) + gzip.compress(b"second member"))
        self.assertEqual(concatenated.exception.code, "unsupported_format")

    def fetch_encoded(self, encoding, compressed):
        response = Mock()
        response.status = 200
        response.getheader.side_effect = lambda name, default=None: {
            "Content-Type": "text/html", "Content-Encoding": encoding,
        }.get(name, default)
        response.read.side_effect = [compressed, b""]
        connection = Mock()
        connection.getresponse.return_value = response
        with patch("binder.chatgpt_share._public_addresses", return_value=["8.8.8.8"]), patch(
            "binder.chatgpt_share._PinnedHTTPSConnection", return_value=connection
        ):
            return fetch_share_page(LINK)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.preview = SharePreview(LINK, "Test", "User:\nhello\n\nAssistant:\nשלום", 2, ())
        self.cache = _SharePreviewCache()

    def post(self, suffix, payload, folder="folder", exists=True):
        handler = object.__new__(Binder)
        handler.path = f"/api/folders/{folder}/ai-threads/{suffix}"
        data = json.dumps(payload).encode()
        handler.headers = {"Content-Length": str(len(data))}
        handler.rfile = io.BytesIO(data)
        handler.store = Mock()
        handler.store.get.return_value = {"id": folder} if exists else None
        handler.store.create_ai_thread.return_value = {"id": "saved"}
        handler.share_previews = self.cache
        handler._send_json = Mock()
        handler.do_POST()
        return handler

    def test_preview_then_exact_one_time_confirmation(self):
        with patch("binder.binder.import_share_url", return_value=self.preview) as importer:
            preview_handler = self.post("share-preview", {"url": LINK})
        importer.assert_called_once_with(LINK)
        preview_handler.store.create_ai_thread.assert_not_called()
        preview_id = preview_handler._send_json.call_args.args[0]["preview_id"]
        confirm = self.post("share-confirm", {
            "preview_id": preview_id, "conversation_date": "2026-09-28", "summary": "My note",
        })
        confirm.store.create_ai_thread.assert_called_once_with(
            "folder", "Test", "ChatGPT", "2026-09-28", self.preview.transcript, "My note",
            source_url=LINK,
            transcript_sha256=hashlib.sha256(self.preview.transcript.encode()).hexdigest(),
        )
        self.assertEqual(confirm._send_json.call_args.args[1], 201)
        again = self.post("share-confirm", {"preview_id": preview_id, "conversation_date": "2026-09-28"})
        self.assertEqual(again._send_json.call_args.args[1], 410)
        again.store.create_ai_thread.assert_not_called()

    def test_wrong_folder_expired_and_missing_folder(self):
        token = self.cache.put("folder", self.preview)
        wrong = self.post("share-confirm", {"preview_id": token, "conversation_date": "2026-09-28"}, folder="other")
        self.assertEqual(wrong._send_json.call_args.args[1], 410)
        with patch("binder.binder.clock.monotonic", return_value=float("inf")):
            expired = self.post("share-confirm", {"preview_id": token, "conversation_date": "2026-09-28"})
        self.assertEqual(expired._send_json.call_args.args[1], 410)
        with patch("binder.binder.import_share_url") as importer:
            handler = self.post("share-preview", {"url": LINK}, exists=False)
        importer.assert_not_called()
        self.assertEqual(handler._send_json.call_args.args[1], 404)


class MigrationTests(unittest.TestCase):
    def test_existing_ai_threads_gain_nullable_import_columns(self):
        path = Path(__file__).resolve().parents[1] / "migrations/versions/20260928_06_add_ai_thread_import_source.py"
        spec = importlib.util.spec_from_file_location("ai_thread_import_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE ai_threads (id TEXT PRIMARY KEY, title TEXT NOT NULL)"))
            connection.execute(text("INSERT INTO ai_threads (id, title) VALUES ('old', 'Legacy')"))
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(migration, "op", operations):
                migration.upgrade()
            row = connection.execute(text("SELECT title, source_url, imported_at, transcript_sha256 FROM ai_threads WHERE id = 'old'")).one()
        self.assertEqual(row, ("Legacy", None, None, None))
        columns = {column["name"] for column in inspect(engine).get_columns("ai_threads")}
        self.assertTrue({"source_url", "imported_at", "transcript_sha256"} <= columns)
