# Binder

A small MVP for managing information folders. The user interface is in Hebrew
and supports right-to-left text.

## Running the application

Python 3.11 or later is required, along with [`uv`](https://docs.astral.sh/uv/)
for dependency management. Folders, documents, and attachments are stored in
PostgreSQL.

The easiest way to run everything locally is Docker Compose, which starts both
the app and a PostgreSQL database and applies schema migrations automatically:

```bash
docker compose up
```

Then open [http://localhost:8000](http://localhost:8000) in a browser.

To run outside Docker, start a PostgreSQL instance yourself, set
`DATABASE_URL`, apply migrations, then start the server:

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
