import io
import json
import unittest
from http import HTTPStatus
from unittest.mock import Mock, patch

from binder.ask import AskDisabled
from binder.binder import Binder
from binder.embeddings import ChatUnavailable

_DEFAULT_REMOVE_RESULT = {"id": "document"}


class BinderConfigTests(unittest.TestCase):
    def get_config(self):
        handler = object.__new__(Binder)
        handler.path = "/api/config"
        handler._send_json = Mock()
        handler.do_GET()
        return handler

    def test_returns_empty_google_config_by_default(self):
        with patch("binder.binder.GOOGLE_CLIENT_ID", ""), patch(
            "binder.binder.GOOGLE_API_KEY", ""
        ), patch("binder.binder.GOOGLE_DRIVE_IMPORT_ENABLED", False):
            handler = self.get_config()
        handler._send_json.assert_called_once_with(
            {"googleClientId": "", "googleApiKey": ""}
        )

    def test_hides_credentials_when_toggle_is_disabled(self):
        with patch("binder.binder.GOOGLE_CLIENT_ID", "client-id"), patch(
            "binder.binder.GOOGLE_API_KEY", "api-key"
        ), patch("binder.binder.GOOGLE_DRIVE_IMPORT_ENABLED", False):
            handler = self.get_config()
        handler._send_json.assert_called_once_with(
            {"googleClientId": "", "googleApiKey": ""}
        )

    def test_returns_configured_google_credentials_when_toggle_is_enabled(self):
        with patch("binder.binder.GOOGLE_CLIENT_ID", "client-id"), patch(
            "binder.binder.GOOGLE_API_KEY", "api-key"
        ), patch("binder.binder.GOOGLE_DRIVE_IMPORT_ENABLED", True):
            handler = self.get_config()
        handler._send_json.assert_called_once_with(
            {"googleClientId": "client-id", "googleApiKey": "api-key"}
        )


class BinderUploadTests(unittest.TestCase):
    def test_parses_mov_upload_in_bounded_chunks(self):
        boundary = "binder-test-boundary"
        content = b"video-data" * 200_000
        body = (
            f'--{boundary}\r\n'
            'Content-Disposition: form-data; name="files"; filename="clip.mov"\r\n'
            'Content-Type: video/quicktime\r\n\r\n'
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        handler = object.__new__(Binder)
        handler.rfile = io.BytesIO(body)

        uploads = handler._read_multipart_uploads(
            f"multipart/form-data; boundary={boundary}", len(body)
        )

        self.assertEqual(len(uploads), 1)
        filename, content_type, stream = uploads[0]
        try:
            self.assertEqual(filename, "clip.mov")
            self.assertEqual(content_type, "video/quicktime")
            self.assertEqual(stream.read(), content)
        finally:
            stream.close()

    def test_rejects_multipart_without_boundary(self):
        handler = object.__new__(Binder)
        handler.rfile = io.BytesIO(b"invalid")

        uploads = handler._read_multipart_uploads("multipart/form-data", 7)

        self.assertEqual(uploads, [])


class BinderEditTests(unittest.TestCase):
    def patch(self, payload):
        handler = object.__new__(Binder)
        handler.path = "/api/folders/folder/documents/document"
        body = json.dumps(payload).encode()
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.store = Mock()
        handler.store.update_document.return_value = {"id": "document"}
        handler._send_json = Mock()
        handler.do_PATCH()
        return handler

    def test_edit_all_fields(self):
        payload = {
            "name": " Updated ", "date": "2026-09-12", "time": "10:30",
            "description": "Description", "content": "Content",
            "summary": "Summary",
        }
        handler = self.patch(payload)
        handler.store.update_document.assert_called_once_with(
            "folder", "document", {**payload, "name": "Updated"}
        )
        handler._send_json.assert_called_once_with({"id": "document"})

    def test_rejects_invalid_edits_without_writing(self):
        # Payloads rejected by do_PATCH validation before calling update_document
        for payload in (
            {"name": " "}, {"name": "x" * 201},
            {"description": "x" * 1001}, {"date": ""},
            {"date": "2026-02-30"}, {"time": "25:00"},
            {"content": None}, {"time": False}, {"name": []},
            {}, [], {"id": "replacement"},
        ):
            with self.subTest(payload=payload):
                handler = self.patch(payload)
                handler.store.update_document.assert_not_called()
                self.assertEqual(handler._send_json.call_args.args[1], 400)

    def test_rejects_summary_exceeding_length_in_repository(self):
        # Summary validation now happens in update_document (repository layer)
        from binder.folder_repository import DocumentRuleError
        payload = {"summary": "x" * 1001}
        handler = object.__new__(Binder)
        handler.path = "/api/folders/folder/documents/document"
        body = json.dumps(payload).encode()
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.store = Mock()
        # Mock update_document to raise DocumentRuleError for long summaries
        handler.store.update_document.side_effect = DocumentRuleError(
            "Summary exceeds maximum length of 1000 for regular documents"
        )
        handler._send_json = Mock()
        handler.do_PATCH()
        handler.store.update_document.assert_called_once()
        self.assertEqual(handler._send_json.call_args.args[1], 400)

    def test_optional_fields_can_be_cleared(self):
        payload = {"time": "", "description": "", "content": "", "summary": ""}
        handler = self.patch(payload)
        handler.store.update_document.assert_called_once_with(
            "folder", "document", payload
        )

    def test_edit_tags(self):
        payload = {"tags": [" Budget ", "budget", "תקציב"]}
        handler = self.patch(payload)
        handler.store.update_document.assert_called_once_with(
            "folder", "document", {"tags": ["Budget", "תקציב"]}
        )

    def test_edit_tags_can_be_cleared(self):
        payload = {"tags": []}
        handler = self.patch(payload)
        handler.store.update_document.assert_called_once_with(
            "folder", "document", {"tags": []}
        )

    def test_rejects_invalid_tags_in_edit(self):
        for payload in (
            {"tags": "a"},
            {"tags": [1]},
            {"tags": ["x" * 51]},
            {"tags": [str(i) for i in range(21)]},
        ):
            with self.subTest(payload=payload):
                handler = self.patch(payload)
                handler.store.update_document.assert_not_called()
                self.assertEqual(handler._send_json.call_args.args[1], 400)


class BinderAttachmentTests(unittest.TestCase):
    def test_sends_attachment_bytes_from_the_store(self):
        handler = object.__new__(Binder)
        handler.path = "/api/folders/folder/documents/document/files/file"
        handler.store = Mock()
        handler.store.get_file.return_value = (b"image bytes", "image/jpeg", "photo.jpg")
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = io.BytesIO()

        handler.do_GET()

        handler.store.get_file.assert_called_once_with("folder", "document", "file")
        handler.send_header.assert_any_call("Content-Type", "image/jpeg")
        self.assertEqual(handler.wfile.getvalue(), b"image bytes")

    def test_sends_pdf_inline_with_filename(self):
        handler = object.__new__(Binder)
        handler.path = "/api/folders/folder/documents/document/files/file"
        handler.store = Mock()
        handler.store.get_file.return_value = (b"%PDF-1.7", "application/pdf", "דוח.pdf")
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = io.BytesIO()

        handler.do_GET()

        handler.send_header.assert_any_call("Content-Type", "application/pdf")
        handler.send_header.assert_any_call(
            "Content-Disposition", "inline; filename*=UTF-8''%D7%93%D7%95%D7%97.pdf"
        )
        self.assertEqual(handler.wfile.getvalue(), b"%PDF-1.7")

    def test_missing_attachment_returns_404(self):
        handler = object.__new__(Binder)
        handler.path = "/api/folders/folder/documents/document/files/file"
        handler.store = Mock()
        handler.store.get_file.return_value = None
        handler.send_error = Mock()

        handler.do_GET()

        handler.send_error.assert_called_once()


class BinderDeleteTests(unittest.TestCase):
    def delete(self, path, remove_file_result=_DEFAULT_REMOVE_RESULT):
        handler = object.__new__(Binder)
        handler.path = path
        handler.store = Mock()
        handler.store.remove_file.return_value = remove_file_result
        handler._send_json = Mock()
        handler.send_error = Mock()
        handler.do_DELETE()
        return handler

    def test_deletes_attachment(self):
        handler = self.delete("/api/folders/folder/documents/document/files/file")
        handler.store.remove_file.assert_called_once_with("folder", "document", "file")
        handler._send_json.assert_called_once_with({"id": "document"})

    def test_rejects_path_without_a_file_segment(self):
        handler = self.delete("/api/folders/folder")
        handler.store.remove_file.assert_not_called()
        handler.send_error.assert_called_once()

    def test_missing_file_returns_404(self):
        handler = self.delete(
            "/api/folders/folder/documents/document/files/file",
            remove_file_result=None,
        )
        self.assertEqual(handler._send_json.call_args.args[1], 404)


class BinderAiThreadTests(unittest.TestCase):
    """Tests for the deprecated /ai-threads endpoint - now returns 404."""

    def test_ai_threads_endpoint_removed(self):
        """The old /ai-threads endpoint should return 404."""
        handler = object.__new__(Binder)
        handler.path = "/api/folders/folder/ai-threads"
        payload = {"title": "Title", "conversation_date": "2026-09-28", "transcript": "Text"}
        body = json.dumps(payload).encode()
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.store = Mock()
        with patch.object(handler, 'send_error') as mock_send_error:
            handler.do_POST()
            mock_send_error.assert_called_once_with(HTTPStatus.NOT_FOUND)


class BinderCreateDocumentTests(unittest.TestCase):
    def post(self, path, payload):
        handler = object.__new__(Binder)
        handler.path = path
        body = json.dumps(payload).encode()
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.store = Mock()
        handler.store.create_document.return_value = {"id": "document"}
        handler._send_json = Mock()
        handler.do_POST()
        return handler

    def test_create_document_with_tags(self):
        payload = {
            "date": "2026-09-27",
            "name": "Test",
            "tags": ["Budget", "Urgent"],
        }
        handler = self.post("/api/folders/folder/documents", payload)
        handler.store.create_document.assert_called_once()
        call_kwargs = handler.store.create_document.call_args.kwargs
        self.assertEqual(call_kwargs["tags"], ["Budget", "Urgent"])

    def test_create_document_accepts_empty_tags(self):
        payload = {
            "date": "2026-09-27",
            "name": "Test",
            "tags": [],
        }
        handler = self.post("/api/folders/folder/documents", payload)
        handler.store.create_document.assert_called_once()

    def test_rejects_invalid_tags_in_create(self):
        for payload in (
            {"date": "2026-09-27", "name": "Test", "tags": "not-a-list"},
            {"date": "2026-09-27", "name": "Test", "tags": [1, 2]},
            {"date": "2026-09-27", "name": "Test", "tags": ["x" * 51]},
            {"date": "2026-09-27", "name": "Test", "tags": [str(i) for i in range(21)]},
        ):
            with self.subTest(payload=payload):
                handler = self.post("/api/folders/folder/documents", payload)
                handler.store.create_document.assert_not_called()
                self.assertEqual(handler._send_json.call_args.args[1], 400)


class BinderCreateAiDocumentTests(unittest.TestCase):
    def post(self, path, payload):
        handler = object.__new__(Binder)
        handler.path = path
        body = json.dumps(payload).encode()
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.store = Mock()
        handler.store.create_document.return_value = {"id": "document"}
        handler._send_json = Mock()
        handler.do_POST()
        return handler

    def test_create_valid_ai_document(self):
        """Test creating a valid AI document with content preservation."""
        payload = {
            "type": "ai",
            "name": "Chat",
            "date": "2026-01-01",
            "content": "User: hi\nAssistant: hello",
            "provider": "ChatGPT",
            "summary": "",
        }
        handler = self.post("/api/folders/folder/documents", payload)
        handler.store.create_document.assert_called_once()
        call_args = handler.store.create_document.call_args
        call_kwargs = call_args.kwargs
        # Content is positional arg 4 (after folder_id, date, name, description)
        content_arg = call_args.args[4]
        self.assertEqual(call_kwargs["document_type"], "ai")
        self.assertEqual(call_kwargs["provider"], "ChatGPT")
        # Content should NOT be stripped for AI documents
        self.assertEqual(content_arg, "User: hi\nAssistant: hello")

    def test_create_ai_document_preserves_leading_trailing_whitespace(self):
        """Test that leading/trailing whitespace in content is preserved for AI documents."""
        payload = {
            "type": "ai",
            "name": "Chat",
            "date": "2026-01-01",
            "content": "  User: hi\nAssistant: hello  ",
            "provider": "ChatGPT",
            "summary": "",
        }
        handler = self.post("/api/folders/folder/documents", payload)
        handler.store.create_document.assert_called_once()
        call_args = handler.store.create_document.call_args
        # Content is positional arg 4 (after folder_id, date, name, description)
        content_arg = call_args.args[4]
        # Whitespace should be preserved
        self.assertEqual(content_arg, "  User: hi\nAssistant: hello  ")

    def test_rejects_invalid_document_type(self):
        """Test that invalid document type returns 400."""
        payload = {
            "type": "invalid",
            "name": "Chat",
            "date": "2026-01-01",
            "content": "Text",
        }
        handler = self.post("/api/folders/folder/documents", payload)
        handler.store.create_document.assert_not_called()
        self.assertEqual(handler._send_json.call_args.args[1], 400)

    def test_rejects_ai_document_with_summary_exceeding_10000(self):
        """Test that AI document with summary > 10000 chars returns 400."""
        payload = {
            "type": "ai",
            "name": "Chat",
            "date": "2026-01-01",
            "content": "Text",
            "summary": "x" * 10001,
        }
        handler = self.post("/api/folders/folder/documents", payload)
        handler.store.create_document.assert_not_called()
        self.assertEqual(handler._send_json.call_args.args[1], 400)

    def test_accepts_ai_document_with_summary_of_5000(self):
        """Test that AI document with summary of 5000 chars is accepted."""
        payload = {
            "type": "ai",
            "name": "Chat",
            "date": "2026-01-01",
            "content": "Text",
            "summary": "x" * 5000,
        }
        handler = self.post("/api/folders/folder/documents", payload)
        handler.store.create_document.assert_called_once()
        self.assertEqual(handler._send_json.call_args.args[1], 201)

    def test_rejects_provider_on_regular_document(self):
        """Test that setting provider on a regular document returns 400."""
        payload = {
            "type": "regular",
            "name": "Document",
            "date": "2026-01-01",
            "content": "Text",
            "provider": "ChatGPT",
        }
        handler = self.post("/api/folders/folder/documents", payload)
        handler.store.create_document.assert_not_called()
        self.assertEqual(handler._send_json.call_args.args[1], 400)


class BinderLinkTests(unittest.TestCase):
    def post(self, path, payload=None):
        handler = object.__new__(Binder)
        handler.path = path
        handler.rfile = io.BytesIO(json.dumps(payload or {}).encode())
        handler.headers = {"Content-Length": str(len(json.dumps(payload or {}).encode()))}
        handler.store = Mock()
        handler._send_json = Mock()
        handler.send_error = Mock()
        handler.do_POST()
        return handler

    def delete(self, path):
        handler = object.__new__(Binder)
        handler.path = path
        handler.store = Mock()
        handler._send_json = Mock()
        handler.send_error = Mock()
        handler.do_DELETE()
        return handler

    def test_get_link_types(self):
        handler = object.__new__(Binder)
        handler.path = "/api/link-types"
        handler.store = Mock()
        handler.store.link_types.return_value = [
            {"id": "1", "name": "Reply to"},
            {"id": "2", "name": "Related to"},
        ]
        handler._send_json = Mock()
        handler.do_GET()
        handler.store.link_types.assert_called_once()
        handler._send_json.assert_called_once_with(
            [
                {"id": "1", "name": "Reply to"},
                {"id": "2", "name": "Related to"},
            ]
        )

    def test_post_link_types_returns_201_when_created(self):
        with patch("binder.binder.create_link_type") as mock_create:
            mock_create.return_value = ({"id": "1", "name": "Reply to"}, True)
            handler = self.post("/api/link-types", {"name": "Reply to"})
            self.assertEqual(handler._send_json.call_args.args[1], 201)

    def test_post_link_types_returns_200_when_existing(self):
        with patch("binder.binder.create_link_type") as mock_create:
            mock_create.return_value = ({"id": "1", "name": "Reply to"}, False)
            handler = self.post("/api/link-types", {"name": "Reply to"})
            self.assertEqual(handler._send_json.call_args.args[1], 200)

    def test_post_link_types_rejects_empty_name(self):
        handler = self.post("/api/link-types", {"name": ""})
        self.assertEqual(handler._send_json.call_args.args[1], 400)
        self.assertEqual(handler._send_json.call_args.args[0], {"error": "invalid_link_type"})

    def test_post_link_types_rejects_missing_name(self):
        handler = self.post("/api/link-types", {})
        self.assertEqual(handler._send_json.call_args.args[1], 400)

    def test_post_link_types_rejects_non_string_name(self):
        handler = self.post("/api/link-types", {"name": 123})
        self.assertEqual(handler._send_json.call_args.args[1], 400)

    def test_post_link_types_rejects_too_long_name(self):
        handler = self.post("/api/link-types", {"name": "x" * 51})
        self.assertEqual(handler._send_json.call_args.args[1], 400)

    def test_create_link_calls_store_add_link(self):
        with patch("binder.binder.add_document_link") as mock_add:
            mock_add.return_value = {"id": "doc1"}
            handler = self.post(
                "/api/folders/folder1/documents/doc1/links",
                {"target_document_id": "doc2", "link_type_id": "type1"}
            )
            self.assertEqual(handler._send_json.call_args.args[1], 201)

    def test_create_link_rejects_missing_target(self):
        handler = self.post(
            "/api/folders/folder1/documents/doc1/links",
            {"link_type_id": "type1"}
        )
        self.assertEqual(handler._send_json.call_args.args[1], 400)
        self.assertEqual(handler._send_json.call_args.args[0], {"error": "invalid_request"})

    def test_create_link_rejects_empty_target(self):
        handler = self.post(
            "/api/folders/folder1/documents/doc1/links",
            {"target_document_id": "", "link_type_id": "type1"}
        )
        self.assertEqual(handler._send_json.call_args.args[1], 400)

    def test_create_link_rejects_missing_type(self):
        handler = self.post(
            "/api/folders/folder1/documents/doc1/links",
            {"target_document_id": "doc2"}
        )
        self.assertEqual(handler._send_json.call_args.args[1], 400)

    def test_create_link_returns_404_when_document_not_found(self):
        with patch("binder.binder.add_document_link") as mock_add:
            mock_add.return_value = None
            handler = self.post(
                "/api/folders/folder1/documents/doc1/links",
                {"target_document_id": "doc2", "link_type_id": "type1"}
            )
            self.assertEqual(handler._send_json.call_args.args[1], 404)

    def test_create_link_returns_409_for_duplicate(self):
        from binder.folder_repository import LinkError
        with patch("binder.binder.add_document_link") as mock_add:
            mock_add.side_effect = LinkError("duplicate_link")
            handler = self.post(
                "/api/folders/folder1/documents/doc1/links",
                {"target_document_id": "doc2", "link_type_id": "type1"}
            )
            self.assertEqual(handler._send_json.call_args.args[1], 409)

    def test_create_link_returns_400_for_other_link_errors(self):
        from binder.folder_repository import LinkError
        with patch("binder.binder.add_document_link") as mock_add:
            mock_add.side_effect = LinkError("self_link")
            handler = self.post(
                "/api/folders/folder1/documents/doc1/links",
                {"target_document_id": "doc2", "link_type_id": "type1"}
            )
            self.assertEqual(handler._send_json.call_args.args[1], 400)
            self.assertEqual(handler._send_json.call_args.args[0], {"error": "self_link"})

    def test_delete_link_returns_404_when_not_found(self):
        with patch("binder.binder.remove_document_link") as mock_remove:
            mock_remove.return_value = None
            handler = self.delete("/api/folders/folder1/documents/doc1/links/link1")
            self.assertEqual(handler._send_json.call_args.args[1], 404)

    def test_delete_link_returns_200_on_success(self):
        with patch("binder.binder.remove_document_link") as mock_remove:
            mock_remove.return_value = {"id": "doc1"}
            handler = self.delete("/api/folders/folder1/documents/doc1/links/link1")
            self.assertEqual(handler._send_json.call_args.args[1], 200)

    def test_delete_file_still_works_for_7_part_paths_with_files(self):
        handler = object.__new__(Binder)
        handler.path = "/api/folders/folder1/documents/doc1/files/file1"
        handler.store = Mock()
        handler._send_json = Mock()
        handler.send_error = Mock()
        handler.do_DELETE()
        handler.store.remove_file.assert_called_once_with("folder1", "doc1", "file1")


class BinderTagsTests(unittest.TestCase):
    def test_get_tags_returns_list(self):
        handler = object.__new__(Binder)
        handler.path = "/api/tags"
        handler.store = Mock()
        handler.store.tags.return_value = [
            {"name": "Budget", "document_count": 2},
            {"name": "Urgent", "document_count": 1},
        ]
        handler._send_json = Mock()
        handler.do_GET()
        handler.store.tags.assert_called_once()
        handler._send_json.assert_called_once_with(
            [
                {"name": "Budget", "document_count": 2},
                {"name": "Urgent", "document_count": 1},
            ]
        )


class BinderSearchTests(unittest.TestCase):
    def search(self, path):
        handler = object.__new__(Binder)
        handler.path = path
        handler.store = Mock()
        handler.store.search.return_value = {"query": "lease", "results": [], "semantic": "ok"}
        handler._send_json = Mock()
        handler.do_GET()
        return handler

    def test_valid_request_routes_to_store_search(self):
        handler = self.search("/api/search?q=lease&folder_id=f1&limit=20&mode=keyword")

        handler.store.search.assert_called_once_with("lease", folder_id="f1", limit=20, mode="keyword")
        handler._send_json.assert_called_once_with(handler.store.search.return_value)

    def test_defaults_are_hybrid_mode_fifty_results_and_all_folders(self):
        handler = self.search("/api/search?q=lease")

        handler.store.search.assert_called_once_with("lease", folder_id=None, limit=50, mode="hybrid")

    def test_rejects_invalid_requests_without_searching(self):
        cases = [
            ("/api/search", "invalid_query"),
            ("/api/search?q=%20%20", "invalid_query"),
            ("/api/search?q=" + "a" * 201, "invalid_query"),
            ("/api/search?q=lease&limit=0", "invalid_limit"),
            ("/api/search?q=lease&limit=501", "invalid_limit"),
            ("/api/search?q=lease&limit=abc", "invalid_limit"),
            ("/api/search?q=lease&mode=bogus", "invalid_request"),
        ]
        for path, error in cases:
            with self.subTest(path=path):
                handler = self.search(path)

                handler._send_json.assert_called_once_with(
                    {"error": error}, HTTPStatus.BAD_REQUEST
                )
                handler.store.search.assert_not_called()


class BinderAskTests(unittest.TestCase):
    def post(self, body: bytes, store=None):
        handler = object.__new__(Binder)
        handler.path = "/api/ask"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.store = store if store is not None else Mock()
        handler._send_json = Mock()
        handler.do_POST()
        return handler

    def test_valid_request_routes_to_store_ask(self):
        store = Mock()
        store.ask.return_value = {"status": "ok", "answer": "5000 [1]"}

        handler = self.post(
            json.dumps({"question": "What is the rent?", "folder_id": "f1", "limit": 3}).encode(),
            store=store,
        )

        store.ask.assert_called_once_with("What is the rent?", folder_id="f1", limit=3)
        handler._send_json.assert_called_once_with({"status": "ok", "answer": "5000 [1]"})

    def test_defaults(self):
        store = Mock()
        store.ask.return_value = {"status": "ok"}

        self.post(json.dumps({"question": "What is the rent?"}).encode(), store=store)

        store.ask.assert_called_once_with("What is the rent?", folder_id=None, limit=5)

    def test_rejects_invalid_requests_without_asking(self):
        cases = [
            ({"question": ""}, "invalid_question"),
            ({"question": "   "}, "invalid_question"),
            ({"question": "x" * 1001}, "invalid_question"),
            ({"question": 42}, "invalid_question"),
            ({}, "invalid_question"),
            ({"question": "ok", "limit": 0}, "invalid_limit"),
            ({"question": "ok", "limit": 11}, "invalid_limit"),
            ({"question": "ok", "limit": "3"}, "invalid_limit"),
            ({"question": "ok", "limit": True}, "invalid_limit"),
            ({"question": "ok", "extra": 1}, "invalid_request"),
            ({"question": "ok", "folder_id": ""}, "invalid_request"),
        ]
        for payload, code in cases:
            with self.subTest(payload=payload):
                store = Mock()
                handler = self.post(json.dumps(payload).encode(), store=store)

                handler._send_json.assert_called_once_with({"error": code}, HTTPStatus.BAD_REQUEST)
                store.ask.assert_not_called()

    def test_empty_body_is_invalid_request(self):
        store = Mock()

        handler = self.post(b"", store=store)

        handler._send_json.assert_called_once_with({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
        store.ask.assert_not_called()

    def test_generation_unavailable_returns_503(self):
        store = Mock()
        store.ask.side_effect = ChatUnavailable("down")

        handler = self.post(json.dumps({"question": "What is the rent?"}).encode(), store=store)

        handler._send_json.assert_called_once_with(
            {"error": "generation_unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE
        )

    def test_ask_disabled_returns_503(self):
        store = Mock()
        store.ask.side_effect = AskDisabled("off")

        handler = self.post(json.dumps({"question": "What is the rent?"}).encode(), store=store)

        handler._send_json.assert_called_once_with({"error": "ask_disabled"}, HTTPStatus.SERVICE_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
