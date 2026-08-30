import io
import unittest
import zipfile

from src.binder.whatsapp import WhatsAppChat


CHAT_TEXT = """30/08/2026, 14:35 - דנה: אפשר לדבר? 😊
30/08/2026, 14:36 - אני: כן
המשך של אותה הודעה
30/08/2026, 14:37 - ההודעות בקבוצה זו מוצפנות מקצה לקצה.
"""


class WhatsAppChatTests(unittest.TestCase):
    def test_parses_android_export(self):
        chat = WhatsAppChat.from_file("WhatsApp Chat.txt", CHAT_TEXT.encode())

        self.assertEqual(chat.participants, ["דנה", "אני"])
        self.assertEqual(chat.to_dict()["me"], "Liora Milbaum")
        self.assertEqual(len(chat.messages), 3)
        self.assertEqual(chat.messages[0]["content"], "אפשר לדבר? 😊")
        self.assertEqual(chat.messages[1]["content"], "כן\nהמשך של אותה הודעה")
        self.assertTrue(chat.messages[2]["system"])

    def test_parses_iphone_export(self):
        content = "[30.08.2026, 09:12:03] Dana: Morning 👍".encode()

        chat = WhatsAppChat.from_file("_chat.txt", content)

        self.assertEqual(chat.messages[0]["sender"], "Dana")
        self.assertEqual(chat.messages[0]["time"], "09:12:03")

    def test_parses_export_from_zip(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("_chat.txt", CHAT_TEXT)
            archive.writestr("photo.jpg", b"image")

        chat = WhatsAppChat.from_file("WhatsApp Chat.zip", buffer.getvalue())

        self.assertEqual(len(chat.messages), 3)

    def test_ignores_unrelated_file(self):
        self.assertIsNone(WhatsAppChat.from_file("notes.pdf", b"content"))


if __name__ == "__main__":
    unittest.main()
