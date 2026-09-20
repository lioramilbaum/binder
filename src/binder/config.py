import os
from pathlib import Path

from .i18n import DEFAULT_LOCALE, LOCALES


PROJECT_ROOT = Path(__file__).parents[2]
STATIC_DIR = Path(os.environ.get("BINDER_STATIC_DIR", PROJECT_ROOT / "static"))


def _max_upload_size() -> int:
    value = os.environ.get("BINDER_MAX_UPLOAD_MB", "2048")
    try:
        megabytes = int(value)
    except ValueError:
        megabytes = 2048
    return max(megabytes, 1) * 1024 * 1024


MAX_UPLOAD_SIZE = _max_upload_size()
GOOGLE_CLIENT_ID = os.environ.get("BINDER_GOOGLE_CLIENT_ID", "")
GOOGLE_API_KEY = os.environ.get("BINDER_GOOGLE_API_KEY", "")
GOOGLE_DRIVE_IMPORT_ENABLED = os.environ.get(
    "BINDER_GOOGLE_DRIVE_IMPORT_ENABLED", ""
).strip().lower() in {"1", "true", "yes"}


LOCALE = os.environ.get("BINDER_LOCALE", DEFAULT_LOCALE).strip() or DEFAULT_LOCALE
if LOCALE not in LOCALES:
    raise ValueError(
        f"Unsupported BINDER_LOCALE {LOCALE!r}; expected one of {sorted(LOCALES)}."
    )
