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
            # Check for document type select with ai option
            self.assertIn('<select id="document-type" name="type">', page)
            self.assertIn('value="regular"', page)
            self.assertIn('value="ai"', page)
            # Check for AI-specific fields with proper visibility
            self.assertIn('data-document-type="ai"', page)
            self.assertIn('id="document-provider"', page)
            self.assertIn('name="provider"', page)
            # Check for share dialog time fields
            self.assertIn('id="ai-share-time" name="conversation_time" type="time"', page)
            # Check script functions
            self.assertIn('localDateTimeInputs(result.source_first_message_at)', script)
            self.assertIn('result.warnings.includes("timestamp_unavailable")', script)
            self.assertIn('function applyDocumentType(', script)
            # Verify old AI-thread specific elements are absent
            self.assertNotIn('id="ai-thread-dialog"', page)
            self.assertNotIn('ai-threads-section', page)

    def test_rejects_unknown_locale(self):
        with self.assertRaises(ValueError):
            Translator("fr")

    def test_html_strings_are_escaped_and_js_strings_are_literals(self):
        translator = Translator("he")
        self.assertIn("&quot;", translator.html("confirm_delete_file"))
        self.assertTrue(translator.js("confirm_delete_file").startswith('"'))

    def test_import_share_button_lives_in_ai_document_fields(self):
        for locale in ("he", "en"):
            page = _get("/", locale)
            script = _get("/app.js", locale)

            # Markup assertions
            # Extract the documents section and verify button is NOT there
            documents_section = page.split('<section class="documents-section"', 1)[1].split("</section>", 1)[0]
            self.assertNotIn('id="import-ai-thread-button"', documents_section)

            # Extract the document dialog and then the AI fieldset
            dialog = page.split('<dialog id="document-dialog">', 1)[1].split("</dialog>", 1)[0]
            ai_fieldset = dialog.split('<fieldset class="document-type-fields" data-document-type="ai"', 1)[1].split("</fieldset>", 1)[0]
            self.assertIn('id="import-ai-thread-button"', ai_fieldset)

            # Verify button appears exactly once
            self.assertEqual(page.count('id="import-ai-thread-button"'), 1)

            # Script assertions
            # Find the click listener and verify documentDialog.close() appears before aiShareDialog.showModal()
            self.assertIn('importAiThreadButton.addEventListener("click"', script)
            click_start = script.index('importAiThreadButton.addEventListener("click"')
            click_end = script.index("\n});", click_start) + 4
            click_handler = script[click_start:click_end]
            self.assertIn("documentDialog.close();", click_handler)
            self.assertIn("aiShareDialog.showModal();", click_handler)
            self.assertLess(click_handler.index("documentDialog.close();"),
                           click_handler.index("aiShareDialog.showModal();"))

            # Verify hidden property is set in both cases
            self.assertIn("importAiThreadButton.hidden = true", script)
            self.assertIn("importAiThreadButton.hidden = false", script)

    def test_link_dialog_and_backlinks_markup(self):
        for locale in ("he", "en"):
            page = _get("/", locale)
            script = _get("/app.js", locale)

            # Check for link dialog element
            self.assertIn('<dialog id="link-dialog" aria-labelledby="link-dialog-heading">', page)

            # Check for link-type-select with placeholder option
            self.assertIn('id="link-type-select"', page)
            link_dialog_section = page.split('<dialog id="link-dialog"', 1)[1].split("</dialog>", 1)[0]
            # The placeholder option should be there with an empty value
            self.assertIn('<option value="">', link_dialog_section)

            # Check for other link dialog elements
            self.assertIn('id="new-link-type-input"', page)
            self.assertIn('id="link-target-select"', page)

            # Check for link functions in app.js
            self.assertIn("/api/link-types", script)
            self.assertIn("function renderDocumentLinks(", script)
            self.assertIn("function revealDocument(", script)

    def test_pdf_attachments_open_in_inline_viewer(self):
        styles = _get("/styles.css", "he")
        self.assertIn(".pdf-frame", styles)
        for locale in ("he", "en"):
            script = _get("/app.js", locale)
            self.assertIn("function renderPdfViewer(", script)
            self.assertIn('attachment.content_type === "application/pdf"', script)
            self.assertIn("event.preventDefault()", script)
            self.assertIn("aria-expanded", script)
            self.assertIn('document.createElement("iframe")', script)
            self.assertIn("pdf-frame", script)
            start = script.index("function renderPdfViewer(")
            body = script[start:script.index("\n}\n", start)]
            self.assertNotIn("sandbox", body)

    def test_folder_view_exports_document_titles_and_dates(self):
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        self.assertIn("@media print", styles)
        self.assertIn("body > :not(#print-export) { display: none !important; }", styles)
        for locale in ("he", "en"):
            page = _get("/", locale)
            script = _get("/app.js", locale)
            actions = page.split('<section class="documents-section"', 1)[1].split('<div class="search-bar">', 1)[0]
            self.assertIn('id="export-documents-button"', actions)
            self.assertEqual(page.count('id="export-documents-button"'), 1)
            export = page.split('<section id="print-export"', 1)[1].split("</section>", 1)[0]
            self.assertEqual(export.count("<th>"), 2)
            self.assertIn('id="print-export-rows"', export)
            self.assertIn('exportDocumentsButton.addEventListener("click"', script)
            self.assertIn("window.print();", script)
            start = script.index("function renderPrintExport(")
            body = script[start:script.index("\n}\n", start)]
            self.assertIn("documentItem.name", body)
            self.assertIn("documentItem.date", body)
            for excluded in ("content", "description", "summary", "files", "attachments", "tags", "time", "provider"):
                self.assertNotIn(f"documentItem.{excluded}", body)
            self.assertIn("exportDocumentsButton.disabled = currentDocuments.length === 0", script)

    def test_sidebar_navigation_markup(self):
        """Sidebar shell elements are present in the rendered page."""
        for locale in ("he", "en"):
            page = _get("/", locale)
            self.assertIn('<aside class="sidebar" id="sidebar"', page)
            self.assertIn('id="sidebar-folders"', page)
            self.assertIn('id="sidebar-toggle"', page)
            self.assertIn('aria-controls="sidebar"', page)
            self.assertIn('aria-expanded="false"', page)
            # new-folder-button must appear exactly once and inside the sidebar
            self.assertEqual(page.count('id="new-folder-button"'), 1)
            aside_start = page.index('<aside class="sidebar" id="sidebar"')
            aside_end = page.index("</aside>", aside_start)
            self.assertIn('id="new-folder-button"', page[aside_start:aside_end])
            # print-export comes after main-column (outside the shell)
            self.assertGreater(page.index('<section id="print-export"'), page.index('<div class="main-column">'))

    def test_sidebar_script_behaviour(self):
        """app.js contains the required sidebar functions and behaviour."""
        script = _get("/app.js", "en")
        self.assertIn("function renderSidebarFolders(", script)
        self.assertIn("function setActiveSidebarFolder(", script)
        self.assertIn("function setSidebarOpen(", script)
        self.assertIn('"sidebar-open"', script)
        self.assertIn("aria-current", script)
        # renderSidebarFolders uses textContent, not innerHTML
        start = script.index("function renderSidebarFolders(")
        end = script.index("function setActiveSidebarFolder(", start)
        body = script[start:end]
        self.assertIn("textContent", body)
        self.assertNotIn("innerHTML", body)

    def test_dark_theme_styles(self):
        """styles.css uses the dark theme tokens and safe properties."""
        styles = _get("/styles.css", "en")
        self.assertIn("color-scheme: dark", styles)
        self.assertIn("--sidebar-bg", styles)
        self.assertIn("prefers-reduced-motion", styles)
        self.assertIn('[dir="rtl"]', styles)
        self.assertIn("body.sidebar-open .sidebar", styles)
        # no physical margin-left or margin-right in CSS rules
        self.assertIsNone(re.search(r"margin-(left|right)\s*:", styles))
        # print block includes black text
        self.assertIn("@media print", styles)
        print_start = styles.index("@media print")
        print_block = styles[print_start:]
        self.assertIn("color: #000", print_block)

    def test_calendar_week_start_per_locale(self):
        self.assertEqual(Translator("he").text("calendar_week_start"), "0")
        self.assertEqual(Translator("en").text("calendar_week_start"), "1")

    def test_calendar_panel_markup(self):
        for locale in ("he", "en"):
            with self.subTest(locale=locale):
                page = _get("/", locale)
                self.assertIn('id="calendar-panel"', page)
                self.assertIn('role="grid"', page)
                self.assertIn('aria-labelledby="calendar-month-label"', page)
                self.assertIn('id="date-filter-status"', page)

    def test_calendar_filter_wiring(self):
        script = _get("/app.js", "en")
        # applyDocumentFilters must call renderCalendar and filterDocumentsByDate
        start = script.index("function applyDocumentFilters(")
        body = script[start:script.index("\n}\n", start)]
        self.assertIn("renderCalendar(", body)
        self.assertIn("filterDocumentsByDate(", body)
        # openFolder must clear selectedDate and show calendarPanel
        start = script.index("function openFolder(")
        body = script[start:script.index("\n}\n", start)]
        self.assertIn("selectedDate = null", body)
        self.assertIn("calendarPanel.hidden = false", body)
        # showHome must hide calendarPanel
        start = script.index("function showHome(")
        body = script[start:script.index("\n}\n", start)]
        self.assertIn("calendarPanel.hidden = true", body)
        # renderCalendar must not use innerHTML
        start = script.index("function renderCalendar(")
        body = script[start:script.index("\n}\n", start)]
        self.assertNotIn("innerHTML", body)
        self.assertIn("aria-pressed", body)
        # documentDateKey must use T00:00:00
        start = script.index("function documentDateKey(")
        body = script[start:script.index("\n}\n", start)]
        self.assertIn("T00:00:00", body)

    def test_calendar_styles(self):
        styles = _get("/styles.css", "en")
        self.assertIn(".calendar-dot", styles)
        self.assertIn('[aria-pressed="true"]', styles)
        self.assertIn('[dir="rtl"] .calendar-nav-icon', styles)

    def test_document_delete_button_wiring(self):
        for locale in ("he", "en"):
            with self.subTest(locale=locale):
                script = _get("/app.js", locale)
                start = script.index("function renderDocuments(")
                body = script[start:script.index("\n}\n", start)]
                self.assertIn("danger-button", body)
                self.assertIn("confirm(format(", body)
                self.assertIn('method: "DELETE"', body)
                self.assertIn("openFolder(currentFolderId, false)", body)
                self.assertIn(Translator(locale).js("delete_document"), body)


if __name__ == "__main__":
    unittest.main()
