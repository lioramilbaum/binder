import unittest

from src.binder.document import Document


class DocumentTests(unittest.TestCase):
    def test_create_document(self):
        document = Document.create(
            "2026-08-30",
            "  שיחת WhatsApp  ",
            "  שיחה עם נציג הוועד  ",
            "  דני: שלום\nאני: היי  ",
            time="14:35",
            summary="  צריך לבדוק תקציב  ",
        )

        self.assertEqual(document.date, "2026-08-30")
        self.assertEqual(document.time, "14:35")
        self.assertEqual(document.name, "שיחת WhatsApp")
        self.assertEqual(document.description, "שיחה עם נציג הוועד")
        self.assertEqual(document.content, "דני: שלום\nאני: היי")
        self.assertEqual(document.summary, "צריך לבדוק תקציב")
        self.assertTrue(document.id)
        self.assertEqual(document.created_at, document.updated_at)

    def test_to_dict(self):
        document = Document.create("2026-08-30", "מסמך")

        result = document.to_dict()

        self.assertEqual(result["id"], document.id)
        self.assertEqual(result["name"], "מסמך")
        self.assertEqual(result["description"], "")
        self.assertEqual(result["content"], "")
        self.assertEqual(result["time"], "")
        self.assertEqual(result["files"], [])
        self.assertEqual(result["summary"], "")


if __name__ == "__main__":
    unittest.main()
