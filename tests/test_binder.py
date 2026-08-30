import io
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


if __name__ == "__main__":
    unittest.main()
