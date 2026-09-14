import io
import json
from unittest.mock import Mock
import unittest

from src.binder.binder import Binder


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
        }
        handler = self.patch(payload)
        handler.store.update_document.assert_called_once_with(
            "folder", "document", {**payload, "name": "Updated"}
        )
        handler._send_json.assert_called_once_with({"id": "document"})

    def test_rejects_invalid_edits_without_writing(self):
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

    def test_optional_fields_can_be_cleared(self):
        payload = {"time": "", "description": "", "content": ""}
        handler = self.patch(payload)
        handler.store.update_document.assert_called_once_with(
            "folder", "document", payload
        )


class BinderDeleteTests(unittest.TestCase):
    def delete(self, path, remove_file_result={"id": "document"}):
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


if __name__ == "__main__":
    unittest.main()
