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

Uploads are streamed to disk and may be up to 2 GB by default. Set
`BINDER_MAX_UPLOAD_MB` to change the maximum request size, for example:

```bash
BINDER_MAX_UPLOAD_MB=4096 python3 -m src.binder.app
```

The limit applies to the combined size of all files selected in one upload.

Each document is stored as a separate JSON file under
`data/documents/<folder-id>/`, with its attachments stored alongside it. The
entire `data/` directory is ignored by Git, so personal folders, documents, and
uploaded files are never included in commits.

WhatsApp chat exports in `.txt` or `.zip` format are detected automatically
when attached to a document. Binder preserves the original export and renders
the parsed messages as a chat conversation. Android and iPhone export formats
can vary by locale, so the original file remains the authoritative source.

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
python3 -m src.binder.app
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
python3 -m unittest discover -s tests
```
