import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
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
        older = folder_repository.create_document(
            self.factory, folder["id"], "2026-01-01", "Older",
            description="", content="User: first\nAI: answer", time="",
            summary="My note", document_type="ai", provider="Claude"
        )
        newer = folder_repository.create_document(
            self.factory, folder["id"], "2026-02-01", "Newer",
            description="", content="Full transcript", time="",
            summary="", document_type="ai", provider="ChatGPT"
        )
        folder_repository.create_document(
            self.factory, other["id"], "2026-03-01", "Private",
            description="", content="Elsewhere", time="",
            summary="", document_type="ai", provider=""
        )

        saved = folder_repository.get_folder(self.factory, folder["id"])["documents"]
        # Filter to AI documents only
        ai_docs = [d for d in saved if d.get("type") == "ai"]
        self.assertEqual([doc["id"] for doc in ai_docs], [newer["id"], older["id"]])
        self.assertEqual(ai_docs[1]["content"], "User: first\nAI: answer")
        self.assertEqual(ai_docs[1]["summary"], "My note")
        self.assertIsNone(ai_docs[1].get("source_url"))
        self.assertIsNone(ai_docs[1].get("imported_at"))
        self.assertIsNone(ai_docs[1].get("transcript_sha256"))
        self.assertEqual(ai_docs[1]["time"], "")
        self.assertIsNone(ai_docs[1].get("source_first_message_at"))
        other_docs = folder_repository.get_folder(self.factory, other["id"])["documents"]
        self.assertEqual(len(other_docs), 1)

    def test_import_source_metadata_is_folder_scoped(self):
        folder = folder_repository.create_folder(self.factory, "Case")
        other = folder_repository.create_folder(self.factory, "Other")
        imported = folder_repository.create_document(
            self.factory, folder["id"], "2026-09-28", "Shared",
            description="", content="Exact text", time="14:35",
            summary="", document_type="ai", provider="ChatGPT",
            source_url="https://chatgpt.com/share/12345678-1234-4234-8234-123456789abc",
            transcript_sha256="a" * 64,
            source_first_message_at="2023-11-14T22:13:20.250000+00:00",
        )
        self.assertEqual(imported.get("transcript_sha256"), "a" * 64)
        self.assertEqual(imported["time"], "14:35")
        self.assertEqual(imported.get("source_first_message_at"), "2023-11-14T22:13:20.250000+00:00")
        self.assertIsNotNone(imported.get("imported_at"))
        other_docs = folder_repository.get_folder(self.factory, other["id"])["documents"]
        self.assertEqual(len(other_docs), 0)

    def test_ai_thread_requires_existing_folder(self):
        self.assertIsNone(folder_repository.create_document(
            self.factory, "missing", "2026-01-01", "Title",
            description="", content="Conversation", time="",
            summary="", document_type="ai", provider=""
        ))

    def test_ai_threads_on_same_date_order_by_creation_time(self):
        folder = folder_repository.create_folder(self.factory, "Case")
        with patch.object(folder_repository, "_now", side_effect=[
            "2026-09-28T10:00:00+00:00", "2026-09-28T11:00:00+00:00",
        ]):
            first = folder_repository.create_document(
                self.factory, folder["id"], "2026-09-20", "First",
                description="", content="First", time="",
                summary="", document_type="ai", provider=""
            )
            second = folder_repository.create_document(
                self.factory, folder["id"], "2026-09-20", "Second",
                description="", content="Second", time="",
                summary="", document_type="ai", provider=""
            )
        saved = folder_repository.get_folder(self.factory, folder["id"])["documents"]
        ai_docs = [d for d in saved if d.get("type") == "ai"]
        self.assertEqual([doc["id"] for doc in ai_docs], [second["id"], first["id"]])

    def test_ai_threads_on_same_date_order_by_time_then_creation(self):
        folder = folder_repository.create_folder(self.factory, "Case")
        with patch.object(folder_repository, "_now", side_effect=[
            "2026-09-28T13:00:00+00:00", "2026-09-28T12:00:00+00:00",
            "2026-09-28T14:00:00+00:00",
        ]):
            early = folder_repository.create_document(
                self.factory, folder["id"], "2026-09-20", "Early",
                description="", content="First", time="09:00",
                summary="", document_type="ai", provider=""
            )
            late = folder_repository.create_document(
                self.factory, folder["id"], "2026-09-20", "Late",
                description="", content="Second", time="14:00",
                summary="", document_type="ai", provider=""
            )
            unknown = folder_repository.create_document(
                self.factory, folder["id"], "2026-09-20", "Unknown",
                description="", content="Third", time="",
                summary="", document_type="ai", provider=""
            )
        saved = folder_repository.get_folder(self.factory, folder["id"])["documents"]
        ai_docs = [d for d in saved if d.get("type") == "ai"]
        self.assertEqual([doc["id"] for doc in ai_docs], [late["id"], early["id"], unknown["id"]])

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


class DocumentTypeMigrationTests(unittest.TestCase):
    """Tests for the ai_threads to documents migration."""

    def test_migration_moves_ai_threads_to_documents_with_type(self):
        path = Path(__file__).resolve().parents[1] / "migrations/versions/20260930_08_unify_ai_threads_into_documents.py"
        spec = importlib.util.spec_from_file_location("doc_type_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            # Create schema at revision 07 (before migration)
            connection.execute(text("CREATE TABLE folders (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"))
            connection.execute(text("CREATE TABLE documents (id TEXT PRIMARY KEY, folder_id TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL, content TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, whatsapp_chat TEXT, whatsapp_chat_file_id TEXT, FOREIGN KEY (folder_id) REFERENCES folders(id))"))
            connection.execute(text("CREATE TABLE ai_threads (id TEXT PRIMARY KEY, folder_id TEXT NOT NULL, title TEXT NOT NULL, provider TEXT NOT NULL, conversation_date TEXT NOT NULL, conversation_time TEXT, transcript TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL, source_url TEXT, imported_at TEXT, transcript_sha256 TEXT, source_first_message_at TEXT, FOREIGN KEY (folder_id) REFERENCES folders(id))"))
            # Create indexes for ai_threads
            connection.execute(text("CREATE INDEX ix_ai_threads_folder_id ON ai_threads (folder_id)"))
            connection.execute(text("CREATE INDEX ix_ai_threads_conversation_date ON ai_threads (conversation_date)"))
            connection.execute(text("CREATE INDEX ix_ai_threads_folder_chronology ON ai_threads (folder_id, conversation_date, created_at)"))

            # Insert test data
            connection.execute(text("INSERT INTO folders (id, name, description, created_at, updated_at) VALUES ('f1', 'Test', '', '2026-09-30T00:00:00+00:00', '2026-09-30T00:00:00+00:00')"))
            connection.execute(text("INSERT INTO documents (id, folder_id, date, time, name, description, content, summary, created_at, updated_at) VALUES ('d1', 'f1', '2026-09-30', '', 'Regular', '', 'Content', '', '2026-09-30T00:00:00+00:00', '2026-09-30T00:00:00+00:00')"))
            connection.execute(text("INSERT INTO ai_threads (id, folder_id, title, provider, conversation_date, conversation_time, transcript, summary, created_at, source_url, imported_at, transcript_sha256, source_first_message_at) VALUES ('a1', 'f1', 'AI Thread', 'ChatGPT', '2026-09-28', '14:35', 'Chat transcript', 'Summary', '2026-09-28T00:00:00+00:00', 'https://chatgpt.com/share/abc', '2026-09-30T01:00:00+00:00', 'abc123', '2023-11-14T22:13:20.250000+00:00')"))

            # Run migration
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(migration, "op", operations):
                migration.upgrade()

            # Verify regular document still has type='regular'
            regular = connection.execute(text("SELECT type, name, provider FROM documents WHERE id = 'd1'")).one()
            self.assertEqual(regular[0], "regular")
            self.assertEqual(regular[1], "Regular")
            self.assertEqual(regular[2], "")

            # Verify ai_thread moved as type='ai' document
            ai_doc = connection.execute(text("SELECT type, name, date, time, content, summary, provider, source_url, transcript_sha256, source_first_message_at FROM documents WHERE id = 'a1'")).one()
            self.assertEqual(ai_doc[0], "ai")
            self.assertEqual(ai_doc[1], "AI Thread")
            self.assertEqual(ai_doc[2], "2026-09-28")
            self.assertEqual(ai_doc[3], "14:35")
            self.assertEqual(ai_doc[4], "Chat transcript")
            self.assertEqual(ai_doc[5], "Summary")
            self.assertEqual(ai_doc[6], "ChatGPT")
            self.assertEqual(ai_doc[7], "https://chatgpt.com/share/abc")
            self.assertEqual(ai_doc[8], "abc123")
            self.assertEqual(ai_doc[9], "2023-11-14T22:13:20.250000+00:00")

            # Verify ai_threads table is gone
            with self.assertRaises(Exception):  # Table doesn't exist
                connection.execute(text("SELECT COUNT(*) FROM ai_threads"))

    def test_migration_downgrade_restores_ai_threads(self):
        path = Path(__file__).resolve().parents[1] / "migrations/versions/20260930_08_unify_ai_threads_into_documents.py"
        spec = importlib.util.spec_from_file_location("doc_type_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            # Create schema at revision 07
            connection.execute(text("CREATE TABLE folders (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"))
            connection.execute(text("CREATE TABLE documents (id TEXT PRIMARY KEY, folder_id TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL, content TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, whatsapp_chat TEXT, whatsapp_chat_file_id TEXT, FOREIGN KEY (folder_id) REFERENCES folders(id))"))
            connection.execute(text("CREATE TABLE ai_threads (id TEXT PRIMARY KEY, folder_id TEXT NOT NULL, title TEXT NOT NULL, provider TEXT NOT NULL, conversation_date TEXT NOT NULL, conversation_time TEXT, transcript TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL, source_url TEXT, imported_at TEXT, transcript_sha256 TEXT, source_first_message_at TEXT, FOREIGN KEY (folder_id) REFERENCES folders(id))"))
            connection.execute(text("CREATE TABLE document_files (id TEXT PRIMARY KEY, document_id TEXT NOT NULL, name TEXT NOT NULL, content_type TEXT NOT NULL, size INTEGER NOT NULL, data_oid INTEGER, sha256 TEXT, created_at TEXT NOT NULL, FOREIGN KEY (document_id) REFERENCES documents(id))"))
            # Create indexes for ai_threads
            connection.execute(text("CREATE INDEX ix_ai_threads_folder_id ON ai_threads (folder_id)"))
            connection.execute(text("CREATE INDEX ix_ai_threads_conversation_date ON ai_threads (conversation_date)"))
            connection.execute(text("CREATE INDEX ix_ai_threads_folder_chronology ON ai_threads (folder_id, conversation_date, created_at)"))

            # Insert test data
            connection.execute(text("INSERT INTO folders (id, name, description, created_at, updated_at) VALUES ('f1', 'Test', '', '2026-09-30T00:00:00+00:00', '2026-09-30T00:00:00+00:00')"))
            connection.execute(text("INSERT INTO ai_threads (id, folder_id, title, provider, conversation_date, conversation_time, transcript, summary, created_at, source_url, imported_at, transcript_sha256, source_first_message_at) VALUES ('a1', 'f1', 'AI Thread', 'ChatGPT', '2026-09-28', '14:35', 'Chat transcript', 'Summary', '2026-09-28T00:00:00+00:00', 'https://chatgpt.com/share/abc', '2026-09-30T01:00:00+00:00', 'abc123', '2023-11-14T22:13:20.250000+00:00')"))

            # Run upgrade
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(migration, "op", operations):
                migration.upgrade()

            # Run downgrade
            with patch.object(migration, "op", operations):
                migration.downgrade()

            # Verify ai_thread is restored
            ai_thread = connection.execute(text("SELECT title, provider, conversation_date, conversation_time, transcript, summary, source_url, transcript_sha256, source_first_message_at FROM ai_threads WHERE id = 'a1'")).one()
            self.assertEqual(ai_thread[0], "AI Thread")
            self.assertEqual(ai_thread[1], "ChatGPT")
            self.assertEqual(ai_thread[2], "2026-09-28")
            self.assertEqual(ai_thread[3], "14:35")
            self.assertEqual(ai_thread[4], "Chat transcript")
            self.assertEqual(ai_thread[5], "Summary")
            self.assertEqual(ai_thread[6], "https://chatgpt.com/share/abc")
            self.assertEqual(ai_thread[7], "abc123")
            self.assertEqual(ai_thread[8], "2023-11-14T22:13:20.250000+00:00")

            # Verify ai_thread is removed from documents
            count = connection.execute(text("SELECT COUNT(*) FROM documents WHERE id = 'a1'")).scalar()
            self.assertEqual(count, 0)


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
