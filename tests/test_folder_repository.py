import os
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from binder import folder_repository
from binder.database import Base


class FolderRepositoryTests(unittest.TestCase):
    """Folder/document metadata tests: no attachments, so plain SQLite is enough."""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)

    def test_empty_store(self):
        self.assertEqual(folder_repository.list_folders(self.factory), [])

    def test_create_folder_persists_hebrew(self):
        created = folder_repository.create_folder(self.factory, "ועד הבית", "מסמכים והתכתבויות")

        self.assertEqual(created["name"], "ועד הבית")
        self.assertEqual(created["description"], "מסמכים והתכתבויות")
        self.assertEqual(created["documents"], [])
        self.assertEqual(folder_repository.list_folders(self.factory)[0]["id"], created["id"])

    def test_newest_folder_is_first(self):
        first = folder_repository.create_folder(self.factory, "ראשונה")
        second = folder_repository.create_folder(self.factory, "שנייה")

        ids = [folder["id"] for folder in folder_repository.list_folders(self.factory)]

        self.assertEqual(ids, [second["id"], first["id"]])

    def test_get_folder_with_documents(self):
        created = folder_repository.create_folder(self.factory, "ועד הבית")

        self.assertEqual(folder_repository.get_folder(self.factory, created["id"])["id"], created["id"])

    def test_get_missing_folder(self):
        self.assertIsNone(folder_repository.get_folder(self.factory, "missing"))

    def test_create_document(self):
        folder = folder_repository.create_folder(self.factory, "ועד הבית")

        document = folder_repository.create_document(
            self.factory,
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
        stored = folder_repository.get_folder(self.factory, folder["id"])["documents"]
        self.assertEqual([item["id"] for item in stored], [document["id"]])

    def test_create_document_in_missing_folder(self):
        self.assertIsNone(
            folder_repository.create_document(self.factory, "missing", "2026-08-30", "מסמך")
        )

    def test_list_folders_excludes_document_content(self):
        folder = folder_repository.create_folder(self.factory, "ועד הבית")
        folder_repository.create_document(
            self.factory, folder["id"], "2026-08-30", "מסמך", content="תוכן סודי"
        )

        listed_documents = folder_repository.list_folders(self.factory)[0]["documents"]

        self.assertNotIn("content", listed_documents[0])

    def test_documents_are_sorted_by_date_newest_first(self):
        folder = folder_repository.create_folder(self.factory, "מסמכים")
        first = folder_repository.create_document(self.factory, folder["id"], "2026-08-30", "ראשון")
        second = folder_repository.create_document(self.factory, folder["id"], "2026-01-01", "שני")

        documents = folder_repository.get_folder(self.factory, folder["id"])["documents"]

        self.assertEqual(
            [document["id"] for document in documents], [first["id"], second["id"]]
        )

    def test_documents_on_the_same_date_are_sorted_by_time_newest_first(self):
        folder = folder_repository.create_folder(self.factory, "מסמכים")
        early = folder_repository.create_document(
            self.factory, folder["id"], "2026-08-30", "מוקדם", time="09:00"
        )
        late = folder_repository.create_document(
            self.factory, folder["id"], "2026-08-30", "מאוחר", time="17:30"
        )

        documents = folder_repository.get_folder(self.factory, folder["id"])["documents"]

        self.assertEqual(
            [document["id"] for document in documents], [late["id"], early["id"]]
        )

    def test_update_document_content(self):
        folder = folder_repository.create_folder(self.factory, "התכתבויות")
        document = folder_repository.create_document(
            self.factory, folder["id"], "2026-08-30", "שיחה", content="הודעה ראשונה"
        )

        updated = folder_repository.update_document(
            self.factory, folder["id"], document["id"], {"content": "הודעה ראשונה\nהודעה נוספת"}
        )

        self.assertEqual(updated["content"], "הודעה ראשונה\nהודעה נוספת")
        saved = folder_repository.get_folder(self.factory, folder["id"])["documents"][0]
        self.assertEqual(saved["content"], "הודעה ראשונה\nהודעה נוספת")

    def test_update_missing_document(self):
        folder = folder_repository.create_folder(self.factory, "התכתבויות")

        self.assertIsNone(
            folder_repository.update_document(self.factory, folder["id"], "missing", {"content": "תוכן"})
        )

    def test_update_all_document_fields(self):
        folder = folder_repository.create_folder(self.factory, "מסמכים")
        document = folder_repository.create_document(self.factory, folder["id"], "2026-08-30", "ישן")
        updates = {
            "name": "חדש", "date": "2026-09-12", "time": "12:30",
            "description": "תיאור חדש", "content": "תוכן חדש",
            "summary": "סיכום מחשבות חדש",
        }

        updated = folder_repository.update_document(self.factory, folder["id"], document["id"], updates)
        reloaded = folder_repository.get_folder(self.factory, folder["id"])["documents"][0]

        self.assertEqual(updated, reloaded)
        for key, value in updates.items():
            self.assertEqual(reloaded[key], value)
        self.assertEqual(reloaded["id"], document["id"])
        self.assertEqual(reloaded["created_at"], document["created_at"])
        self.assertGreater(reloaded["updated_at"], document["updated_at"])

        cleared = folder_repository.update_document(
            self.factory,
            folder["id"],
            document["id"],
            {"time": "", "description": "", "content": "", "summary": ""},
        )
        self.assertEqual(cleared["name"], updates["name"])
        for key in ("time", "description", "content", "summary"):
            self.assertEqual(cleared[key], "")

    def test_remove_file_returns_none_for_missing_file_or_document(self):
        folder = folder_repository.create_folder(self.factory, "שיחות")
        document = folder_repository.create_document(self.factory, folder["id"], "2026-08-30", "מסמך")

        self.assertIsNone(
            folder_repository.remove_file(self.factory, folder["id"], document["id"], "missing")
        )
        self.assertIsNone(
            folder_repository.remove_file(self.factory, folder["id"], "missing-document", "missing")
        )
        self.assertIsNone(
            folder_repository.remove_file(self.factory, "missing-folder", document["id"], "missing")
        )


_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


@unittest.skipUnless(
    _TEST_DATABASE_URL,
    "attachments are stored as PostgreSQL large objects; set TEST_DATABASE_URL to a "
    "disposable Postgres database to run these tests (its tables are dropped and "
    "recreated for every test)",
)
class AttachmentRepositoryTests(unittest.TestCase):
    """Attachment tests: these need real PostgreSQL for large object support."""

    def setUp(self):
        self.engine = create_engine(_TEST_DATABASE_URL)
        with self.engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS lo"))
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TRIGGER document_files_lo_trigger BEFORE UPDATE OR DELETE "
                    "ON document_files FOR EACH ROW EXECUTE FUNCTION lo_manage(data_oid)"
                )
            )
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_add_and_read_document_files(self):
        folder = folder_repository.create_folder(self.factory, "תמונות")
        document = folder_repository.create_document(self.factory, folder["id"], "2026-08-30", "תיעוד")

        files = folder_repository.add_files(
            self.factory,
            folder["id"],
            document["id"],
            [("צילום.jpg", "image/jpeg", b"image bytes")],
        )

        self.assertEqual(files[0]["name"], "צילום.jpg")
        self.assertEqual(files[0]["content_type"], "image/jpeg")
        self.assertEqual(files[0]["size"], 11)
        stored_document = folder_repository.get_folder(self.factory, folder["id"])["documents"][0]
        self.assertEqual(stored_document["files"], files)
        data, content_type, name = folder_repository.get_file(
            self.factory, folder["id"], document["id"], files[0]["id"]
        )
        self.assertEqual(data, b"image bytes")
        self.assertEqual(content_type, "image/jpeg")
        self.assertEqual(name, "צילום.jpg")

    def test_add_file_from_stream(self):
        import io

        folder = folder_repository.create_folder(self.factory, "וידאו")
        document = folder_repository.create_document(self.factory, folder["id"], "2026-08-30", "סרטון")
        content = b"mov data" * 1024

        files = folder_repository.add_files(
            self.factory,
            folder["id"],
            document["id"],
            [("recording.mov", "video/quicktime", io.BytesIO(content))],
        )

        data, content_type, _name = folder_repository.get_file(
            self.factory, folder["id"], document["id"], files[0]["id"]
        )
        self.assertEqual(data, content)
        self.assertEqual(content_type, "video/quicktime")
        self.assertEqual(files[0]["size"], len(content))

    def test_add_file_spanning_multiple_chunks(self):
        folder = folder_repository.create_folder(self.factory, "גדול")
        document = folder_repository.create_document(self.factory, folder["id"], "2026-08-30", "קובץ גדול")
        content = b"x" * (5 * 1024 * 1024)  # bigger than the large-object chunk size

        files = folder_repository.add_files(
            self.factory, folder["id"], document["id"], [("big.bin", "application/octet-stream", content)]
        )

        data, _content_type, _name = folder_repository.get_file(
            self.factory, folder["id"], document["id"], files[0]["id"]
        )
        self.assertEqual(data, content)

    def test_update_document_preserves_files_and_identity(self):
        folder = folder_repository.create_folder(self.factory, "מסמכים")
        document = folder_repository.create_document(self.factory, folder["id"], "2026-08-30", "ישן")
        files = folder_repository.add_files(
            self.factory, folder["id"], document["id"], [("note.txt", "text/plain", b"note")]
        )

        updated = folder_repository.update_document(
            self.factory, folder["id"], document["id"], {"name": "חדש"}
        )

        self.assertEqual(updated["files"], files)

    def test_imports_whatsapp_chat_from_attachment(self):
        folder = folder_repository.create_folder(self.factory, "שיחות")
        document = folder_repository.create_document(self.factory, folder["id"], "2026-08-30", "WhatsApp")
        chat = "30/08/2026, 14:35 - דנה: אפשר לדבר?".encode()

        folder_repository.add_files(
            self.factory, folder["id"], document["id"], [("chat.txt", "text/plain", chat)]
        )

        saved = folder_repository.get_folder(self.factory, folder["id"])["documents"][0]
        self.assertEqual(saved["whatsapp_chat"]["messages"][0]["sender"], "דנה")

    def test_imports_only_messages_from_the_document_date(self):
        folder = folder_repository.create_folder(self.factory, "שיחות")
        document = folder_repository.create_document(self.factory, folder["id"], "2026-08-30", "WhatsApp")
        chat = (
            "29/08/2026, 10:00 - דנה: אתמול\n"
            "30/08/2026, 14:35 - דנה: אפשר לדבר?\n"
        ).encode()

        folder_repository.add_files(
            self.factory, folder["id"], document["id"], [("chat.txt", "text/plain", chat)]
        )

        saved = folder_repository.get_folder(self.factory, folder["id"])["documents"][0]
        self.assertEqual(len(saved["whatsapp_chat"]["messages"]), 1)
        self.assertEqual(saved["whatsapp_chat"]["messages"][0]["date"], "30/08/2026")

    def test_skips_whatsapp_chat_when_no_message_matches_document_date(self):
        folder = folder_repository.create_folder(self.factory, "שיחות")
        document = folder_repository.create_document(self.factory, folder["id"], "2020-01-01", "WhatsApp")
        chat = "30/08/2026, 14:35 - דנה: אפשר לדבר?".encode()

        folder_repository.add_files(
            self.factory, folder["id"], document["id"], [("chat.txt", "text/plain", chat)]
        )

        saved = folder_repository.get_folder(self.factory, folder["id"])["documents"][0]
        self.assertNotIn("whatsapp_chat", saved)

    def test_removes_attachment(self):
        folder = folder_repository.create_folder(self.factory, "שיחות")
        document = folder_repository.create_document(self.factory, folder["id"], "2026-08-30", "מסמך")
        saved = folder_repository.add_files(
            self.factory, folder["id"], document["id"], [("photo.jpg", "image/jpeg", b"data")]
        )
        file_id = saved[0]["id"]

        updated = folder_repository.remove_file(self.factory, folder["id"], document["id"], file_id)

        self.assertEqual(updated["files"], [])
        self.assertIsNone(
            folder_repository.get_file(self.factory, folder["id"], document["id"], file_id)
        )
        reloaded = folder_repository.get_folder(self.factory, folder["id"])["documents"][0]
        self.assertEqual(reloaded["files"], [])

    def test_removing_the_whatsapp_source_file_clears_the_parsed_chat(self):
        folder = folder_repository.create_folder(self.factory, "שיחות")
        document = folder_repository.create_document(self.factory, folder["id"], "2026-08-30", "WhatsApp")
        chat = "30/08/2026, 14:35 - דנה: אפשר לדבר?".encode()
        saved = folder_repository.add_files(
            self.factory, folder["id"], document["id"], [("chat.txt", "text/plain", chat)]
        )
        file_id = saved[0]["id"]

        updated = folder_repository.remove_file(self.factory, folder["id"], document["id"], file_id)

        self.assertNotIn("whatsapp_chat", updated)
        self.assertNotIn("whatsapp_chat_file_id", updated)

    def test_removing_an_unrelated_file_keeps_the_parsed_chat(self):
        folder = folder_repository.create_folder(self.factory, "שיחות")
        document = folder_repository.create_document(self.factory, folder["id"], "2026-08-30", "WhatsApp")
        chat = "30/08/2026, 14:35 - דנה: אפשר לדבר?".encode()
        folder_repository.add_files(
            self.factory, folder["id"], document["id"], [("chat.txt", "text/plain", chat)]
        )
        saved = folder_repository.add_files(
            self.factory, folder["id"], document["id"], [("photo.jpg", "image/jpeg", b"data")]
        )
        photo_id = saved[0]["id"]

        updated = folder_repository.remove_file(self.factory, folder["id"], document["id"], photo_id)

        self.assertIn("whatsapp_chat", updated)


if __name__ == "__main__":
    unittest.main()
