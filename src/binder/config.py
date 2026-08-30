import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[2]
DATA_DIR = Path(os.environ.get("BINDER_DATA_DIR", PROJECT_ROOT / "data"))
STORE_PATH = DATA_DIR / "folders.json"
STATIC_DIR = PROJECT_ROOT / "static"
