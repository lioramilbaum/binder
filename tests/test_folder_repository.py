import os
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from binder import folder_repository
from binder import dedupe
from binder.database import Base
from binder.large_objects import RELEASE_FUNCTION_SQL, RELEASE_TRIGGER_SQL


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

    def test_ai_threads_persist_in_folder_and_order_by_conversation_date(self):
        folder = folder_repository.create_folder(self.factory, "Case")
        other = folder_repository.create_folder(self.factory, "Other")
        older = folder_repository.create_ai_thread(
            self.factory, folder["id"], "Older", "Claude", "2026-01-01",
            "User: first\nAI: answer", "My note",
        )
        newer = folder_repository.create_ai_thread(
            self.factory, folder["id"], "Newer", "ChatGPT", "2026-02-01",
            "Full transcript", "",
        )
        folder_repository.create_ai_thread(
            self.factory, other["id"], "Private", "", "2026-03-01", "Elsewhere", "",
        )

        saved = folder_repository.get_folder(self.factory, folder["id"])["ai_threads"]
        self.assertEqual([thread["id"] for thread in saved], [newer["id"], older["id"]])
        self.assertEqual(saved[1]["transcript"], "User: first\nAI: answer")
        self.assertEqual(saved[1]["summary"], "My note")
        self.assertIsNone(saved[1]["source_url"])
        self.assertIsNone(saved[1]["imported_at"])
        self.assertIsNone(saved[1]["transcript_sha256"])
        self.assertIsNone(saved[1]["conversation_time"])
        self.assertIsNone(saved[1]["source_first_message_at"])
        self.assertEqual(len(folder_repository.get_folder(self.factory, other["id"])["ai_threads"]), 1)
        self.assertNotIn("ai_threads", folder_repository.list_folders(self.factory)[0])

    def test_import_source_metadata_is_folder_scoped(self):
        folder = folder_repository.create_folder(self.factory, "Case")
        other = folder_repository.create_folder(self.factory, "Other")
        imported = folder_repository.create_ai_thread(
            self.factory, folder["id"], "Shared", "ChatGPT", "2026-09-28", "Exact text", "",
            source_url="https://chatgpt.com/share/12345678-1234-4234-8234-123456789abc",
            transcript_sha256="a" * 64,
            conversation_time="14:35",
            source_first_message_at="2023-11-14T22:13:20.250000+00:00",
        )
        self.assertEqual(imported["transcript_sha256"], "a" * 64)
        self.assertEqual(imported["conversation_time"], "14:35")
        self.assertEqual(imported["source_first_message_at"], "2023-11-14T22:13:20.250000+00:00")
        self.assertIsNotNone(imported["imported_at"])
        self.assertEqual(folder_repository.get_folder(self.factory, folder["id"])["ai_threads"][0], imported)
        self.assertEqual(folder_repository.get_folder(self.factory, other["id"])["ai_threads"], [])

    def test_ai_thread_requires_existing_folder(self):
        self.assertIsNone(folder_repository.create_ai_thread(
            self.factory, "missing", "Title", "", "2026-01-01", "Conversation", "",
        ))

    def test_ai_threads_on_same_date_order_by_creation_time(self):
        folder = folder_repository.create_folder(self.factory, "Case")
        with patch.object(folder_repository, "_now", side_effect=[
            "2026-09-28T10:00:00+00:00", "2026-09-28T11:00:00+00:00",
        ]):
            first = folder_repository.create_ai_thread(
                self.factory, folder["id"], "First", "", "2026-09-20", "First", "",
            )
            second = folder_repository.create_ai_thread(
                self.factory, folder["id"], "Second", "", "2026-09-20", "Second", "",
            )
        saved = folder_repository.get_folder(self.factory, folder["id"])["ai_threads"]
        self.assertEqual([thread["id"] for thread in saved], [second["id"], first["id"]])

    def test_ai_threads_on_same_date_order_by_time_then_creation(self):
        folder = folder_repository.create_folder(self.factory, "Case")
        with patch.object(folder_repository, "_now", side_effect=[
            "2026-09-28T13:00:00+00:00", "2026-09-28T12:00:00+00:00",
            "2026-09-28T14:00:00+00:00",
        ]):
            early = folder_repository.create_ai_thread(
                self.factory, folder["id"], "Early", "", "2026-09-20", "First", "",
                conversation_time="09:00",
            )
            late = folder_repository.create_ai_thread(
                self.factory, folder["id"], "Late", "", "2026-09-20", "Second", "",
                conversation_time="14:00",
            )
            unknown = folder_repository.create_ai_thread(
                self.factory, folder["id"], "Unknown", "", "2026-09-20", "Third", "",
            )
        saved = folder_repository.get_folder(self.factory, folder["id"])["ai_threads"]
        self.assertEqual([thread["id"] for thread in saved], [late["id"], early["id"], unknown["id"]])

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

    def test_create_document_creates_missing_tags(self):
        folder = folder_repository.create_folder(self.factory, "מסמכים")

        document = folder_repository.create_document(
            self.factory, folder["id"], "2026-08-30", "מסמך", tags=["תקציב", "Urgent"]
        )

        self.assertEqual(set(document["tags"]), {"תקציב", "Urgent"})
        tags = folder_repository.list_tags(self.factory)
        self.assertEqual(len(tags), 2)
        tag_names = {t["name"] for t in tags}
        self.assertEqual(tag_names, {"תקציב", "Urgent"})
        budget_tag = next(t for t in tags if t["name"] == "תקציב")
        self.assertEqual(budget_tag["document_count"], 1)

    def test_existing_tag_is_reused_ignoring_case_and_spacing(self):
        folder = folder_repository.create_folder(self.factory, "מסמכים")

        doc_a = folder_repository.create_document(
            self.factory, folder["id"], "2026-08-30", "א", tags=["Budget"]
        )
        doc_b = folder_repository.create_document(
            self.factory, folder["id"], "2026-08-31", "ב", tags=["  budget  ", "BUDGET"]
        )

        self.assertEqual(doc_b["tags"], ["Budget"])
        tags = folder_repository.list_tags(self.factory)
        budget_tag = next(t for t in tags if t["name"] == "Budget")
        self.assertEqual(budget_tag["document_count"], 2)

    def test_update_document_replaces_tags_and_keeps_them_when_omitted(self):
        folder = folder_repository.create_folder(self.factory, "מסמכים")
        doc = folder_repository.create_document(
            self.factory, folder["id"], "2026-08-30", "מסמך", tags=["א", "ב"]
        )

        updated = folder_repository.update_document(
            self.factory, folder["id"], doc["id"], {"tags": ["ב", "ג"]}
        )
        self.assertEqual(set(updated["tags"]), {"ב", "ג"})

        updated = folder_repository.update_document(
            self.factory, folder["id"], doc["id"], {"name": "חדש"}
        )
        self.assertEqual(sorted(updated["tags"], key=str.lower), ["ב", "ג"])

        updated = folder_repository.update_document(
            self.factory, folder["id"], doc["id"], {"tags": []}
        )
        self.assertEqual(updated["tags"], [])

    def test_folder_listing_includes_document_tags(self):
        folder = folder_repository.create_folder(self.factory, "מסמכים")
        folder_repository.create_document(
            self.factory, folder["id"], "2026-08-30", "מסמך", tags=["Important"]
        )

        folders = folder_repository.list_folders(self.factory)
        doc = folders[0]["documents"][0]

        self.assertEqual(doc["tags"], ["Important"])


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
            # Dropping tables leaves their large objects behind; start clean.
            connection.execute(text("SELECT lo_unlink(oid) FROM pg_largeobject_metadata"))
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text(RELEASE_FUNCTION_SQL))
            connection.execute(text(RELEASE_TRIGGER_SQL))
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

    def _document(self, folder, name):
        return folder_repository.create_document(self.factory, folder["id"], "2026-08-30", name)

    def _large_object_count(self):
        with self.engine.connect() as connection:
            return connection.execute(
                text("SELECT count(*) FROM pg_largeobject_metadata")
            ).scalar_one()

    def test_identical_uploads_share_one_large_object(self):
        folder = folder_repository.create_folder(self.factory, "כפילויות")
        first, second = self._document(folder, "א"), self._document(folder, "ב")
        one = folder_repository.add_files(
            self.factory, folder["id"], first["id"], [("a.pdf", "application/pdf", b"same")]
        )
        two = folder_repository.add_files(
            self.factory, folder["id"], second["id"], [("copy.pdf", "application/pdf", b"same")]
        )

        self.assertEqual(self._large_object_count(), 1)
        data, _type, _name = folder_repository.get_file(
            self.factory, folder["id"], second["id"], two[0]["id"]
        )
        self.assertEqual(data, b"same")
        groups = folder_repository.find_duplicates(self.factory)["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual({file["id"] for file in groups[0]["files"]}, {one[0]["id"], two[0]["id"]})
        self.assertEqual(groups[0]["reclaimable_bytes"], 0)

    def test_different_content_is_not_a_duplicate(self):
        folder = folder_repository.create_folder(self.factory, "כפילויות")
        document = self._document(folder, "א")
        folder_repository.add_files(
            self.factory,
            folder["id"],
            document["id"],
            [("a.txt", "text/plain", b"one"), ("b.txt", "text/plain", b"two")],
        )

        self.assertEqual(folder_repository.find_duplicates(self.factory)["groups"], [])
        self.assertEqual(self._large_object_count(), 2)

    def test_shared_bytes_survive_until_last_reference_is_removed(self):
        folder = folder_repository.create_folder(self.factory, "כפילויות")
        first, second = self._document(folder, "א"), self._document(folder, "ב")
        one = folder_repository.add_files(
            self.factory, folder["id"], first["id"], [("a.bin", "application/octet-stream", b"x")]
        )
        two = folder_repository.add_files(
            self.factory, folder["id"], second["id"], [("b.bin", "application/octet-stream", b"x")]
        )

        folder_repository.remove_file(self.factory, folder["id"], first["id"], one[0]["id"])

        self.assertEqual(self._large_object_count(), 1)
        data, _type, _name = folder_repository.get_file(
            self.factory, folder["id"], second["id"], two[0]["id"]
        )
        self.assertEqual(data, b"x")
        folder_repository.remove_file(self.factory, folder["id"], second["id"], two[0]["id"])
        self.assertEqual(self._large_object_count(), 0)

    def test_backfill_and_merge_existing_unhashed_copies(self):
        folder = folder_repository.create_folder(self.factory, "כפילויות")
        first, second = self._document(folder, "א"), self._document(folder, "ב")
        for document in (first, second):
            folder_repository.add_files(
                self.factory, folder["id"], document["id"], [("f.bin", "application/octet-stream", b"legacy")]
            )
        # Simulate legacy rows: unhashed, each with its own large object.
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE document_files SET sha256 = NULL"))
            oid = connection.execute(text("SELECT data_oid FROM document_files LIMIT 1")).scalar_one()
            copy = connection.execute(text("SELECT lo_from_bytea(0, lo_get(:oid))"), {"oid": oid}).scalar_one()
            connection.execute(
                text("UPDATE document_files SET data_oid = :copy WHERE id = (SELECT id FROM document_files WHERE data_oid = :oid LIMIT 1)"),
                {"copy": copy, "oid": oid},
            )
        self.assertEqual(self._large_object_count(), 2)
        self.assertEqual(folder_repository.find_duplicates(self.factory)["unhashed"], 2)

        self.assertEqual(dedupe.backfill_hashes(self.factory), 2)
        report = folder_repository.find_duplicates(self.factory)
        self.assertEqual(report["reclaimable_bytes"], len(b"legacy"))

        self.assertEqual(dedupe.merge_duplicates(self.factory), len(b"legacy"))
        self.assertEqual(self._large_object_count(), 1)
        self.assertEqual(folder_repository.find_duplicates(self.factory)["reclaimable_bytes"], 0)

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
