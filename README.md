# Binder

A small MVP for managing information folders. The user interface is in Hebrew
and supports right-to-left text.

## Running the application

Python 3.11 or later is required, along with [`uv`](https://docs.astral.sh/uv/)
for dependency management. Folders, documents, and attachments are stored in
PostgreSQL.

The application runs in Docker Compose, which starts both the app and a
PostgreSQL database and applies schema migrations automatically:

```bash
make up
```

Then open [http://localhost:8000](http://localhost:8000) in a browser. `make up`
rebuilds the image and waits for both services to report healthy. `make down`
stops them and keeps the database; `make restart` does both. Set `APP_PORT` in
`.env` to change the web port. Compose reads `.env` automatically, so the
`BINDER_*` settings below can be set there too.

To run outside Docker (for development), start a PostgreSQL instance yourself,
set `DATABASE_URL`, apply migrations, then start the server:

```bash
uv sync
export DATABASE_URL="postgresql://user:password@localhost:5432/binder"
uv run alembic upgrade head
uv run binder-server
```

Attachment bytes are stored as PostgreSQL large objects rather than plain
column values, since a single `bytea`/`text` value is capped at 1 GiB and
attachments here can exceed that. A database trigger (`lo_manage`, from the
`lo` extension) automatically deletes the underlying large object whenever
the row referencing it is updated or removed, so attachment cleanup needs no
extra code.

Uploads are streamed into the database and may be up to 2 GB by default. Set
`BINDER_MAX_UPLOAD_MB` to change the maximum request size, for example:

```bash
BINDER_MAX_UPLOAD_MB=4096 uv run binder-server
```

The limit applies to the combined size of all files selected in one upload.

WhatsApp chat exports in `.txt` or `.zip` format are detected automatically
when attached to a document. Binder preserves the original export and renders
the parsed messages as a chat conversation. Android and iPhone export formats
can vary by locale, so the original file remains the authoritative source.

### Importing data from the old file-based storage

Earlier versions of Binder stored folders in `data/folders.json` and documents
under `data/documents/<folder-id>/`. If you have such a `data/` directory,
import it into PostgreSQL with:

```bash
uv run binder-import-json --data-dir data
```

The import is safe to re-run: folders that already exist in the database (by
id) are skipped.

## Interface language

The page is Hebrew by default (`<html lang="he" dir="rtl">`). All interface
text lives in `src/binder/i18n.py`, with Hebrew and English tables that must
define the same keys. `static/index.html` and `static/app.js` reference strings
as `@@key@@` markers, which the server fills in when it serves them, so a
missing key fails the request rather than showing up in the browser. Set
`BINDER_LOCALE=en` to serve the English, left-to-right page. To add a language,
add a table and a `LOCALES` entry; `tests/test_i18n.py` checks that every key
and placeholder is present.

Only interface text is translated. Folder names, descriptions, document text,
file names and chat messages are shown exactly as written, and each one picks
its own text direction. Server API error messages are not part of the
translated interface.

## Importing files from Google Drive

The document upload dialog can import files directly from Google Drive
instead of requiring a local download first. This feature is off by default,
behind a toggle, and needs Google API credentials once you turn it on:

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a
   project (or reuse one) and enable the **Google Drive API** and the
   **Google Picker API**.
2. Create an **OAuth 2.0 Client ID** of type "Web application" and add the
   URL you use to reach Binder (e.g. `http://localhost:8000`) to
   **Authorized JavaScript origins**.
3. Create an **API key**, and restrict it to the Google Picker API.
4. Start the server with the toggle and both values set:

```bash
BINDER_GOOGLE_DRIVE_IMPORT_ENABLED=1 \
BINDER_GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com" \
BINDER_GOOGLE_API_KEY="your-api-key" \
uv run binder-server
```

The "יבוא מ-Google Drive" button appears in the document dialog only when
`BINDER_GOOGLE_DRIVE_IMPORT_ENABLED` is set to `1`, `true`, or `yes` and both
credentials are provided; leaving it unset (the default) hides the button and
ignores any credentials, so the feature stays fully off until you opt in.
Picking a file opens Google's file picker, asks you to sign in and grant
read-only Drive access, then downloads the selected file(s) into the same
upload flow used for local files. Google Docs, Sheets, and Slides files are
exported as PDF; every other file type is downloaded as-is.

## Duplicate attachments

Every attachment is stored with a SHA-256 hash. Uploading a file identical to
one already stored (in any document or folder) reuses the existing stored copy
instead of writing another. Deleting one of several sharing attachments keeps
the bytes until the last one is removed.

`GET /api/duplicates` lists groups of identical attachments and where they
appear. For attachments uploaded before hashing existed, run inside the app
container while it is up:

```sh
docker compose exec app binder-dedupe backfill        # hash existing attachments
docker compose exec app binder-dedupe report          # list duplicate groups
docker compose exec app binder-dedupe merge           # dry run: space that would be reclaimed
docker compose exec app binder-dedupe merge --apply   # share one copy per group (run make backup first)
```

## Tags

Documents can be tagged with free-text labels to organize and filter content.
Tags are global across all folders and matched case-insensitively. When you
create or edit a document, type tag names into the tags input field and press
Enter or comma to add them; duplicates (ignoring case and spacing) are
automatically deduplicated and the first spelling is preserved.

The tag editor accepts up to 20 tags per document, each up to 50 characters
long. Tags are created in the same transaction as the document; if the document save fails, no new tags are persisted.

In the folder view, tags appear as small chips below each document's date.
The filter bar at the top of the documents list shows all distinct tags in the
current folder with their document counts; click a tag chip to toggle filtering
by that tag. Multiple tags can be selected to show documents tagged with any of
them.

`GET /api/tags` returns a sorted list of all tags and their document counts,
used to populate the tag suggestions autocomplete.

## Running the tests

```bash
uv run pytest
```

Folder/document tests run against an in-memory SQLite database. Attachments
are stored as PostgreSQL large objects (see below), so attachment tests need
a real, disposable Postgres database and are skipped unless
`TEST_DATABASE_URL` is set:

```bash
TEST_DATABASE_URL="postgresql+psycopg://binder:binder_dev@127.0.0.1:5433/binder_test" uv run pytest
```

Point it at a throwaway database, never a database with real data — its
tables are dropped and recreated before every test.

## Data location and backups

PostgreSQL's data folder is a local bind mount (`./.postgres-data`, git-ignored),
not a Docker volume, so pruning Docker storage cannot delete it. The image is
pinned to PostgreSQL 18.6 and mounted at `/var/lib/postgresql`; major-version
upgrades require a database migration, not just changing the tag.

`make backup` writes a timestamped dump (`.private-backups/binder-YYYYmmdd-HHMMSS.dump`,
readable only by you, git-ignored) and keeps the newest 30; change that with
`make backup KEEP=10`. Run it before upgrades and anything destructive, and copy
the folder somewhere off this machine now and then. To restore, load a dump into
a separate database first, verify it, then intentionally switch `DATABASE_URL`:

```sh
docker compose exec -T postgres createdb -U binder binder_restore
docker compose exec -T postgres pg_restore -U binder -d binder_restore --no-owner < .private-backups/binder-<timestamp>.dump
```
