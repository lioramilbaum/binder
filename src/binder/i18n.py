"""User-interface strings for the static pages, keyed by locale.

Only UI chrome lives here. Folder names, document text, file names and chat
messages are stored and rendered in whatever language they were written in.
"""

import html
import json
import re
from dataclasses import dataclass
from typing import Mapping

DEFAULT_LOCALE = "he"

_HEBREW: dict[str, str] = {
    "app_title": "Binder — ניהול מידע",
    "folder_title": "{name} — Binder",
    "date_locale": "he-IL",
    "brand_home": "Binder — דף הבית",
    "main_navigation": "ניווט ראשי",
    "open_sidebar": "פתיחת סרגל הצד",
    "close_sidebar": "סגירת סרגל הצד",
    # Home view.
    "workspace": "מרחב העבודה שלך",
    "home_heading": "על איזו תיקייה עובדים?",
    "home_tagline": "כל המידע החשוב, מסודר במקום אחד.",
    "new_folder": "+ תיקייה חדשה",
    "my_folders": "התיקיות שלי",
    "no_folders": "עדיין אין כאן תיקיות",
    "no_folders_hint": "צרו את התיקייה הראשונה והתחילו לאסוף מידע.",
    "folders_count_one": "תיקייה אחת",
    "folders_count_other": "{count} תיקיות",
    "no_description": "ללא תיאור",
    "updated": "עודכן {date}",
    "load_folders_failed": "לא הצלחנו לטעון את התיקיות. נסו לרענן את העמוד.",
    # Folder view.
    "back_to_folders": "→ חזרה לתיקיות",
    "folder": "תיקייה",
    "documents": "מסמכים",
    "new_document": "+ מסמך חדש",
    "export_documents": "ייצוא רשימת מסמכים",
    "no_documents": "עדיין אין מסמכים בתיקייה",
    "no_documents_hint": "כאן תופיע רשימת המסמכים שתוסיפו לתיקייה.",
    "documents_count_one": "מסמך אחד",
    "documents_count_other": "{count} מסמכים",
    "search_documents": "חיפוש בכותרת, בתקציר, בתגיות ובקבצים…",
    "search_semantic_match": "תואם לפי משמעות",
    "search_semantic_unavailable": "חיפוש סמנטי אינו זמין כרגע – מוצגות התאמות מילוניות בלבד",
    "document_type": "סוג מסמך",
    "document_type_regular": "רגיל",
    "document_type_ai": "AI",
    "tags": "תגיות",
    "tags_placeholder": "הקלידו תגית ולחצו Enter",
    "remove_tag": 'הסרת התגית "{name}"',
    "filter_by_tags": "סינון לפי תגיות",
    "edit_document": "עריכת מסמך",
    "show_whatsapp": "הצגת שיחת WhatsApp",
    "hide_whatsapp": "הסתרת שיחת WhatsApp",
    "remove_file": "הסרת קובץ",
    "confirm_delete_file": 'למחוק את הקובץ "{name}"?',
    "delete_file_failed": "מחיקת הקובץ נכשלה.",
    "show_pdf": "הצגת PDF",
    "close_pdf": "סגירת התצוגה",
    "open_pdf_new_tab": "פתיחה בכרטיסייה חדשה",
    "pdf_viewer_title": "תצוגת PDF: {name}",
    # AI conversations.
    "ai_thread_title": "כותרת השיחה",
    "ai_thread_transcript_placeholder": "הדביקו את כל הטקסט של השיחה",
    "imported_transcript_locked": "הטקסט של שיחה מיובאת לא ניתן לעריכה",
    "ai_thread_provider": "שירות AI",
    "ai_thread_provider_placeholder": "לדוגמה: ChatGPT או Claude",
    "ai_thread_date": "תאריך השיחה",
    "ai_thread_time": "שעת השיחה",
    "ai_thread_transcript": "השיחה המלאה",
    "ai_thread_summary": "הסיכום שלך",
    "save_ai_thread": "שמירת שיחה",
    "import_ai_thread": "יבוא מקישור ChatGPT",
    "share_link": "קישור שיתוף של ChatGPT",
    "share_link_hint": "היבוא שומר עותק של הטקסט הגלוי בקישור הציבורי. בדקו את התצוגה המקדימה לפני השמירה.",
    "preview_share": "תצוגה מקדימה",
    "share_messages": "{count} הודעות שיובאו",
    "share_source": "מקור: קישור שיתוף של ChatGPT",
    "share_hash": "SHA-256 של הטקסט השמור: {hash}",
    "back": "חזרה",
    "share_invalid_link": "הזינו קישור שיתוף תקין של ChatGPT.",
    "share_inaccessible_link": "לא ניתן לגשת לקישור השיתוף הציבורי.",
    "share_too_large": "השיחה גדולה מדי ליבוא.",
    "share_unsupported_format": "לא ניתן לקרוא את מבנה השיחה מהקישור. אפשר להוסיף אותה ידנית.",
    "share_preview_unavailable": "התצוגה המקדימה פגה או כבר נשמרה. צרו תצוגה חדשה.",
    "share_invalid_date": "בחרו תאריך שיחה תקין.",
    "share_invalid_time": "בחרו שעת שיחה תקינה.",
    "share_folder_not_found": "התיקייה לא נמצאה.",
    "share_invalid_request": "הבקשה אינה תקינה.",
    "share_non_text_omitted": "חלק מהתוכן שאינו טקסט, כגון תמונות או קבצים, לא יובא. בדקו את השיחה המקורית.",
    "share_non_visible_omitted": "הודעות מוסתרות או פנימיות של ChatGPT הושמטו מהשיחה המיובאת.",
    "share_timestamp_unavailable": "לא נמצא זמן תקין להודעה הראשונה. יש להזין תאריך שיחה; שעה היא לא חובה.",
    # Folder dialog.
    "new_folder_eyebrow": "תיקייה חדשה",
    "folder_dialog_title": "מה נרצה לעקוב?",
    "close": "סגירה",
    "folder_name": "שם התיקייה",
    "folder_name_placeholder": "לדוגמה: ועד הבית",
    "short_description": "תיאור קצר",
    "optional": "(לא חובה)",
    "folder_description_placeholder": "כמה מילים שיעזרו לזכור במה מדובר",
    "cancel": "ביטול",
    "create_folder": "יצירת תיקייה",
    # Document dialog.
    "new_document_eyebrow": "מסמך חדש",
    "new_document_title": "הוספת מידע לתיקייה",
    "edit_document_eyebrow": "עריכת מסמך",
    "edit_document_title": "עדכון פרטי המסמך",
    "date": "תאריך",
    "time": "שעה",
    "document_name": "שם המסמך",
    "document_name_placeholder": "לדוגמה: שיחת WhatsApp עם דני",
    "document_description_placeholder": "הקשר או הערה על המסמך",
    "content": "תוכן",
    "content_placeholder": "אפשר להקליד או להדביק כאן שיחה, מסמך או כל תוכן אחר",
    "summary": "סיכום מחשבות ורעיונות",
    "summary_placeholder": "סיכום אישי של מחשבות, רעיונות או מסקנות לגבי המסמך",
    "files": "קבצים",
    "files_optional": "(לא חובה, עד 50MB יחד)",
    "drop_files": "גררו קבצים לכאן",
    "drop_files_hint": "או לחצו לבחירת תמונות, קבצים או ייצוא WhatsApp",
    "google_drive_import": "יבוא מ-Google Drive",
    "paste_hint": "אפשר גם להדביק תמונה ישירות בשדה התוכן.",
    "save_document": "שמירת מסמך",
    "save_changes": "שמירת שינויים",
    "saving": "שומר…",
    "save_failed": "השמירה נכשלה. יש להפעיל מחדש את השרת ולנסות שוב.",
    "connect_failed": "לא ניתן להתחבר לשרת. ודאו שהשרת פועל ונסו שוב.",
    # Document links.
    "add_link": "הוספת קישור",
    "link_dialog_title": "קישור למסמך אחר",
    "link_type": "סוג קישור",
    "choose_link_type": "בחרו סוג קישור",
    "no_link_types_hint": "עדיין אין סוגי קישור. הוסיפו סוג חדש למטה.",
    "new_link_type_placeholder": "סוג קישור חדש, למשל: תגובה ל",
    "add_link_type": "הוספת סוג",
    "link_target": "מסמך מקושר",
    "choose_link_target": "בחרו מסמך",
    "save_link": "שמירת קישור",
    "links_outgoing": "מקשר אל",
    "links_incoming": "מקושר מ-",
    "link_in_folder": "ב{folder}",
    "remove_link": "הסרת הקישור ל\"{name}\"",
    "confirm_remove_link": "להסיר את הקישור ל\"{name}\"?",
    "remove_link_failed": "הסרת הקישור נכשלה.",
    "link_error_duplicate": "המסמכים כבר מקושרים בסוג הזה.",
    "link_error_self": "מסמך לא יכול לקשר לעצמו.",
    "link_error_target_not_found": "המסמך המקושר לא נמצא.",
    "link_error_type_not_found": "סוג הקישור לא נמצא.",
    "link_error_invalid_type": "יש להזין סוג קישור של עד 50 תווים.",
    "link_error_document_not_found": "המסמך לא נמצא.",
    # Uploads and Google Drive.
    "upload_failed": "העלאת הקבצים נכשלה.",
    "script_load_failed": "טעינת {src} נכשלה.",
    "google_login_failed": "ההתחברות ל-Google Drive נכשלה.",
    "google_download_failed": 'הורדת "{name}" מ-Google Drive נכשלה.',
    "downloading_files": "מוריד קבצים…",
    "google_import_failed": "יבוא מ-Google Drive נכשל.",
}

_ENGLISH: dict[str, str] = {
    "app_title": "Binder — information manager",
    "folder_title": "{name} — Binder",
    "date_locale": "en-US",
    "brand_home": "Binder — home",
    "main_navigation": "Main navigation",
    "open_sidebar": "Open sidebar",
    "close_sidebar": "Close sidebar",
    # Home view.
    "workspace": "Your workspace",
    "home_heading": "Which folder are you working on?",
    "home_tagline": "All the important information, in one place.",
    "new_folder": "+ New folder",
    "my_folders": "My folders",
    "no_folders": "No folders here yet",
    "no_folders_hint": "Create your first folder and start collecting information.",
    "folders_count_one": "1 folder",
    "folders_count_other": "{count} folders",
    "no_description": "No description",
    "updated": "Updated {date}",
    "load_folders_failed": "We couldn't load the folders. Try refreshing the page.",
    # Folder view.
    "back_to_folders": "← Back to folders",
    "folder": "Folder",
    "documents": "Documents",
    "new_document": "+ New document",
    "export_documents": "Export list",
    "no_documents": "No documents in this folder yet",
    "no_documents_hint": "The documents you add to the folder will be listed here.",
    "documents_count_one": "1 document",
    "documents_count_other": "{count} documents",
    "search_documents": "Search titles, summaries, tags and attachments…",
    "search_semantic_match": "Related by meaning",
    "search_semantic_unavailable": "Semantic search unavailable – showing keyword matches only",
    "document_type": "Document type",
    "document_type_regular": "Regular",
    "document_type_ai": "AI",
    "tags": "Tags",
    "tags_placeholder": "Type a tag and press Enter",
    "remove_tag": 'Remove tag "{name}"',
    "filter_by_tags": "Filter by tags",
    "edit_document": "Edit document",
    "show_whatsapp": "Show WhatsApp chat",
    "hide_whatsapp": "Hide WhatsApp chat",
    "remove_file": "Remove file",
    "confirm_delete_file": 'Delete the file "{name}"?',
    "delete_file_failed": "Deleting the file failed.",
    "show_pdf": "View PDF",
    "close_pdf": "Close viewer",
    "open_pdf_new_tab": "Open in new tab",
    "pdf_viewer_title": "PDF viewer: {name}",
    # AI conversations.
    "ai_thread_title": "Conversation title",
    "ai_thread_transcript_placeholder": "Paste the complete conversation text here",
    "imported_transcript_locked": "Imported conversation transcript cannot be edited",
    "ai_thread_provider": "AI service",
    "ai_thread_provider_placeholder": "For example: ChatGPT or Claude",
    "ai_thread_date": "Conversation date",
    "ai_thread_time": "Conversation time",
    "ai_thread_transcript": "Full conversation",
    "ai_thread_summary": "Your summary",
    "save_ai_thread": "Save conversation",
    "import_ai_thread": "Import ChatGPT share link",
    "share_link": "ChatGPT share link",
    "share_link_hint": "The import saves a snapshot of the public link's visible text. Review the preview before saving.",
    "preview_share": "Preview conversation",
    "share_messages": "{count} imported messages",
    "share_source": "Source: ChatGPT share link",
    "share_hash": "Saved text SHA-256: {hash}",
    "back": "Back",
    "share_invalid_link": "Enter a valid ChatGPT share link.",
    "share_inaccessible_link": "This public share link could not be reached.",
    "share_too_large": "The conversation is too large to import.",
    "share_unsupported_format": "The conversation format could not be read. You can add it manually.",
    "share_preview_unavailable": "The preview expired or was already saved. Preview the link again.",
    "share_invalid_date": "Choose a valid conversation date.",
    "share_invalid_time": "Choose a valid conversation time.",
    "share_folder_not_found": "The folder was not found.",
    "share_invalid_request": "The request is invalid.",
    "share_non_text_omitted": "Some non-text content, such as images or files, was omitted. Check the original conversation.",
    "share_non_visible_omitted": "Hidden or internal ChatGPT messages were excluded from the imported conversation.",
    "share_timestamp_unavailable": "The first message has no usable timestamp. Enter a conversation date; time is optional.",
    # Folder dialog.
    "new_folder_eyebrow": "New folder",
    "folder_dialog_title": "What would you like to track?",
    "close": "Close",
    "folder_name": "Folder name",
    "folder_name_placeholder": "For example: Building committee",
    "short_description": "Short description",
    "optional": "(optional)",
    "folder_description_placeholder": "A few words to help you remember what this is about",
    "cancel": "Cancel",
    "create_folder": "Create folder",
    # Document dialog.
    "new_document_eyebrow": "New document",
    "new_document_title": "Add information to the folder",
    "edit_document_eyebrow": "Edit document",
    "edit_document_title": "Update the document details",
    "date": "Date",
    "time": "Time",
    "document_name": "Document name",
    "document_name_placeholder": "For example: WhatsApp chat with Danny",
    "document_description_placeholder": "Context or a note about the document",
    "content": "Content",
    "content_placeholder": "Type or paste a conversation, document or any other content here",
    "summary": "Summary of thoughts and ideas",
    "summary_placeholder": "A personal summary of thoughts, ideas or conclusions about the document",
    "files": "Files",
    "files_optional": "(optional, up to 50MB in total)",
    "drop_files": "Drag files here",
    "drop_files_hint": "or click to choose images, files or a WhatsApp export",
    "google_drive_import": "Import from Google Drive",
    "paste_hint": "You can also paste an image directly into the content field.",
    "save_document": "Save document",
    "save_changes": "Save changes",
    "saving": "Saving…",
    "save_failed": "Saving failed. Restart the server and try again.",
    "connect_failed": "Unable to reach the server. Make sure it is running and try again.",
    # Document links.
    "add_link": "Add link",
    "link_dialog_title": "Link to another document",
    "link_type": "Link type",
    "choose_link_type": "Choose a link type",
    "no_link_types_hint": "No link types yet. Add one below.",
    "new_link_type_placeholder": "New link type, e.g. Reply to",
    "add_link_type": "Add type",
    "link_target": "Linked document",
    "choose_link_target": "Choose a document",
    "save_link": "Save link",
    "links_outgoing": "Links to",
    "links_incoming": "Linked from",
    "link_in_folder": "in {folder}",
    "remove_link": "Remove link to \"{name}\"",
    "confirm_remove_link": "Remove the link to \"{name}\"?",
    "remove_link_failed": "Removing the link failed.",
    "link_error_duplicate": "These documents are already linked with this type.",
    "link_error_self": "A document cannot link to itself.",
    "link_error_target_not_found": "The linked document was not found.",
    "link_error_type_not_found": "The link type was not found.",
    "link_error_invalid_type": "Enter a link type of up to 50 characters.",
    "link_error_document_not_found": "The document was not found.",
    # Uploads and Google Drive.
    "upload_failed": "Uploading the files failed.",
    "script_load_failed": "Loading {src} failed.",
    "google_login_failed": "Signing in to Google Drive failed.",
    "google_download_failed": 'Downloading "{name}" from Google Drive failed.',
    "downloading_files": "Downloading files…",
    "google_import_failed": "Importing from Google Drive failed.",
}


@dataclass(frozen=True)
class _Locale:
    direction: str
    strings: Mapping[str, str]


LOCALES: dict[str, _Locale] = {
    "he": _Locale(direction="rtl", strings=_HEBREW),
    "en": _Locale(direction="ltr", strings=_ENGLISH),
}

# Marks a string in a static file: @@key@@ becomes the string for the locale.
_MARKER = re.compile(r"@@([a-z_]+)@@")


class Translator:
    """Look up UI strings for one locale, escaped for the context they land in."""

    def __init__(self, locale: str = DEFAULT_LOCALE) -> None:
        if locale not in LOCALES:
            raise ValueError(
                f"Unsupported locale {locale!r}; expected one of {sorted(LOCALES)}."
            )
        self.lang = locale
        self.direction = LOCALES[locale].direction
        self._strings = LOCALES[locale].strings

    def text(self, key: str) -> str:
        """Return the raw string; raises KeyError for an unknown key."""

        return self._strings[key]

    def html(self, key: str) -> str:
        """Return the string escaped for HTML text and attribute values."""

        return html.escape(self.text(key), quote=True)

    def js(self, key: str) -> str:
        """Return the string as a JavaScript string literal, safe inside <script>."""

        return json.dumps(self.text(key), ensure_ascii=False).replace("</", "<\\/")

    def fill_html(self, page: str) -> str:
        """Replace @@key@@ markers in a page with HTML-escaped strings.

        @@lang@@ and @@dir@@ resolve to the locale code and text direction.
        """

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key == "lang":
                return self.lang
            if key == "dir":
                return self.direction
            return self.html(key)

        return _MARKER.sub(replace, page)

    def fill_script(self, script: str) -> str:
        """Replace every @@key@@ marker in a script with its string literal."""

        return _MARKER.sub(lambda match: self.js(match.group(1)), script)
