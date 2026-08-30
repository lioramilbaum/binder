import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[2]
DATA_DIR = Path(os.environ.get("BINDER_DATA_DIR", PROJECT_ROOT / "data"))
STORE_PATH = DATA_DIR / "folders.json"
STATIC_DIR = PROJECT_ROOT / "static"


def _max_upload_size() -> int:
    value = os.environ.get("BINDER_MAX_UPLOAD_MB", "2048")
    try:
        megabytes = int(value)
    except ValueError:
        megabytes = 2048
    return max(megabytes, 1) * 1024 * 1024


MAX_UPLOAD_SIZE = _max_upload_size()
