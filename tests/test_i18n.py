import re
import unittest
from io import BytesIO
from unittest.mock import patch

from binder.binder import Binder
from binder.config import STATIC_DIR
from binder.i18n import DEFAULT_LOCALE, LOCALES, Translator


def _placeholders(text):
    return set(re.findall(r"\{(\w+)\}", text))


def _get(path, locale):
    handler = object.__new__(Binder)
    handler.path = path
    handler.wfile = BytesIO()
    handler.send_response = lambda *args: None
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    with patch("binder.binder.LOCALE", locale):
        handler.do_GET()
    return handler.wfile.getvalue().decode("utf-8")


class I18nTests(unittest.TestCase):
    def test_default_locale_is_hebrew(self):
        self.assertEqual(DEFAULT_LOCALE, "he")
        self.assertIn('lang="he" dir="rtl"', _get("/", "he"))

    def test_every_locale_defines_the_same_keys(self):
        reference = set(LOCALES[DEFAULT_LOCALE].strings)
        for code, locale in LOCALES.items():
            self.assertEqual(set(locale.strings), reference, code)

    def test_placeholders_match_across_locales(self):
        reference = LOCALES[DEFAULT_LOCALE].strings
        for code, locale in LOCALES.items():
            for key, text in locale.strings.items():
                self.assertEqual(_placeholders(text), _placeholders(reference[key]), (code, key))

    def test_every_marker_in_static_files_is_defined(self):
        used = set()
        for name in ("index.html", "app.js"):
            used |= set(re.findall(r"@@([a-z_]+)@@", (STATIC_DIR / name).read_text(encoding="utf-8")))
        self.assertLessEqual(used - {"lang", "dir"}, set(LOCALES[DEFAULT_LOCALE].strings))

    def test_pages_render_with_all_strings_resolved(self):
        for locale, direction in (("he", "rtl"), ("en", "ltr")):
            page = _get("/", locale)
            script = _get("/app.js", locale)
            self.assertNotIn("@@", page)
            self.assertNotIn("@@", script)
            self.assertIn(f'lang="{locale}" dir="{direction}"', page)

    def test_english_page_has_no_hebrew(self):
        self.assertIsNone(re.search(r"[֐-׿]", _get("/", "en")))
        self.assertIsNone(re.search(r"[֐-׿]", _get("/app.js", "en")))

    def test_share_dialog_name_stays_visible_in_both_steps(self):
        page = _get("/", "en")
        dialog = page.split('<dialog id="ai-share-dialog" aria-labelledby="ai-share-heading">', 1)[1].split("</dialog>", 1)[0]
        self.assertLess(dialog.index('id="ai-share-heading"'), dialog.index('<form id="ai-share-form">'))
        self.assertIn('<form id="ai-share-confirm-form" hidden>', dialog)

    def test_ai_thread_time_controls_and_local_source_prefill(self):
        for locale in ("he", "en"):
            page = _get("/", locale)
            script = _get("/app.js", locale)
            self.assertIn('id="ai-thread-time" name="conversation_time" type="time"', page)
            self.assertIn('id="ai-share-time" name="conversation_time" type="time"', page)
            self.assertIn('localDateTimeInputs(result.source_first_message_at)', script)
            self.assertIn('result.warnings.includes("timestamp_unavailable")', script)

    def test_rejects_unknown_locale(self):
        with self.assertRaises(ValueError):
            Translator("fr")

    def test_html_strings_are_escaped_and_js_strings_are_literals(self):
        translator = Translator("he")
        self.assertIn("&quot;", translator.html("confirm_delete_file"))
        self.assertTrue(translator.js("confirm_delete_file").startswith('"'))


if __name__ == "__main__":
    unittest.main()
