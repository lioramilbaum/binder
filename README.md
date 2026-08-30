# Binder

A small MVP for managing information folders. The user interface is in Hebrew
and supports right-to-left text.

## Running the application

Python 3.9 or later is required. No external packages need to be installed.

```bash
python3 -m src.binder.app
```

Then open [http://localhost:8000](http://localhost:8000) in a browser.

By default, folders are stored in `data/folders.json`. The storage location can
be changed with the `BINDER_DATA_DIR` environment variable—for example, to use
a directory synchronized with Google Drive:

```bash
BINDER_DATA_DIR="/path/to/Google Drive/Binder" python3 -m src.binder.app
```

Each document is stored as a separate JSON file under
`data/documents/<folder-id>/`, with its attachments stored alongside it. The
entire `data/` directory is ignored by Git, so personal folders, documents, and
uploaded files are never included in commits.

## Running the tests

```bash
python3 -m unittest discover -s tests
```
