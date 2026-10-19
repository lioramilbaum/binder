import io
import unittest
import zipfile

from binder.text_extraction import MAX_EXTRACT_BYTES, MAX_EXTRACTED_CHARS, extract_text


WORD_DOCUMENT_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    "<w:p><w:r><w:t>First paragraph</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>Second</w:t></w:r><w:r><w:t> paragraph</w:t></w:r></w:p>"
    "</w:body></w:document>"
)


def build_pdf(text: str) -> bytes:
    content = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    return bytes(out)


class ExtractTextTests(unittest.TestCase):
    def test_utf8_hebrew_text_file(self):
        data = "חוזה שכירות לדירה".encode("utf-8")

        text = extract_text("notes.txt", "text/plain", data, len(data))

        self.assertEqual(text, "חוזה שכירות לדירה")

    def test_cp1255_hebrew_text_file(self):
        data = "חוזה שכירות לדירה".encode("cp1255")

        text = extract_text("notes.txt", "text/plain", data, len(data))

        self.assertEqual(text, "חוזה שכירות לדירה")

    def test_nul_bytes_are_stripped(self):
        data = b"ab\x00cd"

        self.assertEqual(extract_text("log.log", "text/plain", data, len(data)), "abcd")

    def test_whitespace_is_collapsed(self):
        data = b"one\n\n   two\tthree  "

        self.assertEqual(extract_text("notes.md", "text/markdown", data, len(data)), "one two three")

    def test_output_is_truncated(self):
        data = b"a" * (MAX_EXTRACTED_CHARS + 500)

        text = extract_text("big.txt", "text/plain", data, len(data))

        self.assertEqual(len(text), MAX_EXTRACTED_CHARS)

    def test_stream_is_rewound_after_extraction(self):
        stream = io.BytesIO(b"searchable words")
        stream.seek(5)

        text = extract_text("notes.txt", "text/plain", stream, 16)

        self.assertEqual(text, "searchable words")
        self.assertEqual(stream.tell(), 0)

    def test_image_returns_empty_string(self):
        data = b"\x89PNG\r\n\x1a\n" + b"0" * 32

        self.assertEqual(extract_text("photo.png", "image/png", data, len(data)), "")

    def test_oversized_file_returns_empty_string_without_reading(self):
        stream = io.BytesIO(b"would be searchable")

        text = extract_text("huge.txt", "text/plain", stream, MAX_EXTRACT_BYTES + 1)

        self.assertEqual(text, "")
        self.assertEqual(stream.tell(), 0)

    def test_corrupt_pdf_returns_empty_string(self):
        data = b"%PDF-1.4 this is not really a pdf"

        self.assertEqual(extract_text("broken.pdf", "application/pdf", data, len(data)), "")

    def test_minimal_pdf_returns_its_text(self):
        data = build_pdf("Hello PDF")

        text = extract_text("letter.pdf", "application/pdf", data, len(data))

        self.assertEqual(text, "Hello PDF")

    def test_whatsapp_zip_returns_chat_text(self):
        chat = "30/08/2026, 14:35 - דנה: אפשר לדבר?\n30/08/2026, 14:36 - אני: כן\n".encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("_chat.txt", chat)
        data = buffer.getvalue()

        text = extract_text("WhatsApp Chat.zip", "application/zip", data, len(data))

        self.assertIn("אפשר לדבר?", text)
        self.assertIn("כן", text)

    def test_docx_returns_paragraph_text(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", WORD_DOCUMENT_XML)
        data = buffer.getvalue()

        text = extract_text("letter.docx", "application/octet-stream", data, len(data))

        self.assertEqual(text, "First paragraph Second paragraph")

    def test_unsupported_type_returns_empty_string(self):
        data = b"PK-binary"

        self.assertEqual(extract_text("archive.rar", "application/x-rar", data, len(data)), "")


if __name__ == "__main__":
    unittest.main()
