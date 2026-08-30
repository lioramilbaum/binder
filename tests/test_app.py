import json
import tempfile
import unittest
from pathlib import Path

from src.binder.app import FolderStore


class FolderStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = FolderStore(Path(self.temp_dir.name) / "folders.json")

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


if __name__ == "__main__":
    unittest.main()
