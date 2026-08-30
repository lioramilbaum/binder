import io
import json
import tempfile
import unittest
from pathlib import Path

from src.binder.folders import Folders


class FoldersTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Folders(Path(self.temp_dir.name) / "folders.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_empty_store(self):
        self.assertEqual(self.store.list(), [])

    def test_create_folder_persists_hebrew(self):
        created = self.store.create("ועד הבית", "מסמכים והתכתבויות")

        self.assertEqual(created["name"], "ועד הבית")
        self.assertEqual(created["documents"], [])
        self.assertEqual(self.store.list()[0], created)
        raw_data = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(raw_data[0]["description"], "מסמכים והתכתבויות")

    def test_newest_folder_is_first(self):
        first = self.store.create("ראשונה")
        second = self.store.create("שנייה")

        self.assertEqual([folder["id"] for folder in self.store.list()], [second["id"], first["id"]])

    def test_get_folder_with_documents(self):
        created = self.store.create("ועד הבית")

        self.assertEqual(self.store.get(created["id"]), created)

    def test_get_existing_folder_without_documents_adds_empty_list(self):
        created = self.store.create("תיקייה ישנה")
        stored = self.store.list()
        stored[0].pop("documents")
        self.store.path.write_text(json.dumps(stored), encoding="utf-8")

        self.assertEqual(self.store.get(created["id"])["documents"], [])

    def test_get_missing_folder(self):
        self.assertIsNone(self.store.get("missing"))

    def test_create_document(self):
        folder = self.store.create("ועד הבית")

        document = self.store.create_document(
            folder["id"],
            "2026-08-30",
            "שיחת WhatsApp",
            "שיחה עם נציג הוועד",
            "דני: שלום\nאני: היי",
            "14:35",
        )

        self.assertEqual(document["name"], "שיחת WhatsApp")
        self.assertEqual(document["date"], "2026-08-30")
        self.assertEqual(document["time"], "14:35")
        self.assertEqual(document["content"], "דני: שלום\nאני: היי")
        self.assertEqual(self.store.get(folder["id"])["documents"], [document])
        document_path = (
            self.store.path.parent
            / "documents"
            / folder["id"]
            / f"{document['id']}.json"
        )
        self.assertTrue(document_path.exists())
        self.assertEqual(
            json.loads(document_path.read_text(encoding="utf-8")), document
        )
        folder_data = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertNotIn("content", folder_data[0]["documents"][0])

    def test_create_document_in_missing_folder(self):
        self.assertIsNone(
            self.store.create_document("missing", "2026-08-30", "מסמך")
        )

    def test_reads_legacy_inline_document(self):
        folder = self.store.create("תיקייה ישנה")
        stored = self.store.list()
        legacy_document = {
            "id": "legacy",
            "date": "2025-01-01",
            "name": "מסמך ישן",
            "description": "",
            "content": "תוכן ישן",
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
        }
        stored[0]["documents"] = [legacy_document]
        self.store.path.write_text(json.dumps(stored), encoding="utf-8")

        self.assertEqual(self.store.get(folder["id"])["documents"], [legacy_document])

    def test_add_and_read_document_files(self):
        folder = self.store.create("תמונות")
        document = self.store.create_document(folder["id"], "2026-08-30", "תיעוד")

        files = self.store.add_files(
            folder["id"],
            document["id"],
            [("צילום.jpg", "image/jpeg", b"image bytes")],
        )

        self.assertEqual(files[0]["name"], "צילום.jpg")
        self.assertEqual(files[0]["content_type"], "image/jpeg")
        self.assertEqual(files[0]["size"], 11)
        stored_document = self.store.get(folder["id"])["documents"][0]
        self.assertEqual(stored_document["files"], files)
        path, metadata = self.store.get_file(
            folder["id"], document["id"], files[0]["id"]
        )
        self.assertEqual(path.read_bytes(), b"image bytes")
        self.assertEqual(metadata, files[0])

    def test_add_file_from_stream(self):
        folder = self.store.create("וידאו")
        document = self.store.create_document(folder["id"], "2026-08-30", "סרטון")
        content = b"mov data" * 1024

        files = self.store.add_files(
            folder["id"],
            document["id"],
            [("recording.mov", "video/quicktime", io.BytesIO(content))],
        )

        path, metadata = self.store.get_file(
            folder["id"], document["id"], files[0]["id"]
        )
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(metadata["content_type"], "video/quicktime")
        self.assertEqual(metadata["size"], len(content))

    def test_update_document_content(self):
        folder = self.store.create("התכתבויות")
        document = self.store.create_document(
            folder["id"], "2026-08-30", "שיחה", content="הודעה ראשונה"
        )

        updated = self.store.update_document_content(
            folder["id"], document["id"], "הודעה ראשונה\nהודעה נוספת"
        )

        self.assertEqual(updated["content"], "הודעה ראשונה\nהודעה נוספת")
        saved = self.store.get(folder["id"])["documents"][0]
        self.assertEqual(saved["content"], "הודעה ראשונה\nהודעה נוספת")

    def test_update_missing_document(self):
        folder = self.store.create("התכתבויות")

        self.assertIsNone(
            self.store.update_document_content(folder["id"], "missing", "תוכן")
        )

    def test_imports_whatsapp_chat_from_attachment(self):
        folder = self.store.create("שיחות")
        document = self.store.create_document(folder["id"], "2026-08-30", "WhatsApp")
        chat = "30/08/2026, 14:35 - דנה: אפשר לדבר?".encode()

        self.store.add_files(
            folder["id"], document["id"], [("chat.txt", "text/plain", chat)]
        )

        saved = self.store.get(folder["id"])["documents"][0]
        self.assertEqual(saved["whatsapp_chat"]["messages"][0]["sender"], "דנה")



if __name__ == "__main__":
    unittest.main()
