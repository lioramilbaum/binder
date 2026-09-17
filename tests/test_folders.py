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
            summary="צריך לבדוק תקציב",
        )

        self.assertEqual(document["name"], "שיחת WhatsApp")
        self.assertEqual(document["date"], "2026-08-30")
        self.assertEqual(document["time"], "14:35")
        self.assertEqual(document["content"], "דני: שלום\nאני: היי")
        self.assertEqual(document["summary"], "צריך לבדוק תקציב")
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

    def test_documents_are_sorted_by_date_newest_first(self):
        folder = self.store.create("מסמכים")
        first = self.store.create_document(folder["id"], "2026-08-30", "ראשון")
        second = self.store.create_document(folder["id"], "2026-01-01", "שני")

        documents = self.store.get(folder["id"])["documents"]

        self.assertEqual(
            [document["id"] for document in documents],
            [first["id"], second["id"]],
        )

    def test_documents_on_the_same_date_are_sorted_by_time_newest_first(self):
        folder = self.store.create("מסמכים")
        early = self.store.create_document(
            folder["id"], "2026-08-30", "מוקדם", time="09:00"
        )
        late = self.store.create_document(
            folder["id"], "2026-08-30", "מאוחר", time="17:30"
        )

        documents = self.store.get(folder["id"])["documents"]

        self.assertEqual(
            [document["id"] for document in documents],
            [late["id"], early["id"]],
        )

    def test_document_sort_tolerates_missing_legacy_creation_date(self):
        folder = self.store.create("תיקייה ישנה")
        stored = self.store.list()
        stored[0]["documents"] = [
            {"id": "legacy", "date": "2025-01-01", "name": "ישן", "content": ""},
            {
                "id": "new",
                "date": "2026-01-01",
                "name": "חדש",
                "content": "",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        ]
        self.store.path.write_text(json.dumps(stored), encoding="utf-8")

        documents = self.store.get(folder["id"])["documents"]

        self.assertEqual([document["id"] for document in documents], ["new", "legacy"])

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

        updated = self.store.update_document(
            folder["id"], document["id"], {"content": "הודעה ראשונה\nהודעה נוספת"}
        )

        self.assertEqual(updated["content"], "הודעה ראשונה\nהודעה נוספת")
        saved = self.store.get(folder["id"])["documents"][0]
        self.assertEqual(saved["content"], "הודעה ראשונה\nהודעה נוספת")

    def test_update_missing_document(self):
        folder = self.store.create("התכתבויות")

        self.assertIsNone(
            self.store.update_document(folder["id"], "missing", {"content": "תוכן"})
        )

    def test_update_all_document_fields_preserves_files_and_identity(self):
        folder = self.store.create("מסמכים")
        document = self.store.create_document(folder["id"], "2026-08-30", "ישן")
        files = self.store.add_files(
            folder["id"], document["id"], [("note.txt", "text/plain", b"note")]
        )
        updates = {
            "name": "חדש", "date": "2026-09-12", "time": "12:30",
            "description": "תיאור חדש", "content": "תוכן חדש",
            "summary": "סיכום מחשבות חדש",
        }
        updated = self.store.update_document(folder["id"], document["id"], updates)
        reloaded = Folders(self.store.path).get(folder["id"])["documents"][0]
        self.assertEqual(updated, reloaded)
        for key, value in updates.items():
            self.assertEqual(reloaded[key], value)
        self.assertEqual(reloaded["id"], document["id"])
        self.assertEqual(reloaded["created_at"], document["created_at"])
        self.assertEqual(reloaded["files"], files)
        self.assertGreater(reloaded["updated_at"], document["updated_at"])
        cleared = self.store.update_document(
            folder["id"],
            document["id"],
            {"time": "", "description": "", "content": "", "summary": ""},
        )
        self.assertEqual(cleared["name"], updates["name"])
        for key in ("time", "description", "content", "summary"):
            self.assertEqual(cleared[key], "")

    def test_imports_whatsapp_chat_from_attachment(self):
        folder = self.store.create("שיחות")
        document = self.store.create_document(folder["id"], "2026-08-30", "WhatsApp")
        chat = "30/08/2026, 14:35 - דנה: אפשר לדבר?".encode()

        self.store.add_files(
            folder["id"], document["id"], [("chat.txt", "text/plain", chat)]
        )

        saved = self.store.get(folder["id"])["documents"][0]
        self.assertEqual(saved["whatsapp_chat"]["messages"][0]["sender"], "דנה")

    def test_imports_only_messages_from_the_document_date(self):
        folder = self.store.create("שיחות")
        document = self.store.create_document(folder["id"], "2026-08-30", "WhatsApp")
        chat = (
            "29/08/2026, 10:00 - דנה: אתמול\n"
            "30/08/2026, 14:35 - דנה: אפשר לדבר?\n"
        ).encode()

        self.store.add_files(
            folder["id"], document["id"], [("chat.txt", "text/plain", chat)]
        )

        saved = self.store.get(folder["id"])["documents"][0]
        self.assertEqual(len(saved["whatsapp_chat"]["messages"]), 1)
        self.assertEqual(saved["whatsapp_chat"]["messages"][0]["date"], "30/08/2026")

    def test_skips_whatsapp_chat_when_no_message_matches_document_date(self):
        folder = self.store.create("שיחות")
        document = self.store.create_document(folder["id"], "2020-01-01", "WhatsApp")
        chat = "30/08/2026, 14:35 - דנה: אפשר לדבר?".encode()

        self.store.add_files(
            folder["id"], document["id"], [("chat.txt", "text/plain", chat)]
        )

        saved = self.store.get(folder["id"])["documents"][0]
        self.assertNotIn("whatsapp_chat", saved)

    def test_removes_attachment_and_its_file_on_disk(self):
        folder = self.store.create("שיחות")
        document = self.store.create_document(folder["id"], "2026-08-30", "מסמך")
        saved = self.store.add_files(
            folder["id"], document["id"], [("photo.jpg", "image/jpeg", b"data")]
        )
        file_id = saved[0]["id"]
        stored_path = (
            self.store._attachments_path(folder["id"], document["id"])
            / saved[0]["stored_name"]
        )
        self.assertTrue(stored_path.exists())

        updated = self.store.remove_file(folder["id"], document["id"], file_id)

        self.assertEqual(updated["files"], [])
        self.assertFalse(stored_path.exists())
        reloaded = self.store.get(folder["id"])["documents"][0]
        self.assertEqual(reloaded["files"], [])

    def test_removing_the_whatsapp_source_file_clears_the_parsed_chat(self):
        folder = self.store.create("שיחות")
        document = self.store.create_document(folder["id"], "2026-08-30", "WhatsApp")
        chat = "30/08/2026, 14:35 - דנה: אפשר לדבר?".encode()
        saved = self.store.add_files(
            folder["id"], document["id"], [("chat.txt", "text/plain", chat)]
        )
        file_id = saved[0]["id"]

        updated = self.store.remove_file(folder["id"], document["id"], file_id)

        self.assertNotIn("whatsapp_chat", updated)
        self.assertNotIn("whatsapp_chat_file_id", updated)

    def test_removing_an_unrelated_file_keeps_the_parsed_chat(self):
        folder = self.store.create("שיחות")
        document = self.store.create_document(folder["id"], "2026-08-30", "WhatsApp")
        chat = "30/08/2026, 14:35 - דנה: אפשר לדבר?".encode()
        self.store.add_files(
            folder["id"], document["id"], [("chat.txt", "text/plain", chat)]
        )
        saved = self.store.add_files(
            folder["id"], document["id"], [("photo.jpg", "image/jpeg", b"data")]
        )
        photo_id = saved[0]["id"]

        updated = self.store.remove_file(folder["id"], document["id"], photo_id)

        self.assertIn("whatsapp_chat", updated)

    def test_remove_file_returns_none_for_missing_file_or_document(self):
        folder = self.store.create("שיחות")
        document = self.store.create_document(folder["id"], "2026-08-30", "מסמך")

        self.assertIsNone(
            self.store.remove_file(folder["id"], document["id"], "missing")
        )
        self.assertIsNone(
            self.store.remove_file(folder["id"], "missing-document", "missing")
        )
        self.assertIsNone(
            self.store.remove_file("missing-folder", document["id"], "missing")
        )



if __name__ == "__main__":
    unittest.main()
