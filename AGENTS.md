# Agent Development Guide (AGENTS.md)

This document is a guide for developer agents working on this repository to maintain, debug, and extend this Google Workspace MCP server.

---

## Architectural Blueprint

The codebase is written in Python using a hybrid architecture of the Anthropic `mcp` SDK and `Starlette`.

```
                +-----------------------------------------+
                |            Starlette (HTTP)             |
                +--------------------+--------------------+
                                     |
                 +-------------------+-------------------+
                 |                   |                   |
               /sse              /messages             /mcp
        (Validates token     (Routes JSON-RPC    (Streamable HTTP:
         maps connection)     via session_id)    validates token,
                 |                   |           manages sessions)
                 v                   v                   |
        +-----------------+ +-----------------+          |
        | sse.connect_sse | | handle_post_msg |          |
        +--------+--------+ +-----------------+          |
                 |                                       |
                 +-------------------+-------------------+
                                     |
                                     v
                        +-------------------------+
                        |  session_to_profile[ID] |
                        +------------+------------+
                                     |
                                     v
                        +-------------------------+
                        |    mcp._mcp_server      |
                        +-------------------------+
```

### 1. Multi-Tenant Session Routing
To maintain stateless multi-profile behavior, we map active connection streams/sessions to their corresponding profiles in memory:
- **HTTP+SSE**: When a client issues a `GET /sse?token=<profile_token>` request (or `Authorization: Bearer <profile_token>`), we authenticate the token against `.env` files in `profiles/*/`. Once authenticated, `SseServerTransport.connect_sse(...)` generates a UUID mapped as `session_id.hex` -> `profile_name` in `session_to_profile`.
- **Streamable HTTP**: When a client issues a `POST /mcp` initialization request (or `GET /mcp`), `ProfileStreamableHTTPSessionManager` validates the token, generates a session ID, and maps `session_id` -> `profile_name` in `session_to_profile`. Subsequent requests provide the `mcp-session-id` header.
- In `mcp_server.py`, tool handlers retrieve the current profile via `get_profile_name(ctx)`. `get_profile_name` extracts `session_id` from either the `mcp-session-id` header (Streamable HTTP) or query parameters (`session_id` in SSE), looks it up in `session_to_profile`, and loads the corresponding profile credentials.

### 2. File Caching & Temp Files
- As per security and isolation rules, all temporary files must be under `profiles/<profile_name>/`.
- The Drive download tool caches files at `profiles/<profile_name>/cache/<file_id>`.
- Next to each cached file, a JSON metadata file `profiles/<profile_name>/cache/<file_id>.json` stores the file name, size, mimeType, and MD5 checksum from Drive.
- Before downloading, we compare local cached metadata against live Drive metadata. If it matches, we return the cached file's path instantly.
- **Deliberate exception**: `google_drive_export_file` (Google-Apps-native file export, e.g. Doc -> PDF) caches at `profiles/<profile_name>/cache/<file_id>.export.<format>` (metadata sidecar: `...<format>.json`) instead of `<file_id>`, and keys its cache-validity check on the *source* document's `modifiedTime` instead of size/MD5. This is intentional, not an oversight — exported bytes have no Drive-side checksum/size to compare against, and the same source file can be exported to multiple formats, so `file_id` alone isn't a safe cache key. Do not "fix" this to match `google_drive_download_file`'s scheme.

### 3. Serving Cached Files Back to Clients (GM-4)
- All `@mcp.tool()` functions return plain text/JSON — there is no protocol-level way for a tool to return raw file bytes to an MCP client. `google_drive_download_file` / `google_drive_export_file` therefore only return a `local_path` on this server's own filesystem, which is meaningless to a client that doesn't share that filesystem/volume (e.g. a remote agent, or this server running in a different container than the caller).
- `GET /files/{cache_key}` (`mcp_server.py`, `handle_file_download`) closes that gap: it resolves `cache_key` to a file under the caller's `profiles/<profile_name>/cache/` (authenticated the same way as `/sse`/`/mcp` — `token` query param or `Authorization: Bearer`) and streams it back via `FileResponse`, using the metadata sidecar (`mimeType`/`export_mime_type`, `name`) for `Content-Type`/`Content-Disposition`.
- Both cache-writing tools include a `download_path` (`/files/<cache_key>`) in their JSON response specifically so callers know how to fetch the bytes — if you add another tool that caches a file, give it a `download_path` too rather than only a `local_path`.
- `resolve_cache_file_path(profile_name, cache_key)` is the only path from an HTTP-supplied `cache_key` to a filesystem path — it rejects anything that isn't a bare filename (no separators, no `..`) and double-checks the resolved real path is still inside the profile's cache dir. Do not bypass it or reimplement path joining for this route.

---

## How to Extend This Codebase

### 1. Adding a New Google API or Tool
All tool registrations are in `mcp_server.py` using `@mcp.tool()`.

To add a new tool:
1. Ensure the scope required for the API is listed in the `SCOPES` global array in both `mcp_server.py` and `cli.py`.
2. Define the tool function with `@mcp.tool()` and include `ctx: Context` as the first argument.
3. Call `profile_name = await get_profile_name(ctx)` to load the current profile context.
4. Retrieve the credentials using `creds = get_profile_credentials(profile_name)`.
5. Build the service with `service = build("<api_name>", "<version>", credentials=creds)`.
6. Run the operation, wrap it in a `try-except` block, and return a clean string or JSON dump.

#### Example:
```python
@mcp.tool()
async def google_calendar_list_events(ctx: Context, max_results: int = 5) -> str:
    """List upcoming events in the primary calendar.
    
    Args:
        max_results: Max events to list (default: 5).
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials(profile_name)
        calendar_service = build("calendar", "v3", credentials=creds)
        
        events_result = calendar_service.events().list(
            calendarId='primary', maxResults=max_results, singleEvents=True, orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        
        return json.dumps(events, indent=2)
    except Exception as e:
        logger.exception("Error listing calendar events")
        return f"Error: {str(e)}"
```

---

## Testing Guidelines

When modifying tool handlers, you can run the server locally and connect via STDIO for quick testing:
```bash
DEFAULT_PROFILE=stone python3 mcp_server.py
```
Setting `DEFAULT_PROFILE` allows tool calls that originate from STDIO (where HTTP context is missing) to fall back to the specified profile.

To test HTTP/SSE connectivity and token mapping:
1. Run the server locally: `PORT=8000 python3 mcp_server.py`
2. Connect to the SSE stream using `curl` or a test script:
   ```bash
   curl -N "http://localhost:8000/sse?token=<profile_token>"
   ```
3. Observe connections and session IDs being mapped in the server terminal logs.
