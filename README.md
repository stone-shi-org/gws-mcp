# Google Workspace MCP Server

An official Model Context Protocol (MCP) server that provides access to various Google Workspace and related APIs:
*   **Google Drive** (Listing, searching, uploading, and downloading with caching)
*   **Google Docs** (Reading text, creating docs, appending content)
*   **Google Sheets** (Reading cell ranges, updating cell ranges, creating sheets)
*   **Google Tasks** (Listing task lists, listing tasks, creating, and completing tasks)
*   **Google Maps** (Text searching, routing directions)
*   **Google News** (Free RSS feed news headlines)

It features multi-profile token-based routing, letting different client instances/tokens interact with isolated Google accounts and configurations securely.

---

## Workspace & Profile Structure

All settings, Google client secrets, access tokens, and temporary files (like cached downloads) are kept within individual profile directories under the `profiles/` directory:

```
profiles/
├── <profile_name>/
│   ├── .env                       # Environment settings containing PROFILE_TOKEN
│   ├── google_cli_client.json      # Client Secrets file (from Google Developer Console)
│   ├── google_calendar_token.json  # Generated Google OAuth token file
│   └── cache/                     # Temporary Drive files cache folder
```

---

## Prerequisites

1.  **Google Cloud Project**: Go to the [Google Cloud Console](https://console.cloud.google.com/), create a project, and enable the following APIs:
    *   Google Drive API
    *   Google Docs API
    *   Google Sheets API
    *   Google Tasks API
2.  **OAuth Credentials**:
    *   Configure the OAuth Consent Screen (external, adding your test email address).
    *   Go to **Credentials** -> **Create Credentials** -> **OAuth client ID**.
    *   Select **Desktop App** as the Application type.
    *   Download the JSON file, rename it to `google_cli_client.json`, and place it in your target profile folder (e.g. `profiles/stone/google_cli_client.json`).

---

## Setup & Configuration

### 1. Initialize Virtual Environment and Dependencies
If running locally:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt # Or install: mcp fastapi uvicorn google-api-python-client google-auth-httplib2 google-auth-oauthlib starlette python-dotenv httpx
```

### 2. Generate a Profile and Access Token
Use the CLI to initialize a profile folder and generate a secure access token:
```bash
python3 cli.py generate-token --profile stone
```
This generates a secure 32-character hex token and saves it as `PROFILE_TOKEN` inside `profiles/stone/.env`.

### 3. Setup Client Secrets
Place your downloaded client secrets file at:
`profiles/stone/google_cli_client.json`

### 4. Authorize Google APIs (OAuth Flow)
To authenticate the profile with Google, run the OAuth flow:

#### Option A: Local Desktop (with Web Browser)
```bash
python3 cli.py oauth --profile stone
```
This starts a local web server to handle the redirect. A browser window will open automatically for you to consent and authenticate.

#### Option B: Headless Server / Docker (CLI Only)
If you are running the server on a remote VM, VPS, or inside a headless Docker environment:
```bash
python3 cli.py oauth --profile stone --headless
```
1.  It will print a Google authorization URL. Copy and open it in your local web browser.
2.  Complete the sign-in and consent.
3.  Your browser will redirect to a address starting with `http://localhost:8080/?code=...` (which might fail to load / show a page error).
4.  Copy the **entire URL** from your browser's address bar.
5.  Paste that URL back into the terminal prompt. The script will exchange the code and save the token file automatically!

---

## Running the MCP Server

### Method A: Docker Compose (Recommended)
You can build and start the server using the provided `docker-compose.yml`:
```bash
docker compose up --build
```
This runs the server on port `8000` and mounts the local `profiles/` directory, ensuring all tokens and cache are persisted.

### Method B: Local Running
```bash
source venv/bin/activate
python3 mcp_server.py
```
The server will start listening on `http://0.0.0.0:8000`.

---

## Connecting to the Server

The server supports both **Streamable HTTP** (recommended for modern MCP clients) and **HTTP+SSE** transports running simultaneously on the same port.

### 1. Streamable HTTP Transport (Recommended)

Streamable HTTP exposes a unified endpoint for session initialization, bidirectional messaging, and tool invocations:

*   **Endpoint**: `/mcp`
*   **Authentication**: Pass the token via the `token` query parameter or as an HTTP `Authorization` header:
    *   **Bearer Header**: `Authorization: Bearer <profile_token>`
    *   **Query parameter**: `http://localhost:8000/mcp?token=<profile_token>`
*   **Session Management**: Once initialized, the server responds with an `mcp-session-id` HTTP header. Modern MCP clients automatically include `mcp-session-id: <session_id>` in subsequent requests.

### 2. Legacy HTTP+SSE Transport

For backwards compatibility with existing clients that use SSE:

*   **SSE Handshake Endpoint**: `GET /sse` (e.g. `http://localhost:8000/sse?token=<profile_token>` or `Authorization: Bearer <profile_token>`)
*   **Messages Endpoint**: `POST /messages?session_id=<session_id>`

### 3. Retrieving Cached/Exported Files

`google_drive_download_file` and `google_drive_export_file` write files to this server's own `profiles/<profile_name>/cache/` folder and return that as `local_path` — which is only meaningful if your client shares a filesystem/volume with this server. To actually fetch a file's bytes over the network (e.g. from a client running elsewhere), use the `download_path` field from either tool's response:

*   **Endpoint**: `GET /files/{cache_key}` (`cache_key` is the last path segment of `download_path`, e.g. `http://localhost:8000/files/<cache_key>?token=<profile_token>`)
*   **Authentication**: Same as `/sse` and `/mcp` — a `token` query parameter or `Authorization: Bearer <profile_token>` header.
*   Returns the raw file bytes with a `Content-Type` derived from the file's (export) mimeType and a `Content-Disposition` filename from its original Drive name.

---

## Available Tools

### Google Drive
*   `google_drive_list_files(query: str = None, page_size: int = 10)`: List files in Drive.
*   `google_drive_search_files(name: str)`: Search files by name.
*   `google_drive_upload_file(local_file_path: str, name: str = None, parent_id: str = None)`: Upload a local file.
*   `google_drive_download_file(file_id: str, use_cache: bool = True)`: Download a file to cache and returns local filepath.
    *   *Note: Files are cached locally under `profiles/<profile_name>/cache/` based on file ID, matching size, and MD5 checksum.*
    *   The response also includes a `download_path` — see [Retrieving Cached/Exported Files](#3-retrieving-cachedexported-files) to actually fetch the bytes if your client doesn't share this server's filesystem.
*   `google_drive_export_file(file_id: str, export_format: str, use_cache: bool = True)`: Export a Google-Apps-native file (Doc/Sheet/Slide/Drawing/Apps Script) to another format (e.g. PDF, DOCX, XLSX, CSV, PPTX, PNG), and cache the result locally.
    *   Use this instead of `google_drive_download_file` for files whose `mimeType` starts with `application/vnd.google-apps.*` — those have no raw binary content, so `google_drive_download_file` fails with `403 fileNotDownloadable` on them.
    *   Supported `export_format` values depend on the source file's type; an unsupported value returns an error listing the valid options for that file. See [Google's export format reference](https://developers.google.com/drive/api/guides/ref-export-formats) for the authoritative, up-to-date list:
        *   **Docs**: pdf, docx, odt, rtf, txt, html, epub, md
        *   **Sheets**: pdf, xlsx, ods, csv\*, tsv\*, html
        *   **Slides**: pdf, pptx, odp, txt, jpeg\*, png\*, svg\*
        *   **Drawings**: pdf, png, jpeg, svg
        *   **Apps Script**: json
        *   \* CSV/TSV (Sheets) and image exports (Slides/Drawings) only cover the first/active sheet or slide — the response includes `single_sheet_or_slide_only: true` as a reminder.
    *   Drive enforces a hard 10 MB limit on exported output; oversized exports return a clear error suggesting a smaller subset or a lower-fidelity format.
    *   The response also includes a `download_path` — see [Retrieving Cached/Exported Files](#3-retrieving-cachedexported-files) to actually fetch the bytes if your client doesn't share this server's filesystem.
    *   *Note: Unlike `google_drive_download_file`, exported bytes have no Drive-side checksum, so the cache is keyed on the source document's `modifiedTime` plus the requested format (`profiles/<profile_name>/cache/<file_id>.export.<format>`), not on `file_id` alone.*
*   `google_drive_create_folder(name: str, parent_id: str = None)`: Create a folder in Drive.
*   `google_drive_copy_file(file_id: str, name: str = None, parent_id: str = None)`: Copy a file in Drive.

### Google Docs
*   `google_docs_get_document(document_id: str)`: Read the plain text of a Doc.
*   `google_docs_create_document(title: str)`: Create a new Document.
*   `google_docs_append_text(document_id: str, text: str)`: Append text to a Doc.
*   `google_docs_format_text(document_id: str, text_to_format: str, bold: bool, italic: bool, underline: bool, font_size: float)`: Format specific text occurrences inside the document.
*   `google_docs_render_to_markdown(document_id: str)`: Read a Document and render its structural content to Markdown formatting.
*   `google_docs_create_from_markdown(title: str, markdown: str)`: Create a new Document from a Markdown string, translating formatting to Doc elements.

### Google Sheets
*   `google_sheets_get_spreadsheet(spreadsheet_id: str, range_name: str)`: Get values from a spreadsheet range.
*   `google_sheets_update_spreadsheet(spreadsheet_id: str, range_name: str, values: list[list[str]])`: Update values.
*   `google_sheets_create_spreadsheet(title: str)`: Create a new spreadsheet.

### Google Tasks
*   `google_tasks_list_task_lists()`: List all the user's task lists.
*   `google_tasks_list_tasks(tasklist_id: str)`: List tasks in a list.
*   `google_tasks_create_task(title: str, tasklist_id: str, notes: str, due: str)`: Create a task.
*   `google_tasks_complete_task(tasklist_id: str, task_id: str)`: Complete a task.
*   `google_tasks_create_task_list(title: str)`: Create a new task list.
*   `google_tasks_delete_task_list(tasklist_id: str)`: Delete a task list.
*   `google_tasks_update_task(tasklist_id: str, task_id: str, title: str, notes: str, due: str)`: Update task details.
*   `google_tasks_delete_task(tasklist_id: str, task_id: str)`: Delete a task.

### Google Maps
*   `google_maps_search(query: str)`: Search place details.
*   `google_maps_directions(origin: str, destination: str, mode: str)`: Fetch routing directions.
    *   *Requires `GOOGLE_MAPS_API_KEY` in the profile's `.env` or system environment variables.*

### Google News
*   `google_news_top_news(query: str)`: Fetch headline news (free RSS parser, no API key needed).
*   `google_news_get_article_text(url: str)`: Extract and read the full body text of a news article from its URL.
